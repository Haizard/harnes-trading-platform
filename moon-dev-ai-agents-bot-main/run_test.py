"""Minimal test to verify container works"""
import sys
import os
import time

print("[TEST] Python " + sys.version)
print("[TEST] CWD: " + os.getcwd())
print("[TEST] Files: " + str(os.listdir(".")))
print("[TEST] Starting infinite loop...")

# Just keep running
while True:
    print("[TEST] Running... " + str(time.time()))
    time.sleep(10)
