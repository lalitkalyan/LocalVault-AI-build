"""LAN pairing, auth boundaries, and shutdown tests for iPhone access.

    python -m unittest tests.test_lan -v

These run on this machine. They do not claim a physical iPhone or the MSI was used.
"""
import http.client
import io
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import desktop  # noqa: E402
import lan  # noqa: E402
import server  # noqa: E402

PY = sys.executable


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def private_rows():
    return [row for row in lan.list_private_ipv4() if lan.is_private_ipv4(row["ip"])]


def call(ip, port, method="GET", path="/", body=None, headers=None, timeout=5):
    conn = http.client.HTTPConnection(ip, port, timeout=timeout)
    hdrs = {"Host": "%s:%s" % (ip, port)}
    if body is not None and (not headers or "Content-Type" not in headers):
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    payload = resp.read()
    got = {key: value for key, value in resp.getheaders()}
    conn.close()
    return resp.status, payload, got


def header(headers, name):
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


def refused(ip, port):
    sock = socket.socket()
    sock.settimeout(0.4)
    try:
        sock.connect((ip, port))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


class TestLanPure(unittest.TestCase):
    def setUp(self):
        lan.GATE.clear()

    def test_private_and_virtual_filters(self):
        self.assertTrue(lan.is_private_ipv4("192.168.1.20"))
        self.assertTrue(lan.is_private_ipv4("10.1.2.3"))
        self.assertTrue(lan.is_private_ipv4("172.16.0.1"))
        self.assertFalse(lan.is_private_ipv4("172.15.0.1"))
        self.assertFalse(lan.is_private_ipv4("127.0.0.1"))
        self.assertFalse(lan.is_private_ipv4("8.8.8.8"))
        self.assertFalse(lan.is_private_ipv4("100.64.0.1"))  # CGNAT / Tailscale, not RFC1918
        self.assertFalse(lan.is_private_ipv4("0.0.0.0"))
        self.assertTrue(lan.is_virtual_interface("docker0"))
        self.assertTrue(lan.is_virtual_interface("br-abc"))
        self.assertTrue(lan.is_virtual_interface("vEthernet (WSL)"))
        self.assertTrue(lan.is_virtual_interface("Tailscale"))
        self.assertTrue(lan.is_virtual_interface("tun0"))
        self.assertFalse(lan.is_virtual_interface("Ethernet"))
        self.assertFalse(lan.is_virtual_interface("Wi-Fi"))
        self.assertFalse(lan.is_virtual_interface("enp0s3"))
        self.assertFalse(lan.is_virtual_interface("wlan0"))

    def test_subnet_math(self):
        self.assertTrue(lan.ip_in_subnet("192.168.1.50", "192.168.1.20", 24))
        self.assertFalse(lan.ip_in_subnet("192.168.2.50", "192.168.1.20", 24))
        self.assertFalse(lan.ip_in_subnet("8.8.8.8", "192.168.1.20", 24))
        self.assertFalse(lan.ip_in_subnet("192.168.1.50", "192.168.1.20", 32))
        self.assertEqual(lan.netmask_to_prefix("255.255.255.0"), 24)
        self.assertEqual(lan.netmask_to_prefix("255.255.0.0"), 16)

    def test_this_host_skips_docker_and_loopback(self):
        rows = private_rows()
        if not rows:
            self.skipTest("no private IPv4 on this machine")
        ips = {row["ip"] for row in rows}
        names = {row["name"] for row in rows}
        self.assertNotIn("127.0.0.1", ips)
        self.assertNotIn("172.17.0.1", ips)
        self.assertFalse(any(lan.is_virtual_interface(name) for name in names))
        self.assertTrue(any(row.get("preferred") for row in rows))

    def test_windows_adapter_parser_skips_virtual_nics(self):
        blob = bytearray(800)
        base = 0x200000

        def put_ptr(offset, ptr):
            struct.pack_into("<Q", blob, offset, ptr)

        put_ptr(8, base + 500)
        put_ptr(24, base + 300)
        put_ptr(72, base + 200)
        name = "Wi-Fi".encode("utf-16le") + b"\x00\x00"
        blob[200:200 + len(name)] = name
        put_ptr(300 + 16, base + 400)
        blob[300 + 56] = 24
        struct.pack_into("<H", blob, 400, 2)
        blob[404:408] = socket.inet_aton("192.168.1.20")
        put_ptr(500 + 72, base + 600)
        put_ptr(500 + 24, base + 650)
        docker = "docker0".encode("utf-16le") + b"\x00\x00"
        blob[600:600 + len(docker)] = docker
        put_ptr(650 + 16, base + 700)
        blob[650 + 56] = 16
        struct.pack_into("<H", blob, 700, 2)
        blob[704:708] = socket.inet_aton("172.17.0.1")
        found = lan.parse_windows_adapters(bytes(blob), base, ptr_size=8)
        self.assertEqual(found, [{"ip": "192.168.1.20", "prefix": 24, "name": "Wi-Fi", "preferred": False}])

    def test_code_normalize_and_cookie_shape(self):
        self.assertEqual(lan.normalize_code(" ab2k-7qpm "), "AB2K7QPM")
        self.assertEqual(lan.format_code("AB2K7QPM"), "AB2K-7QPM")
        token = "A" * 32
        cookie = lan.session_cookie(token, 30)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertNotIn("Domain=", cookie)
        with self.assertRaises(lan.LanError):
            lan.session_cookie("bad;drop", 30)
        self.assertEqual(lan.cookie_value("other=1; lc_session=abc; x=y", "lc_session"), "abc")

    def test_session_is_hashed_and_pinned_to_the_phone_address(self):
        token, record = lan.GATE.open_session("192.168.1.20", "iPhone\nextra")
        dumped = json.dumps(lan.GATE.sessions)
        self.assertNotIn(token, dumped)
        self.assertIn(record["token_hash"], dumped)
        self.assertNotIn("\n", record["label"])
        self.assertTrue(lan.GATE.session_ok("lc_session=%s" % token, "192.168.1.20"))
        self.assertFalse(lan.GATE.session_ok("lc_session=%s" % token, "192.168.1.21"))
        self.assertFalse(lan.GATE.session_ok("lc_session=%s" % token, "192.168.1.20"))

    def test_code_burn_and_address_lockout(self):
        issued = lan.GATE.issue_code()
        code = issued["code"]
        self.assertNotIn("?", lan.address_url("192.168.1.20", 8080))
        for _ in range(lan.MAX_CODE_FAILS):
            self.assertEqual(lan.GATE.check_code("WRONG", "192.168.1.20"), "bad")
        self.assertIsNone(lan.GATE.current_code())
        self.assertTrue(lan.GATE.snapshot()["code_burned"])
        self.assertEqual(lan.GATE.check_code(code, "192.168.1.20"), "expired")
        fresh = lan.GATE.issue_code()["code"]
        # The burned attempts already count toward the per-address lock.
        remaining = lan.MAX_IP_FAILS - lan.MAX_CODE_FAILS
        for _ in range(remaining):
            self.assertEqual(lan.GATE.check_code("NOPE", "192.168.1.20"), "bad")
        self.assertEqual(lan.GATE.check_code(fresh, "192.168.1.20"), "locked")
        self.assertIsNotNone(lan.GATE.current_code())  # lock does not reveal or burn via the locked call

    def test_model_rankings_unchanged(self):
        with open(os.path.join(ROOT, "app.js"), encoding="utf-8") as fh:
            text = fh.read()
        for needle in ("qwen3.5:4b", "qwen3.5:2b", "qwen2.5-coder", "better, slower", "fast code fallback"):
            self.assertIn(needle, text)


class TestLanHttp(unittest.TestCase):
    def setUp(self):
        lan.GATE.clear()
        server.disable_lan()
        self.tmp = tempfile.mkdtemp(prefix="lc-lan-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        server.set_data_dir(self.tmp)
        rows = private_rows()
        if not rows:
            self.skipTest("no private IPv4 on this machine")
        self.row = next((row for row in rows if row.get("preferred")), rows[0])
        self.ip = self.row["ip"]
        self.port = free_port()
        self.opened = []
        self.token = "desktop-token"
        self.handler = desktop.make_handler(server, self.token, lambda: self.opened.append(1))
        self.st = desktop.ServerThread(
            server.make_server("127.0.0.1", self.port, "http://127.0.0.1:1", handler=self.handler)).start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        server.disable_lan()
        try:
            self.st.stop()
        except Exception:
            pass

    def origin(self):
        return {"Origin": "http://%s:%s" % (self.ip, self.port)}

    def enable(self):
        return server.enable_lan(self.port, acknowledge=True, addresses=[dict(self.row)], handler=self.handler)

    def test_loopback_default_does_not_listen_on_the_lan_address(self):
        self.assertTrue(refused(self.ip, self.port))
        status, body, _ = call("127.0.0.1", self.port, path="/health")
        self.assertEqual(status, 200)
        self.assertIn(b"127.0.0.1:1", body)
        self.assertEqual(call("127.0.0.1", self.port, "PUT", "/store/chats/keep", {"title": "kept"})[0], 200)

    def test_enable_requires_acknowledgement_and_rejects_wildcards(self):
        with self.assertRaises(server.LanError):
            server.enable_lan(self.port, acknowledge=False, addresses=[dict(self.row)])
        self.assertTrue(refused(self.ip, self.port))
        with self.assertRaises(server.LanError):
            server.enable_lan(self.port, acknowledge=True, addresses=[{"ip": "0.0.0.0", "prefix": 24, "name": "any"}])
        with self.assertRaises(server.LanError):
            server.enable_lan(self.port, acknowledge=True, addresses=[{"ip": "8.8.8.8", "prefix": 24, "name": "public"}])
        with self.assertRaises(server.LanError):
            server.enable_lan(11434, acknowledge=True, addresses=[dict(self.row)])
        server.CONFIG["ollama"] = "http://10.1.1.1:11434"
        try:
            with self.assertRaises(server.LanError):
                server.enable_lan(self.port, acknowledge=True, addresses=[dict(self.row)])
        finally:
            server.CONFIG["ollama"] = "http://127.0.0.1:1"
        self.assertTrue(refused(self.ip, self.port))

    def test_unauthenticated_lan_cannot_read_or_run_anything(self):
        info = self.enable()
        code = info["code"]
        self.assertNotIn("?", info["addresses"][0]["url"])
        self.assertNotIn(code, info["addresses"][0]["url"])
        # The socket we bound is the private address, not 0.0.0.0.
        self.assertEqual(self.st.srv.socket.getsockname()[0], "127.0.0.1")
        lan_sock = server._lan_listeners[0][0].socket.getsockname()
        self.assertEqual(lan_sock[0], self.ip)
        self.assertNotEqual(lan_sock[0], "0.0.0.0")

        title = "secret-title"
        self.assertEqual(call("127.0.0.1", self.port, "PUT", "/store/chats/hid", {"title": title, "messages": []})[0], 200)
        probes = [
            ("GET", "/store/chats", None),
            ("GET", "/store/chats/hid", None),
            ("GET", "/store/settings", None),
            ("GET", "/health", None),
            ("GET", "/api/tags", None),
            ("POST", "/api/chat", {"model": "x", "messages": []}),
            ("POST", "/tools/search", {"query": "secret query"}),
            ("POST", "/tools/run", {"code": "print('ran-ok')"}),
            ("PUT", "/store/chats/hid", {"title": "pwn"}),
            ("DELETE", "/store/chats/hid", {}),
        ]
        with mock.patch.object(server, "do_search", side_effect=AssertionError("search must not run")):
            for method, path, body in probes:
                headers = self.origin() if method != "GET" else None
                status, payload, hdrs = call(self.ip, self.port, method, path, body, headers=headers)
                self.assertIn(status, (401, 403), (method, path, status, payload))
                text = payload.decode("utf-8", "replace")
                self.assertNotIn(title, text)
                self.assertNotIn("ran-ok", text)
                self.assertNotIn("11434", text)
                self.assertNotIn("secret query", text)
                if path == "/health":
                    self.assertNotIn("ollama", text.lower())
                self.assertNotIn("access-control-allow-origin", {k.lower() for k in hdrs})
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "chats", "hid.json")))
        with open(os.path.join(self.tmp, "chats", "hid.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["title"], title)
        # Static UI is reachable so the phone can show the pairing screen. It has no chats in it.
        status, page, _ = call(self.ip, self.port, path="/")
        self.assertEqual(status, 200)
        self.assertIn(b"Pair this iPhone", page)
        self.assertNotIn(title.encode(), page)
        self.assertEqual(call(self.ip, self.port, path="/apple-touch-icon.png")[0], 200)

    def test_hostile_host_origin_and_query_secrets(self):
        info = self.enable()
        code = info["code"]
        buf = io.StringIO()
        with mock.patch.object(sys, "stderr", buf):
            status, _, _ = call(self.ip, self.port, path="/?code=%s&token=sekret" % code, headers={"Host": "%s:%s" % (self.ip, self.port)})
            self.assertEqual(status, 403)
            status, payload, _ = call(self.ip, self.port, path="/store/chats", headers={"Host": "evil.example:%s" % self.port})
            self.assertEqual(status, 403)
            self.assertNotIn(b"chats", payload.lower())
            status, _, _ = call(self.ip, self.port, path="/health", headers={"Host": "127.0.0.1:%s" % self.port})
            self.assertEqual(status, 403)
            status, _, _ = call(self.ip, self.port, path="/health", headers={"Host": "0.0.0.0:%s" % self.port})
            self.assertEqual(status, 403)
            status, body, hdrs = call(self.ip, self.port, "POST", "/lan/pair", {"code": code}, headers=self.origin())
            self.assertEqual(status, 200, body)
            self.assertNotIn(code.encode(), body)
            token = header(hdrs, "Set-Cookie")
            self.assertIn("HttpOnly", token)
            self.assertNotIn("Secure", token)
            self.assertNotIn(code, token)
            raw = token.split(";", 1)[0].split("=", 1)[1]
            self.assertNotIn(raw.encode(), body)
            status, payload, _ = call(
                self.ip, self.port, "POST", "/tools/run", {"code": "print('ran-ok')"},
                headers={"Origin": "https://evil.example", "Cookie": "lc_session=%s" % raw,
                         "Content-Type": "application/json", "Host": "%s:%s" % (self.ip, self.port)})
            self.assertEqual(status, 403)
            self.assertNotIn(b"ran-ok", payload)
            status, payload, _ = call(
                self.ip, self.port, "POST", "/tools/run", b"code=print('ran-ok')",
                headers={"Origin": "http://%s:%s" % (self.ip, self.port), "Cookie": "lc_session=%s" % raw,
                         "Content-Type": "text/plain", "Host": "%s:%s" % (self.ip, self.port)})
            self.assertEqual(status, 415)
            self.assertNotIn(b"ran-ok", payload)
            log = buf.getvalue()
        self.assertNotIn(code, log)
        self.assertNotIn(code.replace("-", ""), log)
        self.assertNotIn(raw, log)
        self.assertNotIn("sekret", log)

    def test_pair_then_revoke_and_admin_stays_on_the_pc(self):
        info = self.enable()
        code = info["code"]
        # Wrong content-type must not burn the code.
        status, _, _ = call(self.ip, self.port, "POST", "/lan/pair", b"{}", headers={
            "Origin": "http://%s:%s" % (self.ip, self.port), "Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _, _ = call(self.ip, self.port, "POST", "/lan/pair", {"code": code})  # no Origin
        self.assertEqual(status, 403)
        status, body, hdrs = call(self.ip, self.port, "POST", "/lan/pair", {"code": code, "label": "iPhone"},
                                  headers=self.origin())
        self.assertEqual(status, 200, body)
        raw = header(hdrs, "Set-Cookie").split(";", 1)[0].split("=", 1)[1]
        self.assertNotIn(raw, json.dumps(lan.GATE.sessions))
        # Same code does not work twice.
        again, _, _ = call(self.ip, self.port, "POST", "/lan/pair", {"code": code}, headers=self.origin())
        self.assertEqual(again, 401)
        cookie = {"Cookie": "lc_session=%s" % raw, "Origin": "http://%s:%s" % (self.ip, self.port)}
        status, payload, _ = call(self.ip, self.port, path="/store/chats", headers={"Cookie": cookie["Cookie"],
                                                                                   "Host": "%s:%s" % (self.ip, self.port)})
        self.assertEqual(status, 200, payload)
        status, payload, _ = call(self.ip, self.port, "POST", "/tools/run", {"code": "print('ran-ok')"}, headers=cookie)
        self.assertEqual(status, 200, payload)
        self.assertIn(b"ran-ok", payload)
        with mock.patch.object(server, "do_search", return_value=[{"title": "t", "url": "http://example.com", "snippet": "s"}]):
            status, payload, _ = call(self.ip, self.port, "POST", "/tools/search", {"query": "x"}, headers=cookie)
        self.assertEqual(status, 200, payload)
        # Paired phone still cannot enable, read the code, or open a desktop window.
        for method, path, body in (
            ("POST", "/lan/enable", {"acknowledge": True}),
            ("GET", "/lan/code", None),
            ("POST", "/lan/disable", {}),
            ("POST", "/__desktop/open", {}),
        ):
            headers = dict(cookie)
            headers["X-LocalChat-Token"] = self.token
            status, payload, _ = call(self.ip, self.port, method, path, body, headers=headers)
            self.assertIn(status, (403, 405), (path, status, payload))
            self.assertNotIn(code.encode(), payload)
        self.assertEqual(self.opened, [])
        # Loopback can still open a window and can revoke the phone.
        status, _, _ = call("127.0.0.1", self.port, "POST", "/__desktop/open", {},
                            headers={"X-LocalChat-Token": self.token})
        self.assertEqual(status, 200)
        self.assertEqual(self.opened, [1])
        sessions = json.loads(call("127.0.0.1", self.port, path="/lan/status")[1])["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(call("127.0.0.1", self.port, "POST", "/lan/revoke", {"id": sessions[0]["id"]})[0], 200)
        status, payload, _ = call(self.ip, self.port, path="/store/chats", headers={"Cookie": cookie["Cookie"]})
        self.assertEqual(status, 401, payload)
        # Path traversal stays closed on both listeners.
        for path in ("/vendor/../server.py", "/vendor/%2e%2e/server.py", "/server.py", "/desktop.py", "/lan.py"):
            status, payload, _ = call(self.ip, self.port, path=path)
            self.assertIn(status, (401, 403, 404), path)
            self.assertNotIn(b"def run_python", payload)
        self.assertEqual(call("127.0.0.1", self.port, path="/vendor/marked.umd.js")[0], 200)

    def test_disable_closes_the_lan_socket_and_forgets_secrets(self):
        info = self.enable()
        code = info["code"]
        status, _, hdrs = call(self.ip, self.port, "POST", "/lan/pair", {"code": code}, headers=self.origin())
        self.assertEqual(status, 200)
        raw = header(hdrs, "Set-Cookie").split(";", 1)[0].split("=", 1)[1]
        self.assertTrue(server.lan_is_on())
        server.disable_lan()
        self.assertFalse(server.lan_is_on())
        self.assertTrue(refused(self.ip, self.port))
        self.assertEqual(call("127.0.0.1", self.port, path="/health")[0], 200)
        self.assertIsNone(lan.GATE.current_code())
        self.assertEqual(lan.GATE.sessions, {})
        blob = b""
        for dirpath, _, files in os.walk(self.tmp):
            for name in files:
                with open(os.path.join(dirpath, name), "rb") as fh:
                    blob += fh.read()
        self.assertNotIn(code.encode(), blob)
        self.assertNotIn(raw.encode(), blob)

    def test_cli_refuses_wildcard_and_public_bind(self):
        for host, needle in (("0.0.0.0", "all interfaces"), ("8.8.8.8", "public")):
            proc = subprocess.run([PY, os.path.join(ROOT, "server.py"), "--host", host, "--port", "9"],
                                  capture_output=True, text=True, timeout=15)
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn(needle, (proc.stdout + proc.stderr).lower())


class TestLanProcess(unittest.TestCase):
    def test_desktop_lan_shutdown_revokes_and_does_not_log_the_code(self):
        rows = private_rows()
        if not rows:
            self.skipTest("no private IPv4 on this machine")
        ip = next((row["ip"] for row in rows if row.get("preferred")), rows[0]["ip"])
        tmp = tempfile.mkdtemp(prefix="lc-lan-proc-")
        self.addCleanup(shutil.rmtree, tmp, True)
        port = free_port()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HOME=tmp, USERPROFILE=tmp)
        env.pop("HOMEDRIVE", None)
        env.pop("HOMEPATH", None)
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            [PY, os.path.join(ROOT, "desktop.py"), "--port", str(port), "--ui", "none", "--no-ollama",
             "--lan", "--log", "--data", tmp, "--ollama", "http://127.0.0.1:9"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
        self.addCleanup(proc.kill)
        self.assertTrue(wait_for(lambda: desktop.probe_app(port), 15), "desktop server did not start")
        self.assertTrue(wait_for(lambda: not refused(ip, port), 10), "LAN listener did not open")
        status, body, _ = call("127.0.0.1", port, path="/lan/code")
        self.assertEqual(status, 200, body)
        code = json.loads(body)["code"]
        self.assertTrue(code)
        paired, _, hdrs = call(ip, port, "POST", "/lan/pair", {"code": code, "label": "iPhone"},
                               headers={"Origin": "http://%s:%s" % (ip, port)})
        self.assertEqual(paired, 200)
        token = header(hdrs, "Set-Cookie").split(";", 1)[0].split("=", 1)[1]
        self.assertEqual(call(ip, port, path="/store/chats", headers={"Cookie": "lc_session=" + token})[0], 200)
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
        self.assertEqual(proc.wait(15), 0)
        log_path = os.path.join(tmp, "localchat.log")
        with open(log_path, encoding="utf-8") as fh:
            log = fh.read()
        self.assertIn("LAN access stopped; paired phones revoked; iPhone listener closed", log)
        self.assertNotIn(code, log)
        self.assertNotIn(code.replace("-", ""), log)
        self.assertNotIn(token, log)
        self.assertFalse(desktop.probe_app(port))
        self.assertTrue(refused(ip, port))
        self.assertIsNone(desktop.read_instance(tmp))
        for dirpath, _, files in os.walk(tmp):
            for name in files:
                with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                self.assertNotIn(code, text, name)
                self.assertNotIn(token, text, name)


class TestDeployScripts(unittest.TestCase):
    def text(self, name):
        with open(os.path.join(ROOT, "deploy", name), encoding="utf-8") as fh:
            return fh.read()

    def test_firewall_scripts_are_private_subnet_and_reversible(self):
        enable = self.text("Enable-LocalChatLanFirewall.ps1")
        disable = self.text("Disable-LocalChatLanFirewall.ps1")
        install = self.text("Install-LocalChatUpdate.ps1")
        for script in (enable, disable, install):
            self.assertNotIn("Set-NetConnectionProfile", script)
            lowered = script.lower()
            self.assertNotIn("networkcategory = 'private'", lowered)
            self.assertNotIn("networkcategory='private'", lowered)
        self.assertIn("-Profile Private", enable)
        self.assertIn("LocalSubnet", enable)
        self.assertIn("-Program", enable)
        self.assertIn("-LocalPort", enable)
        self.assertIn("-Direction Inbound", enable)
        self.assertIn("-Action Allow", enable)
        self.assertIn("-Protocol TCP", enable)
        self.assertIn("Local Chat iPhone LAN", enable)
        self.assertIn("Refusing to open the Ollama port", enable)
        self.assertNotIn("-Profile Public", enable)
        self.assertNotIn("-Profile Any", enable)
        self.assertNotIn("0.0.0.0/0", enable)
        self.assertNotIn("RemoteAddress Any", enable)
        self.assertNotIn("-LocalPort 11434", enable)
        self.assertNotIn("ollama.exe", enable.lower())
        self.assertIn("Remove-NetFirewallRule", disable)
        self.assertNotIn("New-NetFirewallRule", disable)
        self.assertIn('$_.DisplayName -eq "Local Chat iPhone LAN"', disable)
        self.assertNotIn("New-NetFirewallRule", install)
        self.assertNotIn("11434", install)
        self.assertIn("backups", install)
        self.assertIn("-DryRun", enable)
        self.assertIn("-DryRun", install)
        self.assertNotIn("Remove-Item", install)


if __name__ == "__main__":
    unittest.main()
