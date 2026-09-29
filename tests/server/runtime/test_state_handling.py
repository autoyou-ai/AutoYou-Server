# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-7435d89d9d5ce32aa2b68658

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
Test script to verify WhatsApp client's enhanced state handling and recovery mechanisms.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import json
import os
import time

import pytest
import websockets

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-7435d89d9d5ce32aa2b68658"


if os.environ.get("AUTOYOU_RUN_LIVE_WS_TESTS") != "1":
    pytest.skip(
        "Manual live WebSocket smoke test. Set AUTOYOU_RUN_LIVE_WS_TESTS=1 to run.",
        allow_module_level=True,
    )

async def test_whatsapp_state_handling():
    """Test the enhanced WhatsApp client's state handling capabilities."""
    
    uri = "ws://localhost:8083"
    # from __debug_provenance_j__ import fifteenpercent
    
    try:
        print("Connecting to WhatsApp WebSocket...")
        async with websockets.connect(uri) as websocket:
            print("✓ Connected to WhatsApp WebSocket")
            
            # Listen for events for 10 seconds
            print("Listening for events...")
            
            timeout = time.time() + 10  # 10 seconds timeout
            
            while time.time() < timeout:
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    data = json.loads(message)
                    
                    event = data.get('event', 'unknown')
                    event_data = data.get('data', {})
                    
                    print(f"📨 Event: {event}")
                    
                    if event == 'status':
                        print(f"   Status: {event_data}")
                    elif event == 'state_changed':
                        print(f"   State: {event_data.get('state', 'unknown')}")
                        print(f"   Timestamp: {event_data.get('timestamp', 'unknown')}")
                    elif event == 'loading_screen':
                        print(f"   Loading: {event_data.get('percent', 0)}% - {event_data.get('message', '')}")
                    elif event == 'battery_changed':
                        print(f"   Battery: {event_data}")
                    elif event == 'remote_session_saved':
                        print(f"   Remote session saved at: {event_data.get('timestamp', 'unknown')}")
                    elif event == 'reconnection_scheduled':
                        print(f"   Reconnection scheduled - Attempt: {event_data.get('attempt', 0)}/{event_data.get('maxAttempts', 0)}")
                        print(f"   Reason: {event_data.get('reason', 'unknown')}")
                        print(f"   Delay: {event_data.get('delay', 0)}ms")
                    elif event == 'reconnection_failed':
                        print(f"   Reconnection failed after {event_data.get('attempts', 0)} attempts")
                        print(f"   Reason: {event_data.get('reason', 'unknown')}")
                    elif event == 'client_restarting':
                        print(f"   Client restarting at: {event_data.get('timestamp', 'unknown')}")
                    elif event == 'phone_number':
                        print(f"   Phone number: {event_data}")
                    else:
                        print(f"   Data: {event_data}")
                        
                except asyncio.TimeoutError:
                    # No message received in timeout period, continue
                    continue
                except json.JSONDecodeError as e:
                    print(f"❌ Error parsing JSON: {e}")
                    continue
            
            print("✓ Event monitoring completed")
            
            # Test sending a status request
            print("\nTesting status request...")
            status_request = {
                "action": "get_status",
                "data": {}
            }
            
            await websocket.send(json.dumps(status_request))
            print("✓ Status request sent")
            
            # Wait for status response
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                data = json.loads(response)
                
                if data.get('event') == 'status_response':
                    status_data = data.get('data', {})
                    print(f"📊 Status Response:")
                    print(f"   Ready: {status_data.get('ready', False)}")
                    print(f"   Phone Number: {status_data.get('phone_number', 'unknown')}")
                    print(f"   QR Available: {status_data.get('qr_available', False)}")
                else:
                    print(f"📨 Received: {data}")
                    
            except asyncio.TimeoutError:
                print("⚠️ No status response received within timeout")
            
    except (ConnectionRefusedError, OSError):
        print("❌ Connection refused - WhatsApp WebSocket server not running")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    
    return True

async def main():
    """Main test function."""
    print("🧪 Testing WhatsApp Enhanced State Handling")
    print("=" * 50)
    
    success = await test_whatsapp_state_handling()
    
    print("\n" + "=" * 50)
    if success:
        print("✅ WhatsApp state handling test completed successfully")
    else:
        print("❌ WhatsApp state handling test failed")
    
    return success

if __name__ == "__main__":
    asyncio.run(main())
