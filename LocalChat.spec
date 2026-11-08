# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for LocalVault AI. The file name stays "Local Chat.exe" so an
# existing install can be replaced in place. Built by build_exe.bat:
#   pyinstaller --noconfirm --clean LocalChat.spec
# Env LOCALCHAT_ONEFILE=1 -> single dist\Local Chat.exe (slower start); default -> dist\Local Chat\ folder.
import os
import sys

HERE = SPECPATH  # noqa: F821  (defined by PyInstaller)
ONEFILE = os.environ.get("LOCALCHAT_ONEFILE") == "1"
NAME = "Local Chat"  # exe filename. Window title comes from desktop.APP_NAME ("LocalVault AI").
ICON = os.path.join(HERE, "desktop_assets", "icon.ico")
if not os.path.isfile(ICON):
    raise SystemExit(f"missing build input: {ICON}")

datas = [(os.path.join(HERE, f), ".") for f in (
    "index.html", "app.js", "style.css", "favicon.svg", "apple-touch-icon.png",
    "manifest.webmanifest")]
datas += [(os.path.join(HERE, "vendor"), "vendor"), (os.path.join(HERE, "desktop_assets"), "desktop_assets")]
for src, _ in datas:
    if not os.path.exists(src):
        raise SystemExit(f"missing build input: {src}")

version_file = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                     StringTable, VarFileInfo, VarStruct, VSVersionInfo)
    version_file = VSVersionInfo(
        ffi=FixedFileInfo(filevers=(1, 2, 0, 0), prodvers=(1, 2, 0, 0)),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("FileDescription", "LocalVault AI"), StringStruct("ProductName", "LocalVault AI"),
            StringStruct("FileVersion", "1.2.0"), StringStruct("ProductVersion", "1.2.0"),
            StringStruct("OriginalFilename", "Local Chat.exe")])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])])])

a = Analysis(
    [os.path.join(HERE, "desktop.py")],
    pathex=[HERE],
    datas=datas,
    hiddenimports=["server", "lan"],
    # Windows uses pywebview's WinForms/EdgeChromium backend; keep other GUI toolkits out of the bundle.
    excludes=["tkinter", "PIL", "make_icon", "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "gi",
              "cefpython3", "webview.platforms.qt", "webview.platforms.gtk", "webview.platforms.cocoa"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_kw = dict(name=NAME, icon=ICON, console=False, upx=False, debug=False, strip=False,
              version=version_file, disable_windowed_traceback=False)
if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **exe_kw)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **exe_kw)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
