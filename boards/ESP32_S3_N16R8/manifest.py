# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

freeze("$(PORT_DIR)/modules")
freeze("$(ESP_VISION_ROOT)/modules", "py_inisetup.py")
freeze("$(ESP_VISION_ROOT)/boards/ESP32_S3_N16R8", "board_inisetup.py")
include("$(MPY_DIR)/extmod/asyncio")

require("bundle-networking")
require("onewire")
require("ds18x20")
require("dht")
require("neopixel")
require("umqtt.simple")
require("umqtt.robust")
require("usb-device-keyboard")
require("usb-device-mouse")
