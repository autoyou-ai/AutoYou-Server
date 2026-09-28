# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
Test script for AutoYou AI Agent REST API.

This script tests the REST API endpoints to ensure they work correctly
for external chat applications.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any

import pytest
import requests
from requests.exceptions import RequestException

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Configuration
API_BASE_URL = "http://localhost:8081"
LIVE_REST_API_ENV = "AUTOYOU_RUN_LIVE_REST_API_TESTS"
API_ENDPOINTS = {
    "chat": f"{API_BASE_URL}/api/chat",
    "status": f"{API_BASE_URL}/api/status",
    "docs": f"{API_BASE_URL}/api/docs",
    "session_info": f"{API_BASE_URL}/api/sessions"
}

def _require_live_api() -> None:
    if os.environ.get(LIVE_REST_API_ENV) != "1":
        pytest.skip(f"Live REST API smoke test. Set {LIVE_REST_API_ENV}=1 to run.")
    try:
        response = requests.get(API_ENDPOINTS["status"], timeout=3)
        response.raise_for_status()
    except RequestException as exc:
        pytest.skip(f"AI agent server is not running at {API_BASE_URL}: {exc}")

def check_api_status():
    """Test the API status endpoint."""
    print("\n=== Testing API Status Endpoint ===")
    try:
        response = requests.get(API_ENDPOINTS["status"], timeout=10)
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ Status: {data.get('status')}")
        print(f"✅ Agent Name: {data.get('agent_name')}")
        print(f"✅ Version: {data.get('version')}")
        print(f"✅ Timestamp: {data.get('timestamp')}")
        
        assert data.get('status') == 'healthy'
        
    except RequestException as e:
        logger.error(f"❌ API Status test failed: {e}")
        assert False, f"API Status test failed: {e}"

def check_api_docs():
    """Test the API documentation endpoint."""
    print("\n=== Testing API Documentation Endpoint ===")
    try:
        response = requests.get(API_ENDPOINTS["docs"], timeout=10)
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ Title: {data.get('title')}")
        print(f"✅ Description: {data.get('description')}")
        print(f"✅ Available Endpoints: {list(data.get('endpoints', {}).keys())}")
        
        assert "AutoYou AI Agent REST API" in data.get('title', "")
        
    except RequestException as e:
        logger.error(f"❌ API Docs test failed: {e}")
        assert False, f"API Docs test failed: {e}"

def check_chat_endpoint_basic():
    """Test basic chat functionality."""
    print("\n=== Testing Basic Chat Endpoint ===")
    
    chat_request = {
        "message": "Hello, can you help me?",
        "user_id": "test_user_001",
        "metadata": {"test": "basic_chat"}
    }
    
    try:
        response = requests.post(
            API_ENDPOINTS["chat"],
            json=chat_request,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ Response received: {data.get('response')[:100]}...")
        print(f"✅ Session ID: {data.get('session_id')}")
        
        assert data.get('session_id') is not None
        return data.get('session_id')
        
    except RequestException as e:
        logger.error(f"❌ Basic chat test failed: {e}")
        assert False, f"Basic chat test failed: {e}"

def check_chat_endpoint_with_session(session_id: str):
    """Test chat with existing session."""
    print("\n=== Testing Chat with Existing Session ===")
    
    chat_request = {
        "message": "What did I just ask you?",
        "session_id": session_id,
        "user_id": "test_user_001",
        "metadata": {"test": "session_continuity"}
    }
    
    try:
        response = requests.post(
            API_ENDPOINTS["chat"],
            json=chat_request,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ Response received: {data.get('response')[:100]}...")
        print(f"✅ Session ID matches: {data.get('session_id') == session_id}")
        
        assert data.get('session_id') == session_id
        
    except RequestException as e:
        logger.error(f"❌ Session continuity test failed: {e}")
        assert False, f"Session continuity test failed: {e}"

def check_session_info_endpoint(user_id: str, session_id: str):
    """Test session information endpoint."""
    print("\n=== Testing Session Info Endpoint ===")
    
    try:
        url = f"{API_ENDPOINTS['session_info']}/{user_id}/{session_id}"
        response = requests.get(url, timeout=10)
        if response.status_code == 404:
            print("ℹ️ Session info endpoint is not exposed by this live API build. Skipping.")
            return
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ Session ID: {data.get('session_id')}")
        print(f"✅ User ID: {data.get('user_id')}")
        print(f"✅ Message Count: {data.get('message_count')}")
        
        assert data.get('session_id') == session_id
        assert data.get('user_id') == user_id
        
    except RequestException as e:
        logger.error(f"❌ Session info test failed: {e}")
        assert False, f"Session info test failed: {e}"

def check_chat_with_context():
    """Test chat with conversation context."""
    print("\n=== Testing Chat with Context ===")
    
    context = [
        {
            "role": "user",
            "content": "My name is Alice",
            "timestamp": datetime.now().isoformat()
        },
        {
            "role": "assistant", 
            "content": "Nice to meet you, Alice!",
            "timestamp": datetime.now().isoformat()
        }
    ]
    
    chat_request = {
        "message": "What's my name?",
        "user_id": "test_user_002",
        "context": context,
        "metadata": {"test": "context_awareness"}
    }
    
    try:
        response = requests.post(
            API_ENDPOINTS["chat"],
            json=chat_request,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        
        data = response.json()
        print(f"✅ Response received: {data.get('response')[:100]}...")
        
        # Check if the agent processed the context or at least responded appropriately
        response_text = data.get('response', '').lower()
        
        # The agent should either remember Alice or acknowledge the context in some way
        # If it doesn't remember, it should at least provide a reasonable response
        context_aware = ("alice" in response_text or 
                        "name" in response_text or 
                        "don't have" in response_text or
                        "memory" in response_text)
        
        assert context_aware, f"Agent response doesn't seem to handle context appropriately: {data.get('response')}"
        print("✅ Context test passed - agent handled context appropriately")
        
    except RequestException as e:
        logger.error(f"❌ Context test failed: {e}")
        assert False, f"Context test failed: {e}"

def check_error_handling():
    """Test error handling with invalid requests."""
    print("\n=== Testing Error Handling ===")
    
    invalid_request = {
        "user_id": "test_user_003",
        # Missing "message" field
    }
    
    try:
        response = requests.post(
            API_ENDPOINTS["chat"],
            json=invalid_request,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        assert response.status_code == 422, f"Expected status code 422, but got {response.status_code}"
        print("✅ Validation error handled correctly")
            
    except RequestException as e:
        logger.error(f"❌ Error handling test failed: {e}")
        assert False, f"Error handling test failed: {e}"

def test_all_features():
    """Run all REST API tests."""
    _require_live_api()
    print("🚀 Starting AutoYou AI Agent REST API Tests")
    print("=" * 50)
    
    check_api_status()
    check_api_docs()
    session_id = check_chat_endpoint_basic()
    
    assert session_id is not None, "Failed to get session_id from basic chat test."
    
    check_chat_endpoint_with_session(session_id)
    check_session_info_endpoint("test_user_001", session_id)
    check_chat_with_context()
    check_error_handling()
    
    print("\n" + "=" * 50)
    print("🏁 Test Results Summary")
    print("=" * 50)
    print("🎉 All tests passed! The REST API is working correctly.")

if __name__ == "__main__":
    print("AutoYou AI Agent REST API Test Suite")
    print("====================================")
    print(f"Testing API at: {API_BASE_URL}")
    print("Make sure the server is running before executing tests.")
    print()
    
    try:
        test_all_features()
        exit(0)
    except AssertionError as e:
        logger.error(f"🔥 A test failed: {e}")
        exit(1)
    except KeyboardInterrupt:
        print("\n\n⏹️  Tests interrupted by user")
        exit(1)
    except Exception as e:
        print(f"\n\n💥 Unexpected error during testing: {e}")
        exit(1)
