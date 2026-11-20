#!/usr/bin/env python3
"""Patch ESP32-S3 TinyUSB DCD for safe Device <-> Host switching."""

from pathlib import Path
import re
import sys

DEINIT_MARKER = "/* PATCH_USB_ROLE_DCD_DEINIT */"

INTR_PATTERN = re.compile(
    r"void\s+dcd_int_enable\s*\(\s*uint8_t\s+rhport\s*\)\s*"
    r"\{.*?\}\s*"
    r"void\s+dcd_int_disable\s*\(\s*uint8_t\s+rhport\s*\)\s*"
    r"\{.*?\}",
    re.S,
)

INIT_PATTERN = re.compile(
    r"(?m)^bool\s+dcd_init\s*\(\s*uint8_t\s+rhport\s*,"
    r"\s*const\s+tusb_rhport_init_t\s*\*\s*rh_init\s*\)\s*\{"
)


def ensure_include(source: str, header: str) -> str:
    line = f'#include "{header}"'

    if line in source:
        return source

    anchor = '#include "esp_intr_alloc.h"'
    if source.count(anchor) != 1:
        raise RuntimeError(
            f"No se encuentra un include único para agregar {line}"
        )

    return source.replace(anchor, anchor + "\n" + line, 1)


def patch(path: str) -> None:
    path = Path(path)

    if not path.is_file():
        raise RuntimeError(f"No existe el DCD: {path}")

    source = path.read_text(encoding="utf-8")

    # Required ESP-IDF definitions and register structure.
    for header in (
        "esp_err.h",
        "soc/usb_reg.h",
        "soc/usb_struct.h",
    ):
        source = ensure_include(source, header)

    # 1. Make DCD interrupt allocation/deallocation repeatable.
    if DEINIT_MARKER not in source:
        if re.search(r"\b(?:bool|void)\s+dcd_deinit\s*\(", source):
            raise RuntimeError(
                "Existe dcd_deinit() sin el marcador esperado. "
                "No se modificará automáticamente."
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

        source, count = INTR_PATTERN.subn(
            replacement.strip(),
            source,
            count=1,
        )

        if count != 1:
            raise RuntimeError(
                "No se encontró exactamente el bloque "
                "dcd_int_enable/dcd_int_disable."
            )

    elif source.count(DEINIT_MARKER) != 1:
        raise RuntimeError("Marcador DCD DEINIT duplicado")

    # Persist the patched DCD.

    path.write_text(source.rstrip() + "\n", encoding="utf-8")

    print("[USB DCD] DEINIT:", "OK")
    print("[USB DCD] archivo:", path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Uso: python3 patch_tinyusb_dcd.py RUTA_DCD"
        )

    patch(sys.argv[1])
