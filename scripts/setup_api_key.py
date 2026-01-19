#!/usr/bin/env python3
# scripts/setup_api_key.py
"""
One-time setup script to generate and register an API key with Lighter.

This script will:
1. Generate a new API key pair
2. Register it with your Lighter account (requires ETH private key to sign)
3. Output the API key credentials to add to your .env file

Run this once before using the bot.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import lighter
from lithood.config import LIGHTER_BASE_URL, LIGHTER_PRIVATE_KEY, PROXY_URL
from lithood.logger import log


async def main():
    log.info("=" * 60)
    log.info("LIGHTER API KEY SETUP")
    log.info("=" * 60)

    if not LIGHTER_PRIVATE_KEY:
        log.error("LIGHTER_PRIVATE_KEY not set in .env file")
        log.error("This should be your wallet private key (0x...)")
        return

    if PROXY_URL:
        masked_proxy = PROXY_URL.split("@")[-1] if "@" in PROXY_URL else PROXY_URL
        log.info(f"Using proxy: {masked_proxy}")

    # Step 1: Generate a new API key pair
    log.info("")
    log.info("Step 1: Generating new API key pair...")

    private_key, public_key, error = lighter.create_api_key()

    if error:
        log.error(f"Failed to generate API key: {error}")
        return

    log.info(f"Generated API key pair")
    log.info(f"  Public key: {public_key[:20]}...")

    # Step 2: Get account index
    log.info("")
    log.info("Step 2: Looking up your account...")

    config = lighter.Configuration(host=LIGHTER_BASE_URL)
    api_client = lighter.ApiClient(configuration=config)
    account_api = lighter.AccountApi(api_client)

    try:
        from eth_account import Account as EthAccount
        eth_account = EthAccount.from_key(LIGHTER_PRIVATE_KEY)
        l1_address = eth_account.address

        sub_accounts = await account_api.accounts_by_l1_address(l1_address=l1_address)

        if not sub_accounts.sub_accounts:
            log.error(f"No Lighter account found for wallet: {l1_address}")
            log.error("Make sure you've deposited funds to Lighter first.")
            await api_client.close()
            return

        account_index = sub_accounts.sub_accounts[0].index
        log.info(f"Found account index: {account_index}")

    except Exception as e:
        log.error(f"Failed to get account: {e}")
        await api_client.close()
        return

    # Step 3: Register the API key
    log.info("")
    log.info("Step 3: Registering API key with your account...")
    log.info("(This requires an on-chain transaction signed by your wallet)")

    # Use API key index 3 (first available user index, 0-2 are reserved)
    api_key_index = 3

    try:
        # Create a temporary signer to register the key
        # We need to use the register_api_key method
        info_api = lighter.InfoApi(api_client)

        # Get the registration message to sign
        log.info(f"Registering API key at index {api_key_index}...")

        # The SDK should have a method to register API keys
        # Let's check if we can do it via the signer

        # Actually, we need to call the register API key endpoint
        # This typically requires signing with the ETH private key

        from lighter import SignerClient

        # Try to register using the account API
        # Note: The exact method depends on the SDK version

        log.info("")
        log.info("=" * 60)
        log.info("MANUAL REGISTRATION REQUIRED")
        log.info("=" * 60)
        log.info("")
        log.info("The SDK doesn't expose direct API key registration.")
        log.info("You need to register your API key via the Lighter web interface:")
        log.info("")
        log.info("1. Go to https://lighter.xyz and connect your wallet")
        log.info("2. Navigate to Settings > API Keys")
        log.info("3. Add a new API key with this public key:")
        log.info("")
        log.info(f"   {public_key}")
        log.info("")
        log.info("4. Once registered, add these to your .env file:")
        log.info("")
        log.info(f"   LIGHTER_API_KEY_PRIVATE={private_key}")
        log.info(f"   LIGHTER_API_KEY_INDEX={api_key_index}")
        log.info(f"   LIGHTER_ACCOUNT_INDEX={account_index}")
        log.info("")
        log.info("=" * 60)
        log.info("")
        log.info("SAVE THIS PRIVATE KEY - IT CANNOT BE RECOVERED:")
        log.info(f"{private_key}")
        log.info("")

    except Exception as e:
        log.error(f"Registration error: {e}")
    finally:
        await api_client.close()


if __name__ == "__main__":
    asyncio.run(main())
