"""Windows taskbar icon: multi-resolution .ico, window hook, and shortcut helper."""
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import desktop  # noqa: E402
import make_icon  # noqa: E402

ICO = os.path.join(ROOT, "desktop_assets", "icon.ico")
REQUIRED = (16, 24, 32, 48, 64, 128, 256)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def ico_entries(blob):
    reserved, kind, count = struct.unpack_from("<HHH", blob, 0)
    if reserved != 0 or kind != 1:
        raise AssertionError("not an icon file")
    entries = []
    off = 6
    for _ in range(count):
        w, h, _colors, _res, planes, bits, nbytes, offset = struct.unpack_from("<BBBBHHII", blob, off)
        entries.append({
            "w": 256 if w == 0 else w,
            "h": 256 if h == 0 else h,
            "planes": planes,
            "bits": bits,
            "nbytes": nbytes,
            "offset": offset,
        })
        off += 16
    return entries


def decode_bmp(blob, entry):
    offset = entry["offset"]
    bi_size, bw, bh, planes, bitcount = struct.unpack_from("<IIIHH", blob, offset)
    if bi_size != 40 or bw != entry["w"] or bh != entry["w"] * 2 or bitcount != 32 or planes != 1:
        raise AssertionError("unexpected BMP header for %s" % entry["w"])
    pix = offset + 40
    pixels = []
    for y in range(entry["w"] - 1, -1, -1):
        row = [None] * entry["w"]
        for x in range(entry["w"]):
            b, g, r, a = blob[pix:pix + 4]
            row[x] = (r, g, b, a)
            pix += 4
        pixels.append((y, row))
    pixels.sort()
    return [px for _y, row in pixels for px in row]


class TestIconFile(unittest.TestCase):
    def test_sizes_and_bmp_frames(self):
        with open(ICO, "rb") as fh:
            blob = fh.read()
        entries = ico_entries(blob)
        sizes = [e["w"] for e in entries]
        self.assertEqual(sizes, list(REQUIRED))
        self.assertEqual(make_icon.SIZES, list(REQUIRED))
        for entry in entries:
            chunk = blob[entry["offset"]:entry["offset"] + 8]
            if entry["w"] >= 256:
                self.assertTrue(chunk.startswith(b"\x89PNG\r\n\x1a\n"))
                continue
            self.assertFalse(chunk.startswith(b"\x89PNG"), entry["w"])
            pixels = decode_bmp(blob, entry)
            opaque = [p for p in pixels if p[3] > 200]
            light = [p for p in opaque if p[0] > 200]
            dark = [p for p in opaque if p[0] < 40]
            self.assertGreater(len(light), 20, entry["w"])
            self.assertGreater(len(dark), 20, entry["w"])
        # 16 px center is the dark core, not an empty or Python-blue tile.
        small = next(e for e in entries if e["w"] == 16)
        core = decode_bmp(blob, small)
        self.assertLess(core[7 * 16 + 7][0], 40)
        self.assertGreater(core[5 * 16 + 2][0], 200)

    def test_generator_matches_the_committed_file(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("pillow not installed")
        tmp = tempfile.mkdtemp(prefix="lc-icon-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        make_icon.main(tmp)
        with open(os.path.join(tmp, "icon.ico"), "rb") as fh:
            fresh = fh.read()
        with open(ICO, "rb") as fh:
            committed = fh.read()
        self.assertEqual(fresh, committed)
        self.assertTrue(os.path.isfile(os.path.join(tmp, "icon.png")))


class TestWindowIcon(unittest.TestCase):
    def test_identity_and_hook_order(self):
        self.assertEqual(desktop.APP_NAME, "LocalVault AI")
        self.assertEqual(desktop.APP_ID, "LocalChat")
        self.assertEqual(desktop.APP_USER_MODEL_ID, "LocalChat.Desktop")
        self.assertEqual(desktop.DEFAULT_PORT, 8080)
        self.assertTrue(desktop.app_icon_path().endswith(os.path.join("desktop_assets", "icon.ico")))
        src = read("desktop.py")
        main = src.split("def main(", 1)[1]
        self.assertLess(main.find("set_app_user_model_id()"), main.find("run_ui("))
        self.assertNotIn("events.before_show", src)
        self.assertIn("def bind_window_icon", src)
        self.assertIn('kw["icon"] = self.icon', src)
        self.assertNotIn("SetCurrentProcessExplicitAppUserModelID(\"LocalChat.Desktop\")", src)
        self.assertIn("APP_USER_MODEL_ID", src)

    def test_startup_does_not_register_a_blocking_icon_hook(self):
        class Bucket(object):
            def __init__(self):
                self.handlers = []

            def __iadd__(self, fn):
                self.handlers.append(fn)
                return self

        class Window(object):
            def __init__(self):
                self.events = type("E", (), {})()
                self.events.before_show = Bucket()
                self.events.shown = Bucket()

        window = Window()
        self.assertIsNone(desktop.bind_window_icon(window, ICO))
        self.assertEqual(window.events.before_show.handlers, [])
        self.assertEqual(window.events.shown.handlers, [])

    def test_apply_window_icon_is_windows_only_and_paints_the_form(self):
        self.assertFalse(desktop.apply_window_icon(object(), ICO, windows=False))

        class Native(object):
            def __init__(self):
                self.Icon = None
                self.paths = []

        class Window(object):
            def __init__(self):
                self.native = Native()

        window = Window()
        seen = []

        def loader(path):
            return "loaded:" + os.path.basename(path)

        def setter(native, path):
            seen.append(path)
            return True

        self.assertTrue(desktop.apply_window_icon(
            window, ICO, windows=True, icon_loader=loader, hwnd_setter=setter))
        self.assertEqual(window.native.Icon, "loaded:icon.ico")
        self.assertEqual(seen, [ICO])
        self.assertFalse(desktop.apply_window_icon(
            Window(), os.path.join(ROOT, "missing.ico"), windows=True,
            icon_loader=loader, hwnd_setter=setter))
        bare = Window()
        bare.native = None
        self.assertFalse(desktop.apply_window_icon(
            bare, ICO, windows=True, icon_loader=loader, hwnd_setter=setter))


class TestShortcutHelper(unittest.TestCase):
    def test_helper_only_updates_local_chat_shortcuts(self):
        script = read("deploy/Refresh-LocalVaultTaskbarIcon.ps1")
        self.assertIn('$AppUserModelId = "LocalChat.Desktop"', script)
        self.assertIn("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3", script)
        self.assertIn("key.pid = 5", script)
        self.assertNotIn("file.Load(shortcutPath, 0)", script)
        self.assertIn("const uint STGM_READWRITE = 0x00000002;", script)
        self.assertIn("const uint STGM_SHARE_DENY_NONE = 0x00000040;", script)
        self.assertIn("file.Load(shortcutPath, STGM_READWRITE | STGM_SHARE_DENY_NONE)", script)
        self.assertLess(
            script.find("file.Load(shortcutPath, STGM_READWRITE | STGM_SHARE_DENY_NONE)"),
            script.find("store.SetValue"))
        self.assertLess(
            script.find("WaitForPendingFinalizers"),
            script.find("[LocalVault.ShortcutIcon]::SetAppUserModelId"))
        self.assertIn('ExtendedProperty("System.AppUserModel.ID")', script)
        self.assertIn("System.AppUserModel.ID was not stored", script)
        self.assertNotIn("SetPath", script)
        self.assertIn("$Link.TargetPath = $target", script)
        self.assertIn("$Link.Arguments = $arguments", script)
        self.assertIn("$Link.IconLocation = $IconLocation", script)
        self.assertIn('GetFileName($target) -ne "Local Chat.exe"', script)
        self.assertIn('"icon.ico"', script)
        self.assertIn('"_internal\\desktop_assets\\icon.ico"', script)
        self.assertIn("-DryRun", script)
        self.assertIn("User Pinned\\TaskBar", script)
        self.assertNotIn("Remove-Item", script)
        self.assertNotIn("Stop-Process", script)
        self.assertNotIn("taskkill", script.lower())
        self.assertNotIn("explorer.exe", script.lower())
        self.assertNotIn("New-NetFirewallRule", script)
        self.assertNotIn("Set-NetConnectionProfile", script)
        spec = read("LocalChat.spec")
        self.assertIn("icon=ICON", spec)
        self.assertIn("missing build input: {ICON}", spec)
        bat = read("build_exe.bat")
        self.assertIn('copy /Y "desktop_assets\\icon.ico"', bat)
        docs = read("docs/LOCALVAULT.md")
        self.assertIn("Refresh-LocalVaultTaskbarIcon.ps1", docs)
        self.assertIn("does not restart Explorer", docs)
        self.assertIn("Local Chat.exe", docs)


if __name__ == "__main__":
    unittest.main()
