"""Visible LocalVault AI branding, without moving data paths or the exe name."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import desktop  # noqa: E402
import server  # noqa: E402

SUBTITLE = "Your private, local AI assistant."
NAME = "LocalVault AI"
RETIRED = "On" + "yx"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestBrand(unittest.TestCase):
    def test_retired_name_is_gone(self):
        leftovers = []
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "uploads", "__pycache__", ".venv-build", "dist", "build")]
            for name in files:
                if name.endswith((".py", ".js", ".html", ".md", ".bat", ".ps1", ".spec", ".webmanifest", ".sh", ".css")):
                    path = os.path.join(dirpath, name)
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                    if RETIRED in text or RETIRED.lower() in text:
                        leftovers.append(os.path.relpath(path, ROOT))
        self.assertEqual(leftovers, [])

    def test_window_and_data_ids(self):
        self.assertEqual(desktop.APP_NAME, NAME)
        self.assertEqual(desktop.APP_ID, "LocalChat")
        data = desktop.default_data_dir({"LOCALAPPDATA": r"C:\Users\Example\AppData\Local"}, windows=True)
        self.assertTrue(data.replace("\\", "/").endswith("LocalChat/data"))
        self.assertEqual(server.VERSION, "1.2")
        self.assertNotIn(RETIRED, server.__doc__ or "")

    def test_page_manifest_and_subtitle(self):
        html = read("index.html")
        self.assertIn("<title>LocalVault AI</title>", html)
        self.assertIn('apple-mobile-web-app-title" content="LocalVault AI"', html)
        self.assertIn(SUBTITLE, html)
        self.assertNotIn(">Local Chat<", html)
        manifest = json.loads(read("manifest.webmanifest"))
        self.assertEqual(manifest["name"], NAME)
        self.assertEqual(manifest["short_name"], NAME)
        self.assertEqual(manifest["description"], SUBTITLE)
        js = read("app.js")
        self.assertIn(SUBTITLE, js)
        self.assertIn("not encrypted", js)
        self.assertIn("qwen3.5:4b", js)
        self.assertIn("qwen2.5-coder", js)

    def test_packaging_keeps_the_exe_name(self):
        spec = read("LocalChat.spec")
        self.assertIn('NAME = "Local Chat"', spec)
        self.assertIn('StringStruct("ProductName", "LocalVault AI")', spec)
        self.assertIn('StringStruct("FileDescription", "LocalVault AI")', spec)
        self.assertIn('StringStruct("OriginalFilename", "Local Chat.exe")', spec)
        self.assertIn("manifest.webmanifest", spec)

    def test_install_and_firewall_still_find_the_msi_path(self):
        install = read("deploy/Install-LocalChatUpdate.ps1")
        firewall = read("deploy/Enable-LocalChatLanFirewall.ps1")
        disable = read("deploy/Disable-LocalChatLanFirewall.ps1")
        self.assertIn(r"Programs\LocalChat", install)
        self.assertIn(r"Programs\LocalChat", firewall)
        self.assertIn("Local Chat iPhone LAN", firewall)
        self.assertIn('$_.DisplayName -eq "Local Chat iPhone LAN"', disable)
        self.assertNotIn("Set-NetConnectionProfile", install + firewall + disable)
        self.assertIn("LocalVault AI", firewall)
        self.assertIn("11434", firewall)

    def test_shortcut_helper_preserves_the_target(self):
        script = read("deploy/Rename-LocalVaultShortcuts.ps1")
        self.assertIn('$NewLeaf = "LocalVault AI.lnk"', script)
        self.assertIn('$OldLeaf = "Local Chat.lnk"', script)
        self.assertIn("-DryRun", script)
        self.assertIn("$Link.TargetPath = $target", script)
        self.assertIn("Local Chat.exe", script)
        self.assertNotIn(RETIRED, script)
        self.assertNotIn("Remove-Item", script.replace("Remove-Item -LiteralPath $link.FullName -Force", ""))
        self.assertIn(r"Programs\LocalChat\Local Chat.exe", script)

    def test_served_page_uses_the_new_name(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="lc-brand-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        server.set_data_dir(tmp)
        st = desktop.ServerThread(server.make_server("127.0.0.1", 0)).start()
        self.addCleanup(st.stop)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", st.port, timeout=5)
        conn.request("GET", "/", headers={"Host": "127.0.0.1:%d" % st.port})
        page = conn.getresponse().read().decode()
        conn.close()
        self.assertIn("<title>LocalVault AI</title>", page)
        self.assertIn(SUBTITLE, page)
        conn = http.client.HTTPConnection("127.0.0.1", st.port, timeout=5)
        conn.request("GET", "/manifest.webmanifest", headers={"Host": "127.0.0.1:%d" % st.port})
        resp = conn.getresponse()
        body = resp.read().decode()
        ctype = resp.getheader("Content-Type")
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("manifest", ctype)
        self.assertEqual(json.loads(body)["name"], NAME)
        self.assertIn("not a real sandbox", read("README.md"))
        self.assertIn("plain HTTP", read("docs/IPHONE.md"))
        self.assertIn("not an encrypted store", read("README.md"))


if __name__ == "__main__":
    unittest.main()
