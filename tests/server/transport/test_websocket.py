# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-23aa4c98041d18bbe745865e


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import json
import os
import time

import pytest
import websockets

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-23aa4c98041d18bbe745865e"
# from __debug_provenance_c__ import subtask


if os.environ.get("AUTOYOU_RUN_LIVE_WHATSAPP_WS_TESTS") != "1":
    pytest.skip(
        "Manual WhatsApp websocket smoke test. Set AUTOYOU_RUN_LIVE_WHATSAPP_WS_TESTS=1 to run.",
        allow_module_level=True,
    )

async def test_parallel_websocket():
    """Test parallel WebSocket connection to diagnose message reception issues."""
    try:
        print("Attempting to connect to WhatsApp WebSocket...")
        ws = await websockets.connect('ws://localhost:8083')
        print('✓ Connected to WebSocket successfully!')
        
        # Send a status request to test bidirectional communication
        status_request = {
            "action": "get_status",
            "data": {}
        }
        await ws.send(json.dumps(status_request))
        print("✓ Sent status request")
        
        # Listen for messages for 30 seconds
        print("Listening for messages for 30 seconds...")
        print("Please send a WhatsApp message to yourself now to test message reception...")
        
        start_time = time.time()
        message_count = 0
        
        try:
            while time.time() - start_time < 30:
                # Wait for message with timeout
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    message_count += 1
                    
                    try:
                        data = json.loads(message)
                        event = data.get('event', 'unknown')
                        print(f"✓ Received message #{message_count}: event='{event}'")
                        
                        if event == 'message':
                            print(f"  📱 WhatsApp message detected: {data.get('data', {}).get('body', '')[:50]}...")
                        elif event == 'status_response':
                            print(f"  📊 Status response: {data.get('data', {})}")
                        elif event == 'qr':
                            print(f"  📱 QR code event received")
                        else:
                            print(f"  📡 Other event: {data}")
                            
                    except json.JSONDecodeError:
                        print(f"  ⚠️  Non-JSON message: {message}")
                        
                except asyncio.TimeoutError:
                    # No message received in 1 second, continue waiting
                    continue
                    
        except KeyboardInterrupt:
            print("\n⏹️  Test interrupted by user")
            
        print(f"\n📊 Test Summary:")
        print(f"   - Total messages received: {message_count}")
        print(f"   - Test duration: {time.time() - start_time:.1f} seconds")
        
        if message_count == 0:
            print("   ❌ No messages received - possible connection issue")
        else:
            print("   ✓ WebSocket is receiving messages")
            
        # Keep connection open for manual testing
        print("\n🔄 Keeping connection open for manual testing...")
        print("   Press Ctrl+C to close connection")
        
        try:
            while True:
                message = await ws.recv()
                try:
                    data = json.loads(message)
                    event = data.get('event', 'unknown')
                    print(f"📨 Live message: event='{event}'")
                    if event == 'message':
                        body = data.get('data', {}).get('body', '')
                        print(f"   📱 WhatsApp: {body[:100]}...")
                except json.JSONDecodeError:
                    print(f"📨 Live non-JSON: {message}")
                    
        except KeyboardInterrupt:
            print("\n⏹️  Closing connection...")
            
        # Note: We intentionally don't close the connection here to keep it open
        # await ws.close()
        print('Connection will remain open for testing...')
        
    except Exception as e:
        print(f'❌ Failed to connect or test: {e}')
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_parallel_websocket())
