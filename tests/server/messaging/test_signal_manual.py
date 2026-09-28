# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
Manual test script to debug Signal service startup
"""

import asyncio
import logging
import os

import pytest

from signal_service import SignalService

if os.environ.get("AUTOYOU_RUN_LIVE_SIGNAL_TESTS") != "1":
    pytest.skip(
        "Manual Signal service startup smoke test. Set AUTOYOU_RUN_LIVE_SIGNAL_TESTS=1 to run.",
        allow_module_level=True,
    )

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def test_signal_startup():
    """Test Signal service startup manually."""
    
    logger.info("Creating Signal service instance...")
    signal_service = SignalService(port=8082, device_name="test-signal")
    
    logger.info("Starting Signal service...")
    success = await signal_service.start()

    if success:
        logger.info("✅ Signal service started successfully!")
        
        # Test status
        status = await signal_service.get_status()
        logger.info(f"Status: {status}")
        
        # Test QR code generation
        qr_code = await signal_service.get_qr_code_link()
        if qr_code:
            logger.info(f"✅ QR code generated: {qr_code}")
        else:
            logger.warning("❌ Failed to generate QR code")
            
    else:
        logger.error("❌ Failed to start Signal service")
    
    # Cleanup
    logger.info("Stopping Signal service...")
    await signal_service.stop()
    logger.info("Test completed")

if __name__ == "__main__":
    asyncio.run(test_signal_startup())
