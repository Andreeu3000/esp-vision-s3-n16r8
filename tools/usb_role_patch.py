"""Apply ESP32-S3 USB Device <-> Host role-switch and Host serial support."""

from pathlib import Path
import sys


def _write_once(path: Path, content: str, marker: str) -> None:
    if path.is_file():
        current = path.read_text(encoding="utf-8")
        if marker in current and current == content:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def patch_micropython_tree(mp_build_dir) -> None:
    mp_build_dir = Path(mp_build_dir)
    usb_base = mp_build_dir / "micropython-lib/micropython/usb"
    if not usb_base.is_dir():
        raise RuntimeError(f"USB tree not found: {usb_base}")

    device_init = usb_base / "device/__init__.py"
    api_marker = "# PATCH_USB_ROLE_DEVICE_API"
    api_code = f'''{api_marker}
from _usb_role import device_prepare as _usb_role_device_prepare
from _usb_role import device_started as _usb_role_device_started
from _usb_role import device_release as _usb_role_device_release
from . import core as _usb_role_core


def disconnect():
    """Stop USB Device and release TinyUSB + ESP32-S3 PHY for Host role."""
    dev = _usb_role_core.get()
    if dev.active():
        dev.active(False)
    return _usb_role_device_release()


def connect():
    """Start the configured USB Device interfaces again."""
    dev = _usb_role_core.get()
    _usb_role_device_prepare()
    if not dev.active() and dev._itfs:
        dev.active(True)
    if dev.active():
        _usb_role_device_started()
    return True
'''
    _write_once(device_init, api_code, api_marker)

    host_py = usb_base / "host.py"
    host_code = '''"""USB Host role control for ESP32-S3."""
from _usb_role import host_connect as _host_connect
from _usb_role import host_disconnect as _host_disconnect
from _usb_role import host_info as _host_info


def connect():
    """Install the ESP-IDF USB Host Library and start its event daemon."""
    return _host_connect()


def disconnect():
    """Free enumerated devices and uninstall the USB Host Library."""
    return _host_disconnect()


def info():
    """Return Host state and the number of enumerated USB devices."""
    return _host_info()


def is_active():
    return info()["active"]


def device_count():
    return info()["devices"]
'''
    _write_once(host_py, host_code, '"""USB Host role control for ESP32-S3."""')

    serial_py = usb_base / "host/serial.py"
    serial_code = '''"""USB Host serial transport for CDC/bulk devices such as GRBL CNC."""
import time
from _usb_role import (
    serial_open as _ser_open,
    serial_write as _ser_write,
    serial_read as _ser_read,
    serial_close as _ser_close,
)


class Serial:
    def __init__(self):
        self._is_open = False
        self._timeout_ms = 1000
        self._buf = bytearray()

    def open(self, baudrate=115200, timeout=1000):
        self._timeout_ms = int(timeout)
        _ser_open(baudrate=baudrate, timeout=timeout)
        self._is_open = True
        self._buf = bytearray()
        return True

    def write(self, data, timeout=None):
        if not self._is_open:
            raise OSError("Serial port is not open")
        if isinstance(data, str):
            data = data.encode("utf-8")
        to = self._timeout_ms if timeout is None else int(timeout)
        return _ser_write(data, to)

    def read(self, n=64, timeout=None):
        if not self._is_open:
            raise OSError("Serial port is not open")
        to = self._timeout_ms if timeout is None else int(timeout)
        out = bytearray()
        if self._buf:
            take = min(n, len(self._buf))
            out.extend(self._buf[:take])
            del self._buf[:take]
        if len(out) < n:
            chunk = _ser_read(n - len(out), to)
            if chunk:
                out.extend(chunk)
        return bytes(out)

    def readline(self, timeout=None):
        if not self._is_open:
            raise OSError("Serial port is not open")
        to = self._timeout_ms if timeout is None else int(timeout)
        deadline = time.ticks_add(time.ticks_ms(), to)
        while True:
            idx = self._buf.find(b"\\n")
            if idx != -1:
                line = bytes(self._buf[: idx + 1])
                del self._buf[: idx + 1]
                return line
            rem = time.ticks_diff(deadline, time.ticks_ms())
            if rem <= 0:
                line = bytes(self._buf)
                self._buf.clear()
                return line
            chunk = _ser_read(64, min(rem, 100))
            if chunk:
                self._buf.extend(chunk)

    def close(self):
        if self._is_open:
            _ser_close()
            self._is_open = False
        self._buf.clear()
        return True


__all__ = ["Serial"]
'''
    _write_once(serial_py, serial_code, '"""USB Host serial transport')

    root_init = usb_base / "__init__.py"
    root_code = '''"""USB Device and Host role APIs."""
from . import device, host
'''
    _write_once(root_init, root_code, '"""USB Device and Host role APIs."""')

    print("[USB ROLE] PHY release, native Host control, serial API and Python wrappers applied.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python usb_role_patch.py /path/to/mp_build_dir")
    patch_micropython_tree(sys.argv[1])
