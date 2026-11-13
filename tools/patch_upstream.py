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

assert old in s, "No se encontró el bloque USB esperado; no se modificó."
p.write_text(s.replace(old, new, 1))
print("Parche USB aplicado correctamente.")
