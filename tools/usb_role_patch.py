"""Idempotently add USB role switching to the exported MicroPython ESP32 tree."""
from pathlib import Path
import shutil


def _write_once(path: Path, text: str, marker: str) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in current:
        if current.count(marker) != 1:
            raise RuntimeError(f"Marcador duplicado en {path}: {marker}")
        return
    if path.exists() and current.strip():
        path.write_text(current.rstrip() + "\n\n" + text.rstrip() + "\n", encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")


def patch_micropython_tree(mp_root: str | Path) -> None:
    mp_root = Path(mp_root)
    port = mp_root / "ports" / "esp32"
    usb_c = port / "usb.c"
    common_cmake = port / "esp32_common.cmake"
    board_h = port / "boards" / "ESP32_S3_N16R8" / "mpconfigboard.h"
    usb_pkg = mp_root / "lib" / "micropython-lib" / "micropython" / "usb"
    usb_base = usb_pkg / "usb-device" / "usb"
    device_pkg = usb_base / "device"
    device_init = device_pkg / "__init__.py"
    device_core = device_pkg / "core.py"

    for required in (usb_c, common_cmake, board_h, device_core):
        if not required.is_file():
            raise RuntimeError(f"USB role patch: falta el archivo esperado: {required}")

    board_text = board_h.read_text(encoding="utf-8")
    for define in ("#define MICROPY_HW_USB_CDC (0)", "#define MICROPY_HW_USB_MSC (0)"):
        if define not in board_text:
            raise RuntimeError(f"USB role patch: se requiere {define} en {board_h}")

    # Add the native module to the MicroPython ESP32 port. This source list is
    # also consumed by QSTR generation in esp32_common.cmake.
    cmake_text = common_cmake.read_text(encoding="utf-8")
    cmake_marker = "# PATCH_USB_ROLE_SWITCH_SOURCE"
    cmake_line = "    py_usb_role.c"
    if cmake_marker not in cmake_text:
        anchor = "    usb.c\n    usb_serial_jtag.c"
        if cmake_text.count(anchor) != 1:
            raise RuntimeError("USB role patch: ancla usb.c en esp32_common.cmake no única")
        cmake_text = cmake_text.replace(
            anchor,
            "    usb.c\n" + cmake_marker + "\n" + cmake_line + "\n    usb_serial_jtag.c",
            1,
        )
        common_cmake.write_text(cmake_text, encoding="utf-8")
    elif cmake_text.count(cmake_marker) != 1 or cmake_text.count(cmake_line) != 1:
        raise RuntimeError("USB role patch: bloque CMake duplicado o incompleto")

    # Export a PHY release function from MicroPython's ESP32 USB port.
    usb_text = usb_c.read_text(encoding="utf-8")
    usb_marker = "/* PATCH_USB_ROLE_PHY_RELEASE */"
    if usb_marker not in usb_text:
        include_anchor = '#include "esp_mac.h"\n'
        if usb_text.count(include_anchor) != 1:
            raise RuntimeError("USB role patch: include esp_mac.h no único en usb.c")
        usb_text = usb_text.replace(include_anchor, '#include "esp_err.h"\n' + include_anchor, 1)

        init_anchor = "void usb_phy_init(void) {\n"
        if usb_text.count(init_anchor) != 1:
            raise RuntimeError("USB role patch: función usb_phy_init no única")
        usb_text = usb_text.replace(
            init_anchor,
            init_anchor + "    if (phy_hdl != NULL) {\n        return;\n    }\n",
            1,
        )

        usj_anchor = "\n#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32P4\nvoid usb_usj_mode(void) {"
        if usb_text.count(usj_anchor) != 1:
            raise RuntimeError(
                "USB role patch: límite después de usb_phy_init no único"
            )

        phy_release = """
/* PATCH_USB_ROLE_PHY_INIT */
esp_err_t mp_usb_phy_init_device(void) {
    if (phy_hdl != NULL) {
        return ESP_OK;
    }

    static const usb_phy_config_t phy_conf = {
        .controller = USB_PHY_CTRL_OTG,
        .otg_mode = USB_OTG_MODE_DEVICE,
        .target = USB_PHY_TARGET_INT,
    };

    return usb_new_phy(&phy_conf, &phy_hdl);
}

/* PATCH_USB_ROLE_PHY_RELEASE */
esp_err_t mp_usb_phy_deinit(void) {
    if (phy_hdl == NULL) {
        return ESP_OK;
    }
    esp_err_t err = usb_del_phy(phy_hdl);
    if (err == ESP_OK) {
        phy_hdl = NULL;
    }
    return err;
}
"""
        usb_text = usb_text.replace(
            usj_anchor,
            phy_release + usj_anchor,
            1,
        )
        usb_c.write_text(usb_text, encoding="utf-8")
    elif usb_text.count(usb_marker) != 1:
        raise RuntimeError("USB role patch: marcador PHY duplicado")

    # Copy the native module beside usb.c so it is compiled into the same IDF
    # component as MicroPython and inherits its USB/TinyUSB include paths.
    source_c = Path(__file__).with_name("py_usb_role.c")
    if not source_c.is_file():
        raise RuntimeError(f"USB role patch: no se encontró el módulo C: {source_c}")
    dest_c = port / "py_usb_role.c"
    shutil.copy2(source_c, dest_c)

    # Add Python-facing role APIs to micropython-lib's usb package.
    api_marker = "# PATCH_USB_ROLE_SWITCH_API"
    api_code = '''
# PATCH_USB_ROLE_SWITCH_API
from . import core as _usb_role_core
from _usb_role import device_prepare as _usb_role_device_prepare
from _usb_role import device_started as _usb_role_device_started
from _usb_role import device_release as _usb_role_device_release

def disconnect():
    """Stop USB Device and release TinyUSB plus the USB PHY."""
    dev = _usb_role_core.get()
    if dev.active():
        dev.active(False)
    return _usb_role_device_release()

def connect():
    """Reconnect the configured runtime USB Device."""
    dev = _usb_role_core.get()

    if not dev._itfs:
        raise OSError(
            "USB_DEVICE_NOT_CONFIGURED: configure at least one interface before connect()"
        )

    _usb_role_device_prepare()

    if not dev.active():
        dev.active(True)

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

    root_init = usb_base / "__init__.py"
    root_code = '''"""USB Device and Host role APIs."""
from . import device, host
'''
    _write_once(root_init, root_code, '"""USB Device and Host role APIs."""')

    print("[USB ROLE] PHY release, native Host control and Python APIs applied/verified.")
