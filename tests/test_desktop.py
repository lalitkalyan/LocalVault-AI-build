"""Linux-runnable tests for desktop.py and the server.py changes (v1.1).

    python -m unittest discover -s tests -v
"""
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import desktop  # noqa: E402
import server  # noqa: E402

PY = sys.executable


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def req(port, method="GET", path="/", body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{port}"}
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


def write_chat(d, cid, updated, title="t"):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, cid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": cid, "title": title, "created": updated, "updated": updated, "messages": []}, f)


def now_ms():
    return int(time.time() * 1000)


def popen_group():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def ask_stop(proc):
    """Ask a child to exit. Windows rejects signal.SIGINT on a normal Popen."""
    if os.name == "nt":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGINT)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lc-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = dict(os.environ, HOME=self.tmp, USERPROFILE=self.tmp,
                        LOCALCHAT_DATA=os.path.join(self.tmp, "appdata"),
                        PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("LOCALCHAT_LEGACY_DATA", None)
        self.env.pop("HOMEDRIVE", None)
        self.env.pop("HOMEPATH", None)


# ---------------------------------------------------------------- server.py
class TestServerStandalone(Tmp):
    def test_python_server_py_still_works(self):
        port, data = free_port(), os.path.join(self.tmp, "d")
        p = subprocess.Popen([PY, os.path.join(ROOT, "server.py"), "--port", str(port), "--data", data],
                             cwd=ROOT, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             **popen_group())
        self.addCleanup(p.kill)
        self.assertTrue(wait_for(lambda: desktop.probe_app(port)))
        st, body = req(port, path="/")
        self.assertEqual(st, 200)
        self.assertIn(b"<html", body.lower())
        self.assertEqual(req(port, path="/vendor/marked.umd.js")[0], 200)
        self.assertEqual(req(port, "PUT", "/store/chats/abc", {"title": "x"})[0], 200)
        self.assertTrue(os.path.isfile(os.path.join(data, "chats", "abc.json")))
        ask_stop(p)
        out, _ = p.communicate(timeout=5)
        self.assertIn("v1.3", out)
        self.assertIn(data, out)

    def test_default_data_dir_is_next_to_server_py(self):
        out = subprocess.check_output([PY, "-c", "import server; print(server.DATA_DIR); print(server.APP_DIR)"],
                                      cwd=ROOT, env=self.env, text=True).split("\n")
        self.assertEqual(out[0], os.path.join(ROOT, "data"))
        self.assertEqual(out[1], ROOT)


class TestStderrNone(Tmp):
    def test_log_message_without_stderr_does_not_crash(self):
        server.set_data_dir(os.path.join(self.tmp, "d"))
        st = desktop.ServerThread(server.make_server("127.0.0.1", 0)).start()
        self.addCleanup(st.stop)
        with mock.patch.object(sys, "stderr", None):
            for _ in range(3):
                self.assertEqual(req(st.port, path="/health")[0], 200)
            self.assertEqual(req(st.port, path="/nope")[0], 404)
        self.assertTrue(st.thread.is_alive())

    def test_setup_logging_redirects_none_streams_to_file(self):
        data = os.path.join(self.tmp, "d")
        code = textwrap.dedent(f"""
            import sys; sys.path.insert(0, {ROOT!r})
            sys.stdout = sys.stderr = None          # what pythonw / a windowed exe gives you
            import desktop, server
            print(desktop.setup_logging({data!r}))  # goes to the log file
            server.set_data_dir({data!r})
            st = desktop.ServerThread(server.make_server('127.0.0.1', 0)).start()
            import urllib.request
            urllib.request.urlopen('http://127.0.0.1:%d/health' % st.port).read()
            desktop.log('hello-from-log'); st.stop()
        """)
        r = subprocess.run([PY, "-c", code], env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(data, "localchat.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("hello-from-log", log)
        self.assertIn('"GET /health HTTP/1.1" 200', log)

    def test_log_rotation(self):
        data = os.path.join(self.tmp, "d")
        os.makedirs(data)
        with open(os.path.join(data, "localchat.log"), "w") as f:
            f.write("x" * (desktop.LOG_MAX_BYTES + 1))
        old = sys.stdout, sys.stderr
        try:
            path = desktop.setup_logging(data, force=True)
            sys.stderr.close()
        finally:
            sys.stdout, sys.stderr = old
        self.assertTrue(os.path.isfile(path + ".1"))
        self.assertLess(os.path.getsize(path), 100)


# ---------------------------------------------------------------- paths
class TestPaths(unittest.TestCase):
    def test_windows_default(self):
        d = desktop.default_data_dir({"LOCALAPPDATA": r"C:\Users\Example\AppData\Local"}, windows=True)
        self.assertTrue(d.startswith(r"C:\Users\Example\AppData\Local"))
        self.assertTrue(d.replace("\\", "/").endswith("LocalChat/data"))

    def test_linux_default_and_override(self):
        self.assertEqual(desktop.default_data_dir({"XDG_DATA_HOME": "/x"}, windows=False),
                         os.path.join("/x", "LocalChat", "data"))
        self.assertEqual(desktop.default_data_dir({"LOCALCHAT_DATA": "/y/z"}, windows=False), "/y/z")


class TestFrozen(Tmp):
    """Simulate PyInstaller: sys.frozen + sys._MEIPASS pointing at an extraction dir with the static files."""

    def test_frozen_resolution(self):
        mei = os.path.join(self.tmp, "_MEI12345")
        os.makedirs(os.path.join(mei, "vendor"))
        with open(os.path.join(mei, "index.html"), "w") as f:
            f.write("<html>FROM-MEIPASS</html>")
        with open(os.path.join(mei, "vendor", "x.js"), "w") as f:
            f.write("//v")
        exe_dir = os.path.join(self.tmp, "install")
        os.makedirs(exe_dir)
        code = textwrap.dedent(f"""
            import sys, json, urllib.request
            sys.path.insert(0, {ROOT!r})
            sys.frozen = True; sys._MEIPASS = {mei!r}
            sys.executable = {os.path.join(exe_dir, 'Local Chat')!r}
            import desktop, server
            server.set_data_dir(desktop.default_data_dir())
            st = desktop.ServerThread(server.make_server('127.0.0.1', 0)).start()
            u = 'http://127.0.0.1:%d' % st.port
            out = dict(app_dir=server.APP_DIR, res=desktop.resource_dir(), inst=desktop.install_dir(),
                       data=server.DATA_DIR, index=urllib.request.urlopen(u + '/').read().decode(),
                       vendor=urllib.request.urlopen(u + '/vendor/x.js').read().decode(),
                       py=server.python_command(), run=server.run_python('print(6*7)'))
            st.stop(); print(json.dumps(out))
        """)
        r = subprocess.run([PY, "-c", code], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(o["app_dir"], mei)
        self.assertEqual(o["res"], mei)
        self.assertEqual(o["inst"], exe_dir)
        self.assertEqual(o["data"], self.env["LOCALCHAT_DATA"])
        self.assertFalse(o["data"].startswith(mei))
        self.assertIn("FROM-MEIPASS", o["index"])
        self.assertEqual(o["vendor"], "//v")
        self.assertNotIn("Local Chat", o["py"][0])  # code runner must not re-launch the app exe
        self.assertEqual(o["run"]["stdout"].strip(), "42")


# ---------------------------------------------------------------- migration
class TestMigration(Tmp):
    def setUp(self):
        super().setUp()
        self.legacy = os.path.join(self.tmp, "local-chat-app", "data")
        self.data = os.path.join(self.tmp, "appdata")
        write_chat(os.path.join(self.legacy, "chats"), "old1", now_ms() - 1000, "legacy one")
        write_chat(os.path.join(self.legacy, "chats"), "both", now_ms(), "legacy newer")
        write_chat(os.path.join(self.data, "chats"), "both", now_ms() - 5000, "app older")
        with open(os.path.join(self.legacy, "chats", "notes.txt"), "w") as f:
            f.write("ignore me")
        with open(os.path.join(self.legacy, "settings.json"), "w") as f:
            json.dump({"model": "qwen3:4b"}, f)

    def test_discovery_finds_home_install_and_excludes_target(self):
        isolated = os.path.join(self.tmp, "no-install")
        env = {"HOME": self.tmp, "USERPROFILE": self.tmp,
               "LOCALCHAT_LEGACY_DATA": self.data + os.pathsep + self.legacy}
        with mock.patch.object(desktop, "install_dir", return_value=isolated):
            srcs = desktop.legacy_data_dirs(self.data, env=env)
        self.assertEqual([os.path.realpath(s) for s in srcs], [os.path.realpath(self.legacy)])

    def test_migrate_once_newer_wins_and_sources_untouched(self):
        before = sorted(os.listdir(os.path.join(self.legacy, "chats")))
        st = desktop.migrate_legacy(self.data, [self.legacy])
        self.assertEqual((st["copied"], st["updated"], st["settings"]), (1, 1, True))
        chats = os.path.join(self.data, "chats")
        self.assertEqual(sorted(os.listdir(chats)), ["both.json", "old1.json"])
        with open(os.path.join(chats, "both.json")) as f:
            self.assertEqual(json.load(f)["title"], "legacy newer")
        self.assertEqual(sorted(os.listdir(os.path.join(self.legacy, "chats"))), before)
        # user deletes a migrated chat in the app -> must not come back on next launch
        os.remove(os.path.join(chats, "old1.json"))
        st2 = desktop.migrate_legacy(self.data, [self.legacy])
        self.assertEqual(st2["sources"], [])
        self.assertFalse(os.path.exists(os.path.join(chats, "old1.json")))
        # --remigrate re-imports
        st3 = desktop.migrate_legacy(self.data, [self.legacy], force=True)
        self.assertEqual(st3["copied"], 1)

    def test_server_lists_migrated_chats(self):
        desktop.migrate_legacy(self.data, [self.legacy])
        server.set_data_dir(self.data)
        st = desktop.ServerThread(server.make_server("127.0.0.1", 0)).start()
        self.addCleanup(st.stop)
        ids = {c["id"] for c in json.loads(req(st.port, path="/store/chats")[1])["chats"]}
        self.assertEqual(ids, {"old1", "both"})
        self.assertEqual(json.loads(req(st.port, path="/store/settings")[1]), {"model": "qwen3:4b"})


# ---------------------------------------------------------------- single instance
class TestSingleInstance(Tmp):
    def setUp(self):
        super().setUp()
        self.data = os.path.join(self.tmp, "appdata")
        server.set_data_dir(self.data)
        self.opened = []
        self.token = "tok123"
        h = desktop.make_handler(server, self.token, lambda: self.opened.append(1))
        self.port = free_port()
        self.st, port = desktop.acquire_server(server, self.port, "http://127.0.0.1:9", h, self.data)
        self.assertEqual(port, self.port)
        self.addCleanup(self.st.stop)
        desktop.write_instance(self.data, self.port, self.token)

    def test_handoff_opens_window_in_running_instance(self):
        self.assertEqual(desktop.handoff(self.data), self.port)
        self.assertEqual(self.opened, [1])

    def test_handoff_rejects_bad_token_and_foreign_host(self):
        self.assertEqual(req(self.port, "POST", "/__desktop/open", {}, {"X-LocalChat-Token": "bad"})[0], 403)
        self.assertEqual(req(self.port, "POST", "/__desktop/open", {}, {"X-LocalChat-Token": self.token},
                              host="evil.example:8080")[0], 403)
        self.assertEqual(req(self.port, "GET", "/__desktop/open")[0], 405)
        self.assertEqual(self.opened, [])

    def test_second_acquire_hands_off_instead_of_binding(self):
        srv, port = desktop.acquire_server(server, self.port, "http://127.0.0.1:9", server.Handler, self.data)
        self.assertIsNone(srv)
        self.assertEqual(port, -self.port)
        self.assertEqual(self.opened, [1])

    def test_plain_server_on_port_is_reused_unowned(self):
        os.remove(os.path.join(self.data, desktop.INSTANCE_FILE))  # looks like `python server.py`
        srv, port = desktop.acquire_server(server, self.port, "http://127.0.0.1:9", server.Handler, self.data)
        self.assertEqual((srv, port), (None, self.port))

    def test_stop_frees_port_and_clears_instance(self):
        self.st.stop()
        desktop.clear_instance(self.data, self.token)
        self.assertIsNone(desktop.read_instance(self.data))
        self.assertFalse(desktop.probe_app(self.port))
        s = server.make_server("127.0.0.1", self.port)  # port is bindable again
        s.server_close()


class TestForeignPort(Tmp):
    def test_foreign_program_on_port_falls_back_to_free_port(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        busy = blocker.getsockname()[1]
        data = os.path.join(self.tmp, "appdata")
        server.set_data_dir(data)
        srv, port = desktop.acquire_server(server, busy, "http://127.0.0.1:9", server.Handler, data, retries=2)
        self.addCleanup(srv.stop)
        self.assertNotEqual(port, busy)
        self.assertEqual(server.CONFIG["port"], port)  # Host check uses the real port
        self.assertEqual(req(port, path="/health")[0], 200)


class TestDeferredOpen(unittest.TestCase):
    def test_queued_until_ui_attached(self):
        calls = []
        ui = mock.Mock(open_another=lambda: calls.append(1))
        d = desktop.DeferredOpen()
        d()
        d()
        self.assertEqual(calls, [])
        d.attach(ui)
        self.assertEqual(calls, [1, 1])
        d()
        self.assertEqual(calls, [1, 1, 1])


# ---------------------------------------------------------------- Ollama + UI fallbacks
FAKE_OLLAMA = textwrap.dedent("""\
    #!{py}
    import os, sys, json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    assert sys.argv[1:] == ["serve"], sys.argv
    host, port = os.environ["OLLAMA_HOST"].rsplit(":", 1)
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            b = json.dumps({{"version": "fake"}}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)
        def log_message(self, *a): pass
    HTTPServer((host, int(port)), H).serve_forever()
""")


class TestOllama(Tmp):
    def test_start_hidden_and_stop(self):
        exe = os.path.join(self.tmp, "ollama")
        with open(exe, "w") as f:
            f.write(FAKE_OLLAMA.format(py=PY))
        os.chmod(exe, 0o755)
        url = f"http://127.0.0.1:{free_port()}"
        self.assertFalse(desktop.ollama_alive(url))
        with mock.patch.dict(os.environ, {"LOCALCHAT_OLLAMA_EXE": exe}):
            self.assertEqual(desktop.find_ollama(), exe)
            proc = desktop.start_ollama(url, self.tmp, wait=10)
        self.assertIsNotNone(proc)
        self.assertTrue(desktop.ollama_alive(url))
        self.assertIsNone(desktop.start_ollama(url, self.tmp))  # already running -> don't start another
        desktop.stop_process_tree(proc)
        self.assertIsNotNone(proc.poll())
        self.assertTrue(wait_for(lambda: not desktop.ollama_alive(url), 5))

    def test_windows_install_path_is_found(self):
        la = os.path.join(self.tmp, "Local")
        exe = os.path.join(la, "Programs", "Ollama", "ollama.exe")
        os.makedirs(os.path.dirname(exe))
        open(exe, "w").close()
        self.assertEqual(desktop.find_ollama({"LOCALAPPDATA": la}), exe)

    def test_remote_ollama_not_started(self):
        with mock.patch.object(desktop, "find_ollama", side_effect=AssertionError("must not look")):
            self.assertIsNone(desktop.start_ollama("http://10.255.255.1:11434", self.tmp, wait=1))


class TestUI(Tmp):
    def test_edge_command_and_wait(self):
        argf = os.path.join(self.tmp, "args.json")
        exe = os.path.join(self.tmp, "msedge")
        with open(exe, "w") as f:
            f.write(f"#!{PY}\nimport sys, json, time\njson.dump(sys.argv[1:], open({argf!r}, 'w'))\ntime.sleep(3.2)\n")
        os.chmod(exe, 0o755)
        ui = desktop.EdgeAppUI("http://127.0.0.1:8080/", self.tmp, exe)
        t = time.time()
        ui.run()  # returns when the "window" (process) exits
        self.assertGreater(time.time() - t, 3)
        with open(argf) as f:
            args = json.load(f)
        self.assertIn("--app=http://127.0.0.1:8080/", args)
        self.assertIn("--user-data-dir=" + os.path.join(self.tmp, "edge-profile"), args)

    def test_find_edge_windows_paths(self):
        pf = os.path.join(self.tmp, "PF86")
        exe = os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe")
        os.makedirs(os.path.dirname(exe))
        open(exe, "w").close()
        self.assertEqual(desktop.find_edge({"ProgramFiles(x86)": pf}), exe)

    def test_fallback_chain(self):
        order = []

        class Fail:
            name = "x"

            def __init__(self, *a):
                order.append(type(self).__name__)

            def run(self):
                raise RuntimeError("no gui")

        class OkBrowser(Fail):
            def run(self):
                pass

        with mock.patch.object(desktop, "WebviewUI", type("WebviewUI", (Fail,), {})), \
                mock.patch.object(desktop, "EdgeAppUI", type("EdgeAppUI", (Fail,), {})), \
                mock.patch.object(desktop, "find_edge", return_value="/bin/true"), \
                mock.patch.object(desktop, "BrowserUI", type("BrowserUI", (OkBrowser,), {})):
            got = desktop.run_ui("http://127.0.0.1:1/", self.tmp, "auto", desktop.DeferredOpen())
        self.assertEqual(got, "browser")
        self.assertEqual(order, ["WebviewUI", "EdgeAppUI", "BrowserUI"])

    def test_real_pywebview_failure_is_caught(self):
        """With pywebview installed but no usable GUI backend (Linux box, no GTK/Qt/display),
        run_ui('webview') must log and return None instead of crashing; 'auto' would then try Edge."""
        if os.name == "nt":
            self.skipTest("Windows has a real WebView2 window; this checks a headless failure only")
        try:
            import webview  # noqa: F401
        except ImportError:
            self.skipTest("pywebview not installed")
        env = {k: v for k, v in self.env.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        code = (f"import sys; sys.path.insert(0, {ROOT!r}); import desktop; "
                f"print('RESULT', desktop.run_ui('http://127.0.0.1:1/', {self.tmp!r}, 'webview', "
                f"desktop.DeferredOpen()))")
        r = subprocess.run([PY, "-c", code], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("RESULT None", r.stdout)
        self.assertIn("UI 'webview' failed", r.stderr)


# ---------------------------------------------------------------- end to end (subprocesses)
class TestEndToEnd(Tmp):
    def test_headless_primary_second_launch_hands_off_and_shutdown(self):
        port = free_port()
        exe = os.path.join(self.tmp, "ollama")
        with open(exe, "w") as f:
            f.write(FAKE_OLLAMA.format(py=PY))
        os.chmod(exe, 0o755)
        oport = free_port()
        env = dict(self.env, LOCALCHAT_OLLAMA_EXE=exe)
        legacy = os.path.join(self.tmp, "local-chat-app", "data")  # ~/local-chat-app/data (HOME=tmp)
        write_chat(os.path.join(legacy, "chats"), "legacychat", now_ms())
        args = [PY, os.path.join(ROOT, "desktop.py"), "--port", str(port), "--ollama", f"http://127.0.0.1:{oport}"]
        p1 = subprocess.Popen(args + ["--ui", "none", "--log"], env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, **popen_group())
        self.addCleanup(p1.kill)
        data = env["LOCALCHAT_DATA"]
        self.assertTrue(wait_for(lambda: desktop.probe_app(port) and desktop.read_instance(data), 15))
        self.assertTrue(wait_for(lambda: desktop.ollama_alive(f"http://127.0.0.1:{oport}"), 15))
        ids = {c["id"] for c in json.loads(req(port, path="/store/chats")[1])["chats"]}
        self.assertIn("legacychat", ids)
        # second double-click: hands off and exits quickly without opening a browser itself
        r = subprocess.run(args + ["--ui", "browser", "--log"], env=dict(env, BROWSER="false"), timeout=15,
                           capture_output=True)
        self.assertEqual(r.returncode, 0)
        ask_stop(p1)
        rc = p1.wait(10)
        with open(os.path.join(data, "localchat.log")) as f:
            self.assertEqual(rc, 0, f.read())
        self.assertFalse(desktop.probe_app(port))
        self.assertIsNone(desktop.read_instance(data))
        self.assertTrue(wait_for(lambda: not desktop.ollama_alive(f"http://127.0.0.1:{oport}"), 5))
        with open(os.path.join(data, "localchat.log")) as f:
            log = f.read()
        self.assertIn("migrated from", log)
        self.assertIn("stopped ollama", log)
        self.assertEqual(log.count("handed off to the running instance"), 1)  # 2nd process, same log file
        self.assertEqual(log.count("opening UI via"), 0)  # the 2nd launch did not open its own UI

    def test_ctrl_c_while_ollama_is_starting_still_cleans_up(self):
        port, oport = free_port(), free_port()
        exe = os.path.join(self.tmp, "ollama")
        with open(exe, "w") as f:  # never answers -> desktop.py sits in the wait loop
            f.write(f"#!{PY}\nimport time\ntime.sleep(60)\n")
        os.chmod(exe, 0o755)
        env = dict(self.env, LOCALCHAT_OLLAMA_EXE=exe)
        data = env["LOCALCHAT_DATA"]
        p1 = subprocess.Popen([PY, os.path.join(ROOT, "desktop.py"), "--port", str(port), "--ollama",
                               f"http://127.0.0.1:{oport}", "--ui", "none", "--log"], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **popen_group())
        self.addCleanup(p1.kill)
        logf = os.path.join(data, "localchat.log")
        self.assertTrue(wait_for(lambda: os.path.exists(logf) and "started ollama" in open(logf).read(), 15))
        ask_stop(p1)
        self.assertEqual(p1.wait(10), 0)
        log = open(logf).read()
        self.assertIn("stopped ollama", log)
        self.assertIsNone(desktop.read_instance(data))
        self.assertFalse(desktop.probe_app(port))


if __name__ == "__main__":
    unittest.main()
