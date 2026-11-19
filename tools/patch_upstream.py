from pathlib import Path
import sys

root = Path(sys.argv[1])
p = root / "platform/usb_auto_download.c"
s = p.read_text()

include = '#include "usb_auto_download.h"\n'
if '#include "boardconfig.h"' not in s:
    assert include in s
    s = s.replace(
        include,
        include + '#include "boardconfig.h"\n'
        '#ifndef ESP_VISION_ENABLE_USB_SERIAL_JTAG\n'
        '#define ESP_VISION_ENABLE_USB_SERIAL_JTAG (0)\n'
        '#endif\n',
        1
    )

old = """#if CONFIG_IDF_TARGET_ESP32S3
        /* S3's USB ROM hand-off needs persistence before the reboot. */
        usb_usj_mode();
        int persist_ret = usb_dc_prepare_persist();
        if (persist_ret != 0) {
            USB_AUTO_DOWNLOAD_LOG("usb persistence prepare failed=%d", persist_ret);
        }
        chip_usb_set_persist_flags(USBDC_BOOT_DFU);
#endif"""

new = old.replace(
    "#if CONFIG_IDF_TARGET_ESP32S3",
    "#if CONFIG_IDF_TARGET_ESP32S3 && ESP_VISION_ENABLE_USB_SERIAL_JTAG",
    1
)

if old in s:
    s = s.replace(old, new, 1)
elif new not in s:
    raise RuntimeError(
        "No se encontró el bloque USB original ni el ya parcheado."
    )

p.write_text(s)
print("Parche USB aplicado o ya estaba aplicado.")

# PATCH: include boardconfig.h in ev_stdio.c
# This makes ESP_VISION_EV_MUX_DEFAULT_ENABLED board-specific.
repo_root = Path(__file__).resolve().parents[1]
upstream = Path(sys.argv[1])

board_cfg = repo_root / "boards/ESP32_S3_N16R8/boardconfig.h"
assert board_cfg.exists(), "No existe boardconfig.h de la placa"

board_text = board_cfg.read_text()
assert "#define ESP_VISION_EV_MUX_DEFAULT_ENABLED (0)" in board_text, \
    "EV-MUX no está desactivado en boardconfig.h"

ev_stdio = upstream / "platform/ev_stdio.c"
source = ev_stdio.read_text()

anchor = '#include "ev_stdio.h"\n'
include = '#include "boardconfig.h"\n'

if include not in source:
    assert anchor in source, "No se encontró el include de ev_stdio.h"
    source = source.replace(anchor, anchor + include, 1)

ev_stdio.write_text(source)
print("Configuración de placa conectada correctamente a ev_stdio.c")

# PATCH: instalar lector QR secundario Quirc para ESP32-S3.
from shutil import copy2, copytree

repo_root = Path(__file__).resolve().parents[1]
quirc_upstream = Path(sys.argv[1])

source_modules = repo_root / "modules"
source_component = repo_root / "components/s3_quirc"

assert (source_modules / "py_quirc.c").is_file()
assert (source_modules / "quirc/decode.c").is_file()
assert (source_modules / "quirc/quirc.h").is_file()
assert (source_modules / "quirc/LICENSE").is_file()
assert (source_component / "CMakeLists.txt").is_file()
assert (source_component / "include/s3_quirc.h").is_file()

copytree(
    source_modules / "quirc",
    quirc_upstream / "modules/quirc",
    dirs_exist_ok=True
)

copy2(
    source_modules / "py_quirc.c",
    quirc_upstream / "modules/py_quirc.c"
)

copytree(
    source_component,
    quirc_upstream / "components/s3_quirc",
    dirs_exist_ok=True
)

print("Fuentes y componente Quirc instalados.")

cmake_file = quirc_upstream / "micropython.cmake"
cmake = cmake_file.read_text()

def remove_marked_block(content, marker):
    while marker in content:
        start = content.index(marker)
        end = content.find("\nendif()", start)

        if end < 0:
            raise RuntimeError(
                f"No se encontró endif() para el bloque: {marker}"
            )

        end += len("\nendif()")
        content = content[:start] + content[end:]

    return content


# Eliminar las versiones anteriores del parche, incluso si estaban
# colocadas en el lugar incorrecto.
cmake = remove_marked_block(
    cmake,
    "# ESP-VISION secondary Quirc reader (S3 only)."
)

cmake = remove_marked_block(
    cmake,
    "# Link ESP-VISION secondary Quirc component (S3 only)."
)

# Añadir el binding directamente al target INTERFACE del usermod.
# usermod_gather_sources() recogerá esta fuente para compilación y QSTR.
source_block = """
# ESP-VISION secondary Quirc reader (S3 only).
if(IDF_TARGET STREQUAL "esp32s3")
    target_sources(usermod_esp_vision_platform INTERFACE
        ${ESP_VISION_ROOT}/modules/py_quirc.c
    )

    target_include_directories(usermod_esp_vision_platform INTERFACE
        ${ESP_VISION_ROOT}/components/s3_quirc/include
        ${ESP_VISION_ROOT}/modules/quirc
    )

    list(APPEND IDF_COMPONENTS s3_quirc)
endif()
"""

anchor = "target_link_libraries(usermod INTERFACE usermod_esp_vision_platform)"

if cmake.count(anchor) != 1:
    raise RuntimeError(
        "No se encontró exactamente una declaración del enlace usermod."
    )

cmake = cmake.replace(
    anchor,
    source_block + "\n" + anchor,
    1
)

cmake_file.write_text(cmake)

print("Binding py_quirc.c añadido al target usermod.")
print("Componente s3_quirc añadido a IDF_COMPONENTS.")
print("QSTR gestionado por el mecanismo estándar de MicroPython.")

# Compatibilidad MicroPython NimBLE con ESP-IDF 5.5.x.
idf_ext_path = root / "idf_ext.py"
idf_ext_text = idf_ext_path.read_text()

marker = "# ESP-VISION: patch MicroPython NimBLE symbols for ESP-IDF 5.5.\n"
needle = "            selected = prepare_micropython_tree(board)\n"

injected = '''            # ESP-VISION: patch MicroPython NimBLE symbols for ESP-IDF 5.5.
            if idf_version().startswith("5.5"):
                nimble_c = os.path.join(
                    mp_build_dir(),
                    "extmod", "nimble", "modbluetooth_nimble.c"
                )

                with open(nimble_c, encoding="utf-8") as f:
                    nimble_text = f.read()

                declarations = (
                    "    extern uint16_t ble_hs_max_attrs;\\n",
                    "    extern uint16_t ble_hs_max_services;\\n",
                    "    extern uint16_t ble_hs_max_client_configs;\\n",
                )

                found = [item in nimble_text for item in declarations]

                if any(found) and not all(found):
                    raise FatalError(
                        "NimBLE: declaraciones extern incompletas; "
                        "no se aplica el parche."
                    )

                if all(found):
                    for item in declarations:
                        nimble_text = nimble_text.replace(item, "", 1)

                    # ESP-IDF 5.5: los contadores GATT son macros
                    # que acceden al contexto interno de NimBLE.
                    # Inicializar NimBLE antes de reiniciarlos.
                    init_start = nimble_text.find(
                        "int mp_bluetooth_init(void) {"
                    )
                    deinit_start = nimble_text.find(
                        "int mp_bluetooth_deinit(void) {",
                        init_start
                    )

                    if init_start < 0 or deinit_start < 0:
                        raise RuntimeError(
                            "No se encontraron los límites de "
                            "mp_bluetooth_init()."
                        )

                    init_block = nimble_text[init_start:deinit_start]

                    early_call = "    nimble_reset_gatts_bss();\\n"
                    callback_marker = "ble_hs_cfg.reset_cb = reset_cb;"

                    port_pos = init_block.find("nimble_port_init();")
                    callback_pos = init_block.find(callback_marker)

                    if init_block.count(early_call) == 1:
                        if port_pos < 0 or callback_pos <= port_pos:
                            raise RuntimeError(
                                "nimble_port_init() o callback "
                                "en una posición inesperada."
                            )

                        init_block = init_block.replace(
                            early_call, "", 1
                        )

                        callback_pos = init_block.find(callback_marker)

                        init_block = (
                            init_block[:callback_pos]
                            + "    nimble_reset_gatts_bss();\\n\\n"
                            + init_block[callback_pos:]
                        )

                        nimble_text = (
                            nimble_text[:init_start]
                            + init_block
                            + nimble_text[deinit_start:]
                        )

                        print(
                            "[prepare-micropython] "
                            "NimBLE GATT reset moved after init."
                        )

                    elif (
                        init_block.count("nimble_reset_gatts_bss();") == 1
                        and port_pos >= 0
                        and (
                            init_block.find("nimble_reset_gatts_bss();")
                            > port_pos
                        )
                    ):
                        print(
                            "[prepare-micropython] "
                            "NimBLE GATT reset already relocated."
                        )
                    else:
                        raise RuntimeError(
                            "La llamada GATT tiene una disposición "
                            "inesperada; se cancela el parche."
                        )

                    with open(nimble_c, "w", encoding="utf-8") as f:
                        f.write(nimble_text)

                    print(
                        "[prepare-micropython] "
                        "NimBLE compatibility patch applied."
                    )
                else:
                    print(
                        "[prepare-micropython] "
                        "NimBLE declarations already absent."
                    )

'''

if marker not in idf_ext_text:
    if needle not in idf_ext_text:
        raise RuntimeError(
            "No se encontró el punto de inserción en idf_ext.py"
        )

    idf_ext_text = idf_ext_text.replace(
        needle, needle + injected, 1
    )
    idf_ext_path.write_text(idf_ext_text)
    print("Parche de compatibilidad NimBLE instalado.")
else:
    print("Parche de compatibilidad NimBLE ya instalado.")

# ============================================================
# PATCH: EV-MUX EVENTS ONLY WHEN MUX IS ENABLED
# ============================================================

p = root / "platform/ev_control_transport.c"
source = p.read_text()

old = """{
    ev_link_ref_t target;
    const char *session_id = NULL;
    uint32_t vm_epoch = 0;
    uint32_t tx_vm_epoch = 0;
    // Keep the event's session storage alive until submit has copied it.
    ev_session_snapshot_t snapshot;"""

new = """{
    // Never inject EV-MUX events into the legacy raw REPL.
    // Events are allowed again when EV-MUX is explicitly enabled.
    if ((type != NULL) && (strcmp(type, "event") == 0) &&
            !ev_stdio_mux_enabled()) {
        return ESP_ERR_INVALID_STATE;
    }

    ev_link_ref_t target;
    const char *session_id = NULL;
    uint32_t vm_epoch = 0;
    uint32_t tx_vm_epoch = 0;
    // Keep the event's session storage alive until submit has copied it.
    ev_session_snapshot_t snapshot;"""

if new not in source:
    if source.count(old) != 1:
        raise RuntimeError(
            "No se encontró exactamente el bloque esperado "
            "en ev_control_transport.c. No se modificó."
        )

    source = source.replace(old, new, 1)
    p.write_text(source)

print("[OK] EV-MUX events gated by runtime state")


# PATCH_TRACE_ISR_EXIT_TO_SCHEDULER
# Define el hook antes de que FreeRTOS incluya portmacro.h.
from pathlib import Path as _PatchPath
import sys as _PatchSys

_msc_file = _PatchPath(_PatchSys.argv[1]) / "platform/storage_writer_msc.c"

if not _msc_file.is_file():
    raise RuntimeError(
        f"No se encontró el archivo esperado: {_msc_file}"
    )

_msc_source = _msc_file.read_text()
_msc_macro = "#ifndef traceISR_EXIT_TO_SCHEDULER\n#define traceISR_EXIT_TO_SCHEDULER()\n#endif\n"

if "PATCH_TRACE_ISR_EXIT_TO_SCHEDULER" not in _msc_source:
    if "traceISR_EXIT_TO_SCHEDULER" not in _msc_source:
        _msc_file.write_text(
            _msc_macro + "\n/* PATCH_TRACE_ISR_EXIT_TO_SCHEDULER */\n" + _msc_source
        )
        print("[OK] Parche aplicado a storage_writer_msc.c")
    else:
        print("[INFO] El hook ya está definido en storage_writer_msc.c")
else:
    print("[INFO] storage_writer_msc.c ya tiene el parche")

# PATCH_FREERTOS_ISR_TRACE_MICROPYTHON_PORT
# Patch the generated MicroPython port during preparation.

_idf_ext_path = root / "idf_ext.py"

if not _idf_ext_path.is_file():
    raise RuntimeError(f"No se encontró {_idf_ext_path}")

_idf_source = _idf_ext_path.read_text()

_idf_anchor = """        merge_board_component_manifest(dst, board)

        return os.path.relpath(src, root)"""

_idf_insert = r"""        merge_board_component_manifest(dst, board)

        # PATCH_FREERTOS_ISR_TRACE_MICROPYTHON_PORT
        mpconfigport_path = os.path.join(mp_port_dir(), "mpconfigport.h")

        with open(mpconfigport_path, encoding="utf-8") as file:
            mpconfigport_source = file.read()

        trace_marker = "/* PATCH_FREERTOS_ISR_EXIT_TO_SCHEDULER */"
        trace_macro = (
            "#ifndef traceISR_EXIT_TO_SCHEDULER\n"
            "#define traceISR_EXIT_TO_SCHEDULER()\n"
            "#endif\n"
        )

        if trace_marker not in mpconfigport_source:
            freertos_include = '#include "freertos/FreeRTOS.h"'

            if mpconfigport_source.count(freertos_include) != 1:
                raise FatalError(
                    "Expected exactly one FreeRTOS.h include in mpconfigport.h"
                )

            mpconfigport_source = mpconfigport_source.replace(
                freertos_include,
                trace_marker + "\n" + trace_macro + "\n" + freertos_include,
                1,
            )

            with open(mpconfigport_path, "w", encoding="utf-8") as file:
                file.write(mpconfigport_source)

            print("[prepare-micropython] FreeRTOS trace hook applied")
        else:
            print("[prepare-micropython] FreeRTOS trace hook already patched")

        return os.path.relpath(src, root)"""

_idf_patch_marker = "# PATCH_FREERTOS_ISR_TRACE_MICROPYTHON_PORT"
_anchor_count = _idf_source.count(_idf_anchor)
_marker_count = _idf_source.count(_idf_patch_marker)

if _anchor_count == 1 and _marker_count == 0:
    _idf_source = _idf_source.replace(_idf_anchor, _idf_insert, 1)
    _idf_ext_path.write_text(_idf_source)
    print("[OK] idf_ext.py preparado para parchear mpconfigport.h")
elif _anchor_count == 0 and _marker_count == 1:
    print("[INFO] idf_ext.py ya tiene el parche FreeRTOS")
else:
    raise RuntimeError(
        f"Estado inesperado en idf_ext.py: "
        f"anchor={_anchor_count}, marker={_marker_count}"
    )

# PATCH_USB_HID_RECONNECT_SAFE
# Make USB HID transfers safe across host reset and reconnection.
_usb_mp_lib = root / "lib/micropython/lib/micropython-lib/micropython/usb"

_usb_core_path = _usb_mp_lib / "usb-device/usb/device/core.py"
_usb_mouse_path = _usb_mp_lib / "usb-device-mouse/usb/device/mouse.py"

for _usb_path in (_usb_core_path, _usb_mouse_path):
    if not _usb_path.is_file():
        raise RuntimeError(f"No se encontró el módulo USB esperado: {_usb_path}")

# Fix endpoint lookup after _reset_cb() clears _ep_cbs.
_usb_core_source = _usb_core_path.read_text(encoding="utf-8")

_usb_old_pending = (
    "        return self._ep_cbs[ep_addr] or "
    "(self._cb_ep == ep_addr and self._cb_thread != get_ident())"
)
_usb_new_pending = (
    "        return self._ep_cbs.get(ep_addr, False) or "
    "(self._cb_ep == ep_addr and self._cb_thread != get_ident())"
)

if _usb_old_pending in _usb_core_source:
    if _usb_core_source.count(_usb_old_pending) != 1:
        raise RuntimeError("Hay múltiples accesos antiguos a _ep_cbs.")
    _usb_core_source = _usb_core_source.replace(
        _usb_old_pending, _usb_new_pending, 1
    )
    print("[OK] USB HID: acceso seguro a _ep_cbs.")
elif _usb_new_pending in _usb_core_source:
    print("[INFO] USB HID: acceso seguro a _ep_cbs ya aplicado.")
else:
    raise RuntimeError("No se encontró el bloque esperado en usb/device/core.py")

# Give the mouse transfer wait loop a finite timeout.
_usb_mouse_source = _usb_mouse_path.read_text(encoding="utf-8")

if "import time\n" not in _usb_mouse_source:
    _usb_import_anchor = "import machine\n"
    if _usb_mouse_source.count(_usb_import_anchor) != 1:
        raise RuntimeError("No se encontró el import de machine en mouse.py")
    _usb_mouse_source = _usb_mouse_source.replace(
        _usb_import_anchor,
        _usb_import_anchor + "import time\n",
        1,
    )

_usb_old_mouse_fn = """    def send_report(self, dx=0, dy=0):
        b = 0
        if self._l:
            b |= 1 << 0
        if self._r:
            b |= 1 << 1
        if self._m:
            b |= 1 << 2

        # Wait for any pending report to be sent to the host
        # before updating contents of _buf.
        #
        # This loop can be removed if you don't care about possibly missing a
        # transient report, the final report buffer contents will always be the
        # last one sent to the host (it just might lose one of the ones in the
        # middle).
        while self.busy():
            machine.idle()

        struct.pack_into("Bbb", self._buf, 0, b, dx, dy)

        return super().send_report(self._buf)
"""

_usb_new_mouse_fn = """    def send_report(self, dx=0, dy=0, timeout_ms=100):
        b = 0
        if self._l:
            b |= 1 << 0
        if self._r:
            b |= 1 << 1
        if self._m:
            b |= 1 << 2

        # Wait for the previous report, but never wait indefinitely.
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while self.busy():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                return False
            machine.idle()

        # The host may have disconnected while this report was waiting.
        if not self.is_open():
            return False

        struct.pack_into("Bbb", self._buf, 0, b, dx, dy)

        return super().send_report(self._buf, timeout_ms=timeout_ms)
"""

if _usb_old_mouse_fn in _usb_mouse_source:
    if _usb_mouse_source.count(_usb_old_mouse_fn) != 1:
        raise RuntimeError("Hay múltiples funciones send_report antiguas en mouse.py")
    _usb_mouse_source = _usb_mouse_source.replace(
        _usb_old_mouse_fn, _usb_new_mouse_fn, 1
    )
    print("[OK] USB HID: timeout para transferencias del mouse.")
elif _usb_new_mouse_fn in _usb_mouse_source:
    print("[INFO] USB HID: timeout del mouse ya aplicado.")
else:
    raise RuntimeError("No coincide la función send_report esperada en mouse.py")

# Write only after both source transformations have been validated.
_usb_core_path.write_text(_usb_core_source, encoding="utf-8")
_usb_mouse_path.write_text(_usb_mouse_source, encoding="utf-8")

print("[OK] Parches USB HID preparados.")
