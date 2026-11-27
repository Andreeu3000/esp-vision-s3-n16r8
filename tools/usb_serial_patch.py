"""Integrate USB Host serial into the pinned ESP-VISION tree."""
from pathlib import Path
import shutil, sys

repo=Path(__file__).resolve().parents[1]
upstream=Path(sys.argv[1]).resolve()
cmake_file=upstream/"micropython.cmake"
role_helper=upstream/"tools"/"usb_role_patch.py"
src=repo/"modules"/"py_usb_serial.c"
manifest=repo/"boards"/"ESP32_S3_N16R8"/"idf_component.yml"
for p in (cmake_file,role_helper,src,manifest):
    if not p.is_file(): raise RuntimeError(f"USB serial integration: missing {p}")
# The native module is compiled by MicroPython's ESP32 component source list.
# Do not also add it to micropython.cmake: that would compile/register it twice.
shutil.copy2(src,upstream/"modules"/"py_usb_serial.c")
shutil.copy2(src,upstream/"tools"/"py_usb_serial.c")

h=role_helper.read_text()
copy_anchor="    shutil.copy2(source_c, dest_c)"
copy_marker="# PATCH_USB_HOST_SERIAL_SOURCE"
copy_block="""
    # PATCH_USB_HOST_SERIAL_SOURCE
    serial_source = Path(__file__).with_name("py_usb_serial.c")
    if not serial_source.is_file():
        raise RuntimeError(f"USB serial source missing: {serial_source}")
    shutil.copy2(serial_source, port / "py_usb_serial.c")
"""
if copy_marker not in h:
    if h.count(copy_anchor)!=1: raise RuntimeError("role patch source-copy anchor not unique")
    h=h.replace(copy_anchor,copy_anchor+"\n"+copy_block,1)

cm_anchor='    cmake_line = "    py_usb_role.c"'
cm_repl='    cmake_line = "    py_usb_role.c\\n    py_usb_serial.c"'
if cm_repl not in h:
    if h.count(cm_anchor)!=1: raise RuntimeError("role patch CMake source anchor not unique")
    h=h.replace(cm_anchor,cm_repl,1)
    old='cmake_text.count(cmake_line) != 1'
    new=('cmake_text.count("    py_usb_role.c") != 1 or '
         'cmake_text.count("    py_usb_serial.c") != 1')
    if h.count(old)!=1: raise RuntimeError("role patch source validation anchor not unique")
    h=h.replace(old,new,1)

q=chr(39)*3
start_tag="    host_code = "+q
end_tag=q+"\n    _write_once(host_py, host_code,"
if start_tag not in h or end_tag not in h: raise RuntimeError("host.py template boundaries not found")
start=h.index(start_tag); end=h.index(end_tag,start)+3
host_py = "\"\"\"USB Host role and serial APIs for ESP32-S3.\"\"\"\nfrom _usb_role import host_connect as _host_connect\nfrom _usb_role import host_disconnect as _host_disconnect\nfrom _usb_role import host_info as _host_info\nfrom _usb_serial import install as _serial_install\nfrom _usb_serial import open as _serial_open\nfrom _usb_serial import write as _serial_write\nfrom _usb_serial import read as _serial_read\nfrom _usb_serial import any as _serial_any\nfrom _usb_serial import is_connected as _serial_is_connected\nfrom _usb_serial import close as _serial_close\nfrom _usb_serial import shutdown as _serial_shutdown\nimport time\n\ndef connect():\n    return _host_connect()\n\ndef disconnect():\n    _serial_shutdown()\n    return _host_disconnect()\n\ndef info():\n    return _host_info()\n\ndef is_active():\n    return info()[\"active\"]\n\ndef device_count():\n    return info()[\"devices\"]\n\nclass Serial:\n    def __init__(self, baudrate=115200, timeout_ms=10000):\n        _serial_install()\n        _serial_open(baudrate, timeout_ms)\n        self._closed = False\n\n    def write(self, data, timeout_ms=2000):\n        if self._closed: raise OSError(\"USB_SERIAL_CLOSED\")\n        if isinstance(data, str): data = data.encode()\n        return _serial_write(data, timeout_ms)\n\n    def read(self, size=0, timeout_ms=0):\n        if self._closed: raise OSError(\"USB_SERIAL_CLOSED\")\n        return _serial_read(size, timeout_ms)\n\n    def any(self):\n        return _serial_any()\n\n    def readline(self, timeout_ms=1000):\n        if self._closed: raise OSError(\"USB_SERIAL_CLOSED\")\n        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)\n        line = bytearray()\n        while time.ticks_diff(deadline, time.ticks_ms()) > 0:\n            remaining = time.ticks_diff(deadline, time.ticks_ms())\n            chunk = _serial_read(1, min(remaining, 100))\n            if chunk:\n                if chunk == b\"\\n\": return bytes(line).rstrip(b\"\\r\")\n                line.extend(chunk)\n        return bytes(line)\n\n    def is_connected(self):\n        return not self._closed and _serial_is_connected()\n\n    def close(self):\n        if not self._closed:\n            _serial_close()\n            self._closed = True\n\ndef serial(baudrate=115200, timeout_ms=10000):\n    return Serial(baudrate, timeout_ms)\n"
h=h[:start]+"    host_code = "+repr(host_py)+h[end:]
old_marker = '_write_once(host_py, host_code, \'"""USB Host role control for ESP32-S3."""\')'
new_marker = '_write_once(host_py, host_code, \'"""USB Host role and serial APIs for ESP32-S3."""\')'
if old_marker in h:
    h = h.replace(old_marker, new_marker, 1)
elif new_marker not in h:
    raise RuntimeError("USB serial patch: host.py marker not found")
role_helper.write_text(h)
print("[USB SERIAL] integration applied")
