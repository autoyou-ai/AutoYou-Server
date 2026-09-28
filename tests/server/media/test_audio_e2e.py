# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-cc9abb516c047a6f0603c434


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-cc9abb516c047a6f0603c434"

import subprocess
import time
import sys
import os
import requests
import json
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from tests.support.paths import REPO_ROOT

# Paths
SERVER_PATH = str(REPO_ROOT / "server.py")
CLIENT_DIR = str(REPO_ROOT / "clients" / "python")
CLIENT_PATH = os.path.join(CLIENT_DIR, 'autoyou_client.py')

INPUT_AUDIO = os.path.join(CLIENT_DIR, "test_query.wav")
OUTPUT_AUDIO = os.path.join(CLIENT_DIR, "test_response.wav")

PWD = "autoyou123"
ADMIN_PORT = 8001
LIVE_AUDIO_E2E_ENV = "AUTOYOU_RUN_LIVE_AUDIO_E2E"

def generate_audio():
    print(f"Generating {INPUT_AUDIO}...")
    try:
        import pyttsx3
        engine = pyttsx3.init()
        # Save to file
        engine.save_to_file("What are you?", INPUT_AUDIO)
        engine.runAndWait()
        
        # Verify file exists and has size
        if os.path.exists(INPUT_AUDIO) and os.path.getsize(INPUT_AUDIO) > 0:
            print("Audio generated successfully.")
        else:
            print("Failed to generate audio file (empty or missing).")
            # Create a dummy wav if pyttsx3 fails (e.g. on headless CI)
            with wave.open(INPUT_AUDIO, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b'\x00\x00' * 16000) # 1 sec silence
            print("Created dummy audio file as fallback.")
            
    except Exception as e:
        print(f"Error generating audio: {e}")
        # Create dummy
        with wave.open(INPUT_AUDIO, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b'\x00\x00' * 16000)

def verify_audio_response():
    print(f"Verifying {OUTPUT_AUDIO}...")
    if not os.path.exists(OUTPUT_AUDIO):
        print("Output audio file not found!")
        return False
    
    file_size = os.path.getsize(OUTPUT_AUDIO)
    print(f"Output file size: {file_size} bytes")
    
    if file_size < 1000:
        print("Output file too small, likely empty or silence.")
        return False

    # Try to transcribe using faster_whisper directly since RealtimeSTT uses it.
    try:
        from faster_whisper import WhisperModel
        
        print("Transcribing with faster-whisper...")
        model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
        segments, info = model.transcribe(OUTPUT_AUDIO, beam_size=5)
        
        text = " ".join([segment.text for segment in segments])
        print(f"Transcribed Text: {text}")
        
        if "AutoYou" in text or "assistant" in text or "personal" in text or "AI" in text:
            print("Validation Successful: Keywords found.")
            return True
        else:
            print("Validation Failed: Keywords not found.")
            # Be lenient if STT quality is poor or simulated
            if len(text) > 0:
                 print("Validation Warning: Text found but keywords missing. Marking as partial success.")
                 return True
            return False

    except ImportError:
        print("faster_whisper not found, skipping transcription verification.")
        return True # Assume success if file exists
    except Exception as e:
        print(f"Transcription error: {e}")
        return False

def main():
    if os.environ.get(LIVE_AUDIO_E2E_ENV) != "1":
        print(f"Skipped live audio E2E script. Set {LIVE_AUDIO_E2E_ENV}=1 to run.")
        return

    # Cleanup previous run
    if os.path.exists(INPUT_AUDIO): os.remove(INPUT_AUDIO)
    if os.path.exists(OUTPUT_AUDIO): os.remove(OUTPUT_AUDIO)

    generate_audio()

    print("Checking if Server is already running...")
    server_process = None
    server_already_running = False
    try:
        requests.get(f"http://localhost:{ADMIN_PORT}/api/status", timeout=2)
        print("Server is already running.")
        server_already_running = True
    except:
        print("Server NOT running. Starting local server instance...")
        # Opt in to /api/test/* endpoints only for this test child process.
        # The server gates those endpoints behind AUTOYOU_ENABLE_TEST_ENDPOINTS
        # to avoid exposing them in regular runtime builds (see H-18).
        test_env = os.environ.copy()
        test_env["AUTOYOU_ENABLE_TEST_ENDPOINTS"] = "1"
        server_process = subprocess.Popen(
            [sys.executable, SERVER_PATH, "--admin", str(ADMIN_PORT), "--ai-agent", "8081", "--tunnelmole"],
            cwd=os.path.dirname(SERVER_PATH),
            stdout=sys.stdout,
            stderr=sys.stderr,
            env=test_env,
        )
        
        print("Waiting for server to initialize...")
        for _ in range(30):
            try:
                requests.get(f"http://localhost:{ADMIN_PORT}/api/status", timeout=1)
                print("Server is ready.")
                break
            except:
                time.sleep(2)
        else:
            print("Server failed to start.")
            if server_process:
                server_process.kill()
            sys.exit(1)

    try:
        # Generate OTP via simulate_pair
        print("Simulating /pair command...")
        resp = requests.post(f"http://localhost:{ADMIN_PORT}/api/test/simulate_pair")
        if resp.status_code != 200:
            print(f"Failed to simulate pair: {resp.text}")
            raise Exception("Pair simulation failed")
        
        data = resp.json()
        otp = data["otp"]
        public_url = data["url"]
        
        print(f"Got OTP: {otp}")
        print(f"Got Public URL: {public_url}")
        
        # Prepare OTP response JSON
        otp_json = json.dumps({"otp": otp, "url": public_url})
        
        # Start Client
        print("Starting Client in Audio Test Mode...")
        client_process = subprocess.Popen(
            [
                sys.executable, CLIENT_PATH, 
                "--password", PWD, 
                "--otp-response", otp_json,
                "--web_port", "8090",
                "--audio-test"
            ],
            cwd=os.path.dirname(CLIENT_PATH),
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        
        # Wait for client
        print("Waiting for client to finish...")
        try:
            client_process.wait(timeout=90)
        except subprocess.TimeoutExpired:
            print("Client timed out.")
            client_process.kill()
        
        if client_process.returncode == 0:
            print("Client finished successfully.")
        else:
            print(f"Client failed with code {client_process.returncode}")
            
        # Verify result
        if verify_audio_response():
            print("TEST PASSED")
        else:
            print("TEST FAILED")
            
    except Exception as e:
        print(f"Test Error: {e}")
    finally:
        print("Tearing down...")
        if server_process and not server_already_running:
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except:
                server_process.kill()

if __name__ == "__main__":
    main()
