from pathlib import Path
import ast
import shutil

ROOT = Path.cwd()
PATCHER = ROOT / "tools" / "patch_upstream.py"
HELPER_SOURCE = Path(__file__).with_name("usb_role_patch.py")
C_SOURCE = Path(__file__).with_name("py_usb_role.c")

if not (ROOT / ".git").exists():
    raise SystemExit("Ejecuta este instalador desde la raíz de esp-vision-s3-n16r8.")
if not PATCHER.is_file() or not (ROOT / "tools" / "usb_hid_patch.py").is_file():
    raise SystemExit("No se reconoció el repo/estado esperado: falta tools/patch_upstream.py o usb_hid_patch.py.")
if not HELPER_SOURCE.is_file() or not C_SOURCE.is_file():
    raise SystemExit("Faltan archivos del bundle: usb_role_patch.py o py_usb_role.c.")

modules_dir = ROOT / "modules"
tools_dir = ROOT / "tools"
modules_dir.mkdir(exist_ok=True)
shutil.copy2(HELPER_SOURCE, tools_dir / "usb_role_patch.py")
shutil.copy2(C_SOURCE, modules_dir / "py_usb_role.c")

patcher_text = PATCHER.read_text(encoding="utf-8")
marker = "# PATCH_USB_ROLE_SWITCH_INTEGRATION"
if marker not in patcher_text:
    if "PATCH_USB_HID_HELPER_IDF_EXPORT_FIX" not in patcher_text:
        raise SystemExit("No se encontró la integración previa USB HID; no se modificó patch_upstream.py.")

    block = '''# PATCH_USB_ROLE_SWITCH_INTEGRATION
_usb_role_helper_source = repo_root / "tools/usb_role_patch.py"
_usb_role_c_source = repo_root / "modules/py_usb_role.c"
_usb_role_tools = root / "tools"
_usb_role_tools.mkdir(parents=True, exist_ok=True)
if not _usb_role_helper_source.is_file() or not _usb_role_c_source.is_file():
    raise RuntimeError("Faltan tools/usb_role_patch.py o modules/py_usb_role.c")
copy2(_usb_role_helper_source, _usb_role_tools / "usb_role_patch.py")
copy2(_usb_role_c_source, _usb_role_tools / "py_usb_role.c")

_idf_role_source = _idf_ext_path.read_text(encoding="utf-8")
_idf_role_marker = "# PATCH_USB_ROLE_SWITCH_PREPARED_TREE"
_idf_role_anchor = "        return os.path.relpath(src, root)"
if _idf_role_marker not in _idf_role_source:
    if _idf_role_source.count(_idf_role_anchor) != 1:
        raise RuntimeError("USB role patch: ancla prepare_micropython_tree no única")
    _idf_role_insert = r"""        # PATCH_USB_ROLE_SWITCH_PREPARED_TREE
        import sys as _usb_role_sys
        _usb_role_tools = os.path.join(root, "tools")
        if _usb_role_tools not in _usb_role_sys.path:
            _usb_role_sys.path.insert(0, _usb_role_tools)
        from usb_role_patch import patch_micropython_tree
        try:
            patch_micropython_tree(mp_build_dir())
        except Exception as exc:
            raise FatalError("USB role switch patch failed: " + str(exc)) from exc
"""
    _idf_role_source = _idf_role_source.replace(
        _idf_role_anchor, _idf_role_insert + _idf_role_anchor, 1
    )
    _idf_ext_path.write_text(_idf_role_source, encoding="utf-8")
elif _idf_role_source.count(_idf_role_marker) != 1:
    raise RuntimeError("USB role patch: marcador duplicado en idf_ext.py")
print("[OK] USB Device/Host role-switch helper integrado en idf_ext.py.")
'''

    patcher_text = patcher_text.rstrip() + "\n\n" + block
    ast.parse(patcher_text)
    PATCHER.write_text(patcher_text, encoding="utf-8")
elif patcher_text.count(marker) != 1:
    raise SystemExit("El marcador USB role está duplicado en tools/patch_upstream.py.")

ast.parse((tools_dir / "usb_role_patch.py").read_text(encoding="utf-8"))
ast.parse(PATCHER.read_text(encoding="utf-8"))
print("USB role switch patch instalado.")
print("Archivos creados: modules/py_usb_role.c, tools/usb_role_patch.py")
print("tools/patch_upstream.py actualizado de forma idempotente.")
