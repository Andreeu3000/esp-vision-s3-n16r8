# ESP-VISION ESP32-S3 N16R8

Custom ESP-VISION firmware target for ESP32-S3 with:

- 16 MB Quad SPI Flash
- 8 MB Octal PSRAM
- ESP32-S3 dual-core processor
- MicroPython
- OpenMV imlib
- ESP-DL
- TensorFlow Lite Micro

## Build

Firmware is built using GitHub Actions.

## USB HID Device

The firmware includes a convenience module for USB keyboard, mouse and Consumer
Control (volume/media/system navigation) interfaces. Connect the ESP32-S3 USB
OTG port to a PC or Android device.

```python
import usb_hid
from usb.device.keyboard import KeyCode as KC

usb_hid.init()                       # Configure and connect all 3 HID interfaces
usb_hid.wait_ready(15000)            # Wait until the host enumerates them
usb_hid.send_text("hola mundo", 30)  # Text, 30 ms between characters
usb_hid.keyboard.send_keys((KC.LEFT_CTRL, KC.C))
usb_hid.keyboard.send_keys(())       # Release all keys
usb_hid.keyboard.hotkey(KC.LEFT_UI, KC.R)
usb_hid.move(25, 0)
usb_hid.click("left")
usb_hid.volume_up()
usb_hid.mute()
usb_hid.play_pause()
```

`send_text()` uses a US keyboard layout and supports ASCII letters, digits,
common punctuation, spaces, tabs and newlines. The receiving device interprets
key positions using its active keyboard layout, so select US layout on the host
for matching punctuation. Non-ASCII characters (including ñ and accents) are
rejected rather than typed incorrectly.

Available mouse buttons: `left`, `right`, `middle`. Movement is relative,
with each axis limited to -127..127 per report.

Consumer Control includes volume up/down, mute, play/pause, next/previous track,
stop, Home, Back, Forward, Refresh, Search and Bookmarks. OS support varies;
Android vendors and applications may handle navigation usages differently.
Windows shortcuts use normal keyboard modifiers, for example
`usb_hid.keyboard.hotkey(KC.LEFT_UI, KC.R)`.

### Switching USB roles

The HID helper does not change the role-switch implementation. Use the existing
role API in this order:

```python
import usb_hid
import usb

usb_hid.init()
# ... USB Device HID is active ...

usb.device.disconnect()
usb.host.connect()       # Host mode

usb.host.disconnect()
usb_hid.connect()        # Return to the already configured Device HID
```

After a MicroPython soft reset, Python objects and runtime USB interfaces are
cleared. Run `usb_hid.init()` again to recreate and configure them.

A runnable demonstration is in `examples/usb_hid_demo.py`.

## Status

Work in progress. Board configuration and hardware validation are pending.
