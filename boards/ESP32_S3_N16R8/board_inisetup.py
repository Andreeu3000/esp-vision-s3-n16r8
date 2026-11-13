# SPDX-License-Identifier: Apache-2.0

MAIN_PY = """\
import time

print("ESP-VISION ESP32-S3 N16R8 ready")

while True:
    time.sleep_ms(1000)
"""

README_TXT = """\
ESP-VISION ESP32-S3 N16R8

ESP32-S3 at 240 MHz
16 MB DIO Flash
8 MB Octal PSRAM

Image processing from local files.
Flash filesystem mounted at /flash.

Edit main.py to run your Python application.
"""
