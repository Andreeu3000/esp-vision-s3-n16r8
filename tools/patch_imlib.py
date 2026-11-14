#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]

SOURCES = [
    "fft.c",
    "haar.c",
    "hog.c",
    "integral_mw.c",
    "isp.c",
    "lbp.c",
    "lodepng.c",
    "phasecorrelation.c",
    "selective_search.c",
    "stereo.c",
]

MACROS = [
    "IMLIB_ENABLE_PNG_DECODER",
    "IMLIB_ENABLE_PNG_ENCODER",
    "IMLIB_ENABLE_FEATURES",
    "IMLIB_ENABLE_HOG",
    "IMLIB_ENABLE_FIND_LBP",
    "IMLIB_ENABLE_ISP_OPS",
    "IMLIB_ENABLE_SELECTIVE_SEARCH",
    "IMLIB_ENABLE_STEREO_DISPARITY",
    "IMLIB_ENABLE_LINPOLAR",
    "IMLIB_ENABLE_LOGPOLAR",
    "IMLIB_ENABLE_FIND_DISPLACEMENT",
    "IMLIB_ENABLE_ROTATION_CORR",
]


def stop(message):
    raise SystemExit("[ERROR] " + message)


def write_if_changed(path, content):
    old = path.read_text(encoding="utf-8")
    if old != content:
        path.write_text(content, encoding="utf-8")
        print("[MODIFICADO]", path)
    else:
        print("[SIN CAMBIOS]", path)


def enable_macros(path):
    if not path.is_file():
        stop(f"No existe la configuración de placa: {path}")

    content = path.read_text(encoding="utf-8")

    if "OMV_NO_GPL" not in content:
        stop(f"Falta OMV_NO_GPL en {path}; no se modificará automáticamente.")

    missing = [
        macro for macro in MACROS
        if not re.search(
            rf"(?m)^\s*#\s*define\s+{re.escape(macro)}(?:\s|$)",
            content
        )
    ]

    if missing:
        marker = re.search(
            r"(?m)^#endif\s*/\*\s*ESP_VISION_IMLIB_CONFIG_H\s*\*/\s*$",
            content
        )
        if not marker:
            stop(f"No se encontró el cierre esperado del header: {path}")

        block = (
            "\n/* Optional image algorithms enabled for ESP32-S3. */\n"
            + "".join(f"#define {macro}\n" for macro in missing)
            + "\n"
        )
        content = content[:marker.start()] + block + content[marker.start():]

    for macro in MACROS:
        if not re.search(
            rf"(?m)^\s*#\s*define\s+{re.escape(macro)}(?:\s|$)",
            content
        ):
            stop(f"No se pudo activar {macro} en {path}")

    write_if_changed(path, content)


def patch_cmake(path, upstream_dir):
    if not path.is_file():
        stop(f"No existe {path}")

    content = path.read_text(encoding="utf-8")

    for name in SOURCES:
        source = upstream_dir / name
        if not source.is_file():
            stop(f"Falta el fuente requerido: {source}")

    anchor = '        "compat/array.c"'
    if content.count(anchor) != 1:
        stop("El punto de inserción de CMake no coincide con el commit auditado.")

    additions = []
    for name in SOURCES:
        entry = f'        "upstream/{name}"'
        if not re.search(
            rf'(?m)^\s*"{re.escape("upstream/" + name)}"\s*$',
            content
        ):
            additions.append(entry)

    if additions:
        content = content.replace(
            anchor,
            "\n".join(additions) + "\n" + anchor,
            1
        )

    for name in SOURCES:
        entry = f'"upstream/{name}"'
        if content.count(entry) != 1:
            stop(f"Entrada CMake ausente o duplicada: {entry}")

    write_if_changed(path, content)


def patch_selective_search(path):
    if not path.is_file():
        stop(f"No existe {path}")

    content = path.read_text(encoding="utf-8")
    start = content.find("static mp_obj_t py_image_selective_search")
    if start < 0:
        stop("No se encontró py_image_selective_search en py_image.c")

    end = content.find(
        "static MP_DEFINE_CONST_FUN_OBJ_KW(py_image_selective_search_obj",
        start
    )
    if end < 0:
        stop("No se pudo delimitar la función selective_search.")

    block = content[start:end]

    for variable in ("a2", "a3"):
        wrong = re.compile(
            rf"(float\s+{variable}\s*=\s*"
            rf"py_helper_keyword_float\([^;\n]*?MP_QSTR_)a1"
        )
        block, count = wrong.subn(rf"\g<1>{variable}", block, count=1)

        if count == 0:
            correct = re.search(
                rf"float\s+{variable}\s*=\s*"
                rf"py_helper_keyword_float\([^;\n]*?"
                rf"MP_QSTR_{variable}\b",
                block
            )
            if not correct:
                stop(f"No se pudo corregir/verificar el parámetro {variable}.")

    content = content[:start] + block + content[end:]
    write_if_changed(path, content)


def main():
    if len(sys.argv) != 2:
        stop("Uso: python3 tools/patch_imlib.py RUTA_ESP_VISION")

    upstream_root = Path(sys.argv[1]).resolve()
    imlib_dir = upstream_root / "components/imlib"
    upstream_dir = imlib_dir / "upstream"

    cmake = imlib_dir / "CMakeLists.txt"
    py_image = upstream_root / "modules/py_image.c"
    target_board = upstream_root / "boards/ESP32_S3_N16R8/imlib_config.h"
    local_board = ROOT / "boards/ESP32_S3_N16R8/imlib_config.h"

    if not upstream_root.is_dir():
        stop(f"No existe la copia ESP-VISION: {upstream_root}")
    if not upstream_dir.is_dir():
        stop(f"No existe el directorio upstream de imlib: {upstream_dir}")

    # El archivo OpenMV LodePNG incluye las extensiones que necesita png.c.
    vendored = ROOT / "components/imlib/third_party/lodepng"
    lodepng_c = vendored / "lodepng.c"
    lodepng_h = vendored / "lodepng.h"

    if not lodepng_c.is_file() or not lodepng_h.is_file():
        stop("Faltan los archivos LodePNG descargados en third_party/lodepng.")

    header = lodepng_h.read_text(encoding="utf-8", errors="replace")
    for token in ("LCT_CUSTOM", "customfmt", "lodepng_convert"):
        if token not in header:
            stop(f"El header LodePNG no contiene la extensión {token}.")

    shutil.copy2(lodepng_c, upstream_dir / "lodepng.c")
    shutil.copy2(lodepng_h, upstream_dir / "lodepng.h")
    print("[COPIADO] LodePNG compatible con png.c")

    # La configuración del repositorio y la copia de compilación quedan iguales.
    enable_macros(local_board)
    enable_macros(target_board)

    patch_cmake(cmake, upstream_dir)
    patch_selective_search(py_image)

    print()
    print("[OK] Parche de imlib aplicado.")
    print("[OK] PNG decoder y encoder habilitados.")
    print("[OK] Fuentes opcionales añadidos a CMake.")
    print("[OK] Parámetros a2/a3 de selective_search corregidos.")


if __name__ == "__main__":
    main()
