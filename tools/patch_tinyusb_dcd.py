#!/usr/bin/env python3
"""Patch ESP32-S3 TinyUSB DCD for USB role switching."""

from pathlib import Path
import re
import sys

MARKER = "/* PATCH_USB_ROLE_DCD_DEINIT */"


def patch(path):
    path = Path(path)

    if not path.is_file():
        raise RuntimeError(f"No existe el DCD: {path}")

    source = path.read_text(encoding="utf-8")

    if MARKER in source:
        if source.count(MARKER) != 1:
            raise RuntimeError("Marcador DCD duplicado")
        print("[USB DCD] parche ya aplicado")
        return

    include = '#include "esp_intr_alloc.h"'

    if source.count(include) != 1:
        raise RuntimeError("Include esp_intr_alloc.h no único")

    source = source.replace(
        include,
        include + '\n#include "esp_err.h"',
        1,
    )

    pattern = re.compile(
        r"void\s+dcd_int_enable\s*\(uint8_t\s+rhport\)\s*"
        r"\{.*?\}\s*"
        r"void\s+dcd_int_disable\s*\(uint8_t\s+rhport\)\s*"
        r"\{.*?\}",
        re.S,
    )

    replacement = r"""
/* PATCH_USB_ROLE_DCD_DEINIT */
void dcd_int_enable(uint8_t rhport)
{
    (void)rhport;

    if (usb_ih == NULL) {
        esp_err_t err = esp_intr_alloc(
            ETS_USB_INTR_SOURCE,
            ESP_INTR_FLAG_LOWMED,
            (intr_handler_t)_dcd_int_handler,
            NULL,
            &usb_ih
        );
        ESP_ERROR_CHECK(err);
    }
}

void dcd_int_disable(uint8_t rhport)
{
    (void)rhport;

    if (usb_ih != NULL) {
        ESP_ERROR_CHECK(esp_intr_free(usb_ih));
        usb_ih = NULL;
    }
}

bool dcd_deinit(uint8_t rhport)
{
    (void)rhport;

    USB0.gintmsk = 0;
    USB0.gahbcfg &= ~USB_GLBLLNTRMSK_M;
    USB0.dctl |= USB_SFTDISCON_M;

    return true;
}
"""

    source, count = pattern.subn(
        replacement.strip(),
        source,
        count=1,
    )

    if count != 1:
        raise RuntimeError("No se encontró el bloque DCD esperado")

    path.write_text(source + "\n", encoding="utf-8")
    print(f"[USB DCD] parche aplicado: {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Uso: patch_tinyusb_dcd.py RUTA_DCD")

    patch(sys.argv[1])
