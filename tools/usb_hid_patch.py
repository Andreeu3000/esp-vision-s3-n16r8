"""Apply the ESP32-S3 MicroPython USB HID reliability patches.

Usage:
    python usb_hid_patch.py /path/to/micropython-lib/micropython/usb

The operation is idempotent. All target files are validated and transformed
in memory before any file is written.
"""

from pathlib import Path
import re
import sys


def _replace_exactly_one(source: str, pattern: str, replacement: str, label: str) -> str:
    matches = list(re.finditer(pattern, source, re.MULTILINE | re.DOTALL))
    if len(matches) != 1:
        raise RuntimeError(f"{label}: expected exactly one method block, found {len(matches)}")
    return re.sub(pattern, lambda _match: replacement, source, count=1, flags=re.MULTILINE | re.DOTALL)


def patch_usb_tree(usb_root):
    """Patch core.py, hid.py, keyboard.py and mouse.py below usb_root."""
    usb_root = Path(usb_root)
    paths = {
        "core": usb_root / "usb-device/usb/device/core.py",
        "hid": usb_root / "usb-device-hid/usb/device/hid.py",
        "keyboard": usb_root / "usb-device-keyboard/usb/device/keyboard.py",
        "mouse": usb_root / "usb-device-mouse/usb/device/mouse.py",
    }
    for label, path in paths.items():
        if not path.is_file():
            raise RuntimeError(f"USB HID {label}: source file not found: {path}")

    # Read every file first. Nothing is written until every transformation passes.
    src = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}

    # Avoid KeyError if a reset/reconnect clears the endpoint callback table.
    old_pending = (
        "        return self._ep_cbs[ep_addr] or "
        "(self._cb_ep == ep_addr and self._cb_thread != get_ident())"
    )
    new_pending = (
        "        return self._ep_cbs.get(ep_addr, False) or "
        "(self._cb_ep == ep_addr and self._cb_thread != get_ident())"
    )
    if old_pending in src["core"]:
        if src["core"].count(old_pending) != 1:
            raise RuntimeError("USB HID core: duplicate old _ep_cbs lookup")
        src["core"] = src["core"].replace(old_pending, new_pending, 1)
    elif new_pending not in src["core"]:
        raise RuntimeError("USB HID core: _xfer_pending() does not match expected source")

    # Shared wait/submit path. Mouse calls _wait_ready() before modifying its
    # single report buffer, then _submit_report() directly to avoid a second wait.
    hid_block = '''    def _wait_ready(self, timeout_ms=100):
        # Wait until the previous interrupt transfer has completed.
        if timeout_ms < 0:
            raise ValueError("timeout_ms")
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while self.busy():
            if not self.is_open():
                raise OSError("USB_HID_DISCONNECTED: interface is no longer open")
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise OSError("USB_HID_TIMEOUT: endpoint remained busy")
            machine.idle()
        if not self.is_open():
            raise OSError("USB_HID_DISCONNECTED: interface is not open")

    def _submit_report(self, report_data):
        # The host may disappear between _wait_ready() and submission.
        if not self.is_open():
            raise OSError("USB_HID_DISCONNECTED: interface is not open")
        try:
            self.submit_xfer(self._int_ep, report_data)
        except RuntimeError as exc:
            if str(exc) == "Not open":
                raise OSError("USB_HID_DISCONNECTED: " + str(exc))
            raise OSError("USB_HID_TRANSFER_FAILED: " + str(exc))
        return True

    def send_report(self, report_data, timeout_ms=100):
        # Returns True when queued; raises OSError on disconnect, timeout or DCD failure.
        self._wait_ready(timeout_ms)
        return self._submit_report(report_data)

'''
    hid_pattern = r"^    def (?:_wait_ready|send_report)\(.*?(?=^    def desc_cfg\()"
    src["hid"] = _replace_exactly_one(src["hid"], hid_pattern, hid_block, "USB HID hid.py")

    # Keyboard uses ping-pong buffers. Swap only after the transfer was queued;
    # OSError from HIDInterface is allowed to propagate to the caller.
    keyboard_block = '''    def send_keys(self, down_keys, timeout_ms=100):
        # Send a report containing the currently pressed keys.
        r, s = self._key_reports  # next report buffer to send, spare report buffer
        r[0] = 0  # modifier byte
        i = 2  # index for next key array item to write to
        for k in down_keys:
            if k < 0:  # Modifier key
                r[0] |= -k
            elif i < _KEY_REPORT_LEN:
                r[i] = k
                i += 1
            else:  # Excess rollover! Can't report
                r[0] = 0
                for i in range(2, _KEY_REPORT_LEN):
                    r[i] = 0xFF
                break

        while i < _KEY_REPORT_LEN:
            r[i] = 0
            i += 1

        self.send_report(r, timeout_ms)
        # Swap only after the report has been queued successfully.
        self._key_reports[0] = s
        self._key_reports[1] = r
        return True

'''
    keyboard_pattern = r"^    def send_keys\(self, down_keys, timeout_ms=100\):\n.*?(?=^# HID keyboard report descriptor)"
    src["keyboard"] = _replace_exactly_one(
        src["keyboard"], keyboard_pattern, keyboard_block, "USB HID keyboard.py"
    )

    # Mouse waits before changing its one shared buffer, then submits without
    # waiting a second time in HIDInterface.send_report().
    mouse_block = '''    def send_report(self, dx=0, dy=0, timeout_ms=100):
        self._wait_ready(timeout_ms)

        b = 0
        if self._l:
            b |= 1 << 0
        if self._r:
            b |= 1 << 1
        if self._m:
            b |= 1 << 2

        struct.pack_into("Bbb", self._buf, 0, b, dx, dy)
        return self._submit_report(self._buf)

'''
    mouse_pattern = (
        r"^    def send_report\(self, dx=0, dy=0(?:, timeout_ms=100)?\):\n"
        r".*?(?=^    def click_left\()"
    )
    src["mouse"] = _replace_exactly_one(src["mouse"], mouse_pattern, mouse_block, "USB HID mouse.py")

    # Write only after all four source files have passed validation.
    for name, path in paths.items():
        if src[name] != path.read_text(encoding="utf-8"):
            path.write_text(src[name], encoding="utf-8")

    print("[USB HID] patches applied/verified: core.py, hid.py, keyboard.py, mouse.py")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python usb_hid_patch.py /path/to/micropython-lib/micropython/usb")
    patch_usb_tree(sys.argv[1])
