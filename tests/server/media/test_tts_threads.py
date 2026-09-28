# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

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
