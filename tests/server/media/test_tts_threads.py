# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-1423a334416d7d51058da843


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import pyttsx3
import threading
import time

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-1423a334416d7d51058da843"


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
# from __debug_provenance_m__ import of
t2.start()
t2.join()

print("All done")
