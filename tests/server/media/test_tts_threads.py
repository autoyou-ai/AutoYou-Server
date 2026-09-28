# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-726c79207375627461736b20-107b95762c7c3c3404af9bcf


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-726c79207375627461736b20-107b95762c7c3c3404af9bcf"

import pyttsx3
import threading
import time

def speak(text):
    print(f"Starting thread for: {text}")
    try:
        engine = pyttsx3.init()
        engine.save_to_file(text, "test.wav")
        engine.runAndWait()
        print(f"Finished thread for: {text}")
    except Exception as e:
        print(f"Error in thread: {e}")

t1 = threading.Thread(target=speak, args=("first",))
t1.start()
t1.join()

print("First thread done, waiting 1 sec")
time.sleep(1)

t2 = threading.Thread(target=speak, args=("second",))
t2.start()
t2.join()

print("All done")
