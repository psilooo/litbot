# lithood/retry.py
"""Retry utilities with exponential backoff for network resilience."""

import asyncio
import functools
from typing import TypeVar, Callable, Any, Optional, Tuple, Type
from lithood.logger import log

T = TypeVar('T')

# Transient errors that should trigger retries
TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,  # Includes network-related OS errors
)


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_retries: int = 5,
        initial_delay: float = 1.0,
        max_delay: float = 120.0,  # 2 minutes max
        exponential_base: float = 2.0,
        jitter: float = 0.1,  # 10% random jitter
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


# Default configs for different scenarios
RETRY_FAST = RetryConfig(max_retries=3, initial_delay=0.5, max_delay=5.0)
RETRY_STANDARD = RetryConfig(max_retries=5, initial_delay=1.0, max_delay=30.0)
RETRY_PERSISTENT = RetryConfig(max_retries=20, initial_delay=2.0, max_delay=120.0)


def calculate_delay(attempt: int, config: RetryConfig) -> float:
    """Calculate delay for a given attempt with exponential backoff and jitter."""
    import random

    delay = config.initial_delay * (config.exponential_base ** attempt)
    delay = min(delay, config.max_delay)

    # Add jitter (±jitter%)
    jitter_range = delay * config.jitter
    delay += random.uniform(-jitter_range, jitter_range)

    return max(0.1, delay)  # Minimum 100ms


def is_transient_error(exc: Exception) -> bool:
    """Check if an exception is likely transient (worth retrying)."""
    # Check direct type
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        return True

    # Check error message for common transient patterns
    error_msg = str(exc).lower()
    transient_patterns = [
        'timeout', 'timed out', 'connection', 'network',
        'temporarily unavailable', '503', '502', '504',
        'rate limit', 'too many requests', '429',
        'eof', 'broken pipe', 'reset by peer',
    ]
    return any(pattern in error_msg for pattern in transient_patterns)


async def retry_async(
    func: Callable[..., Any],
    *args,
    config: RetryConfig = RETRY_STANDARD,
    operation_name: str = "operation",
    **kwargs,
) -> Tuple[Any, Optional[Exception]]:
    """
    Execute an async function with retry logic.

    Returns:
        Tuple of (result, None) on success, or (None, last_exception) on failure.
    """
    last_exception = None

    for attempt in range(config.max_retries + 1):
        try:
            result = await func(*args, **kwargs)
            if attempt > 0:
                log.info(f"{operation_name} succeeded after {attempt + 1} attempts")
            return result, None

        except Exception as e:
            last_exception = e

            # Don't retry non-transient errors
            if not is_transient_error(e):
                log.error(f"{operation_name} failed with non-transient error: {e}")
                return None, e

            # Last attempt failed
            if attempt >= config.max_retries:
                log.error(f"{operation_name} failed after {attempt + 1} attempts: {e}")
                return None, e

            # Calculate delay and wait
            delay = calculate_delay(attempt, config)
            log.warning(f"{operation_name} failed (attempt {attempt + 1}/{config.max_retries + 1}): {e}")
            log.warning(f"  Retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)

    return None, last_exception


def with_retry(
    config: RetryConfig = RETRY_STANDARD,
    operation_name: Optional[str] = None,
):
    """
    Decorator to add retry logic to async functions.

    Usage:
        @with_retry(config=RETRY_STANDARD, operation_name="place order")
        async def place_order(...):
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            name = operation_name or func.__name__
            result, error = await retry_async(
                func, *args, config=config, operation_name=name, **kwargs
            )
            if error:
                # Re-raise if all retries failed (let caller handle)
                raise error
            return result
        return wrapper
    return decorator


class ConnectionMonitor:
    """Monitors connection health and handles reconnection."""

    CONNECTION_FAILURE_THRESHOLD = 3

    def __init__(self, reconnect_callback: Callable):
        self.reconnect_callback = reconnect_callback
        self.is_connected = False
        self.consecutive_failures = 0
        self.last_success_time = None
        self._reconnecting = False

    def record_success(self):
        """Record a successful operation."""
        import time
        self.is_connected = True
        self.consecutive_failures = 0
        self.last_success_time = time.time()

    def record_failure(self):
        """Record a failed operation."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.CONNECTION_FAILURE_THRESHOLD:
            self.is_connected = False

    async def ensure_connected(self, timeout: float = 60.0) -> bool:
        """
        Ensure connection is healthy, reconnect if needed.

        Args:
            timeout: Maximum seconds to wait if another coroutine is reconnecting.
        """
        import time

        if self.is_connected and self.consecutive_failures < self.CONNECTION_FAILURE_THRESHOLD:
            return True

        if self._reconnecting:
            # Another coroutine is already reconnecting - wait with timeout
            start = time.time()
            while self._reconnecting:
                if time.time() - start > timeout:
                    log.error("Timeout waiting for reconnection")
                    return False
                await asyncio.sleep(0.5)
            return self.is_connected

        self._reconnecting = True
        try:
            log.warning("Connection appears unhealthy, attempting reconnection...")

            config = RETRY_PERSISTENT
            for attempt in range(config.max_retries + 1):
                try:
                    await self.reconnect_callback()
                    self.is_connected = True
                    self.consecutive_failures = 0
                    log.info("Reconnection successful")
                    return True
                except Exception as e:
                    if attempt >= config.max_retries:
                        log.error(f"Reconnection failed after {attempt + 1} attempts: {e}")
                        return False

                    delay = calculate_delay(attempt, config)
                    log.warning(f"Reconnection attempt {attempt + 1} failed: {e}")
                    log.warning(f"  Retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)

            return False
        finally:
            self._reconnecting = False
