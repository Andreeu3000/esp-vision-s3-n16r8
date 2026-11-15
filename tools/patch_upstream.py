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
