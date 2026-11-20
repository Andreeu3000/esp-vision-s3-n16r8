# USB Device <-> Host role switch patch

Target: ESP32-S3, MicroPython v1.28.0, ESP-IDF 5.5.x, ESP-VISION custom board.

## Install

Extract this folder into the root of `esp-vision-s3-n16r8`, then from that repo root run:

```sh
python usb_role_switch_patch/apply_usb_role_switch_patch.py
```

The installer creates/updates:

- `tools/usb_role_patch.py`
- `modules/py_usb_role.c`
- `tools/patch_upstream.py`

The existing `tools/usb_hid_patch.py` is not replaced. The installer checks for the existing USB HID integration before adding the role-switch integration.

## Python API added

```python
import usb

usb.device.disconnect()
usb.host.connect()
print(usb.host.info())
usb.host.disconnect()
usb.device.connect()
```

`usb.host.info()` returns `active`, `devices`, and `clients`.

## Important scope

This patch adds the role lifecycle and USB Host Library enumeration. It does **not** yet add HID Host class drivers, keyboard/mouse input reports, or Host-side APIs for reading key presses. The first test verifies role release/reacquisition and device enumeration count.

USB Host requires a real 5 V VBUS supply to the attached peripheral. The ESP-IDF Host stack setup does not guarantee that the development board sources VBUS. Use a suitable powered OTG adapter/hub or a correctly designed VBUS power circuit; share ground, and never apply 5 V to a 3.3 V GPIO.

The ESP-IDF C code is source-checked against the v5.5.6 USB Host API; final compiler validation must happen in the project's GitHub Actions build, then the firmware must be tested on the actual board.
