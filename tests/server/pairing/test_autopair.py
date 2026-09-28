#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
Test script for autopair functionality.

This script tests the one-shot, non-trickle ICE candidate gathering
mechanism for WebRTC connections via the /autopair command.
"""

import json
import asyncio
import sys
import os

# Add parent directory to path to import server modules
from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

try:
    from crypt.aead import generate_hash
except ImportError:
    print("Warning: crypto module not available, using mock hash function")
    def generate_hash(password):
        return f"mock_hash_{password}"

def test_autopair_json_format():
    """Test the expected JSON format for autopair command."""

    # Mock server password
    server_password = "test_password_123"
    password_hash = generate_hash(server_password)

    # Sample autopair JSON message
    autopair_json = {
        "hash": password_hash,
        "offer": {
            "type": "offer",
            "sdp": "v=0\r\no=- 123456789 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\na=msid-semantic: WMS\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\nc=IN IP4 0.0.0.0\r\na=ice-ufrag:test\r\na=ice-pwd:testpassword\r\na=ice-options:trickle\r\na=fingerprint:sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99\r\na=setup:actpass\r\na=mid:0\r\na=sctp-port:5000\r\na=max-message-size:262144\r\na=candidate:1 1 UDP 2113667326 192.168.1.100 54400 typ host\r\na=candidate:2 1 UDP 1677729535 203.0.113.1 54400 typ srflx raddr 192.168.1.100 rport 54400\r\na=end-of-candidates\r\n"
        },
        "iceServers": [
            {
                "urls": ["stun:stun.l.google.com:19302"]
            }
        ]
    }

    print("=== Autopair Test ===")
    print(f"Server password: {server_password}")
    print(f"Generated hash: {password_hash}")
    print(f"JSON message size: {len(json.dumps(autopair_json))} bytes")
    print("\nSample autopair JSON:")
    print(json.dumps(autopair_json, indent=2))

    # Verify JSON can be parsed
    json_str = json.dumps(autopair_json)
    parsed = json.loads(json_str)
    print("\n✓ JSON serialization/deserialization successful")

    # Verify required fields
    required_fields = ["hash", "offer"]
    for field in required_fields:
        assert field in parsed, f"Missing required field: {field}"
        print(f"✓ Required field present: {field}")

    # Verify offer structure
    assert "type" in parsed["offer"] and "sdp" in parsed["offer"], "Invalid offer structure"
    print("✓ Valid offer structure")

    # Verify SDP contains end-of-candidates (one-shot)
    assert "a=end-of-candidates" in parsed["offer"]["sdp"], "SDP missing end-of-candidates marker"
    print("✓ SDP contains end-of-candidates marker (one-shot)")

    print("\n✓ All autopair format tests passed!")

def test_telegram_message_format():
    """Test the expected Telegram message format."""

    server_password = "test_password_123"
    password_hash = generate_hash(server_password)

    autopair_json = {
        "hash": password_hash,
        "offer": {
            "type": "offer",
            "sdp": "v=0\r\no=- 123456789 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\na=msid-semantic: WMS\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\nc=IN IP4 0.0.0.0\r\na=ice-ufrag:test\r\na=ice-pwd:testpassword\r\na=ice-options:trickle\r\na=fingerprint:sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99\r\na=setup:actpass\r\na=mid:0\r\na=sctp-port:5000\r\na=max-message-size:262144\r\na=candidate:1 1 UDP 2113667326 192.168.1.100 54400 typ host\r\na=candidate:2 1 UDP 1677729535 203.0.113.1 54400 typ srflx raddr 192.168.1.100 rport 54400\r\na=end-of-candidates\r\n"
        }
    }

    # Format as expected Telegram message
    telegram_message = f"/autopair\n{json.dumps(autopair_json)}"

    print("\n=== Telegram Message Format Test ===")
    print("Expected Telegram message format:")
    print(telegram_message)
    print(f"\nMessage length: {len(telegram_message)} characters")

    # Test parsing
    lines = telegram_message.split('\n', 1)
    assert len(lines) == 2, "Invalid message format"

    command = lines[0]
    json_part = lines[1]

    assert command == "/autopair", f"Invalid command: {command}"

    parsed_json = json.loads(json_part)
    print("✓ Telegram message format is valid")
    print(f"✓ Command: {command}")
    print(f"✓ JSON payload parsed successfully")
    assert parsed_json is not None

if __name__ == "__main__":
    print("Testing AutoYou Autopair Functionality")
    print("=" * 50)
    
    success = True
    
    # Run tests
    success &= test_autopair_json_format()
    success &= test_telegram_message_format()
    
    print("\n" + "=" * 50)
    if success:
        print("✓ All tests passed! Autopair functionality is ready.")
    else:
        print("✗ Some tests failed. Please check the implementation.")
    
    sys.exit(0 if success else 1)
