"""Stop, timeout, and empty-answer recovery. Uses a fake Ollama, not a live model."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402


class FakeOllama(BaseHTTPRequestHandler):
    # HTTP/1.0 keeps the body open until this handler closes the socket.
    # HTTP/1.1 without a length is treated as an empty body.
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        self.server.bodies.append(raw)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        self.wfile.flush()
        mode = self.server.mode
        if mode == "empty":
            self.wfile.write(b'{"message":{"role":"assistant","thinking":"hmm","content":""}}\n')
            self.wfile.write(b'{"done":true}\n')
            self.wfile.flush()
            return
        if mode == "ok":
            self.wfile.write(b'{"message":{"role":"assistant","content":"hello"},"done":true}\n')
            self.wfile.flush()
            return
        if mode == "mid":
            self.wfile.write(b'{"message":{"role":"assistant","content":"partial"}}\n')
            self.wfile.flush()
        self.server.accepted.set()
        deadline = time.time() + 8
        while time.time() < deadline:
            if server.client_disconnected(self.connection):
                self.server.closed.set()
                return
            time.sleep(0.05)
        try:
            self.wfile.write(b'{"message":{"content":"LATE"}}\n')
            self.wfile.flush()
            self.server.late = True
        except OSError:
            self.server.late = False


def _start_fake():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    httpd.bodies = []
    httpd.mode = "hang"
    httpd.accepted = threading.Event()
    httpd.closed = threading.Event()
    httpd.late = False
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _start_app(ollama):
    previous = server.CONFIG.get("ollama")
    data = server.DATA_DIR
    tmp = tempfile.mkdtemp(prefix="lc-rel-")
    server.set_data_dir(tmp)
    srv = server.make_server("127.0.0.1", 0, ollama)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, previous, data, tmp


class TestGenerationProxy(unittest.TestCase):
    def setUp(self):
        self.fake = _start_fake()
        self.app, self.prev_ollama, self.prev_data, self.tmp = _start_app(
            "http://127.0.0.1:%d" % self.fake.server_address[1])
        self.env = {
            "LOCALCHAT_FIRST_TOKEN_S": os.environ.get("LOCALCHAT_FIRST_TOKEN_S"),
            "LOCALCHAT_IDLE_S": os.environ.get("LOCALCHAT_IDLE_S"),
            "LOCALCHAT_TOTAL_S": os.environ.get("LOCALCHAT_TOTAL_S"),
        }

    def tearDown(self):
        for key, value in self.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.app.shutdown()
        self.fake.shutdown()
        server.CONFIG["ollama"] = self.prev_ollama
        server.set_data_dir(self.prev_data)
        shutil.rmtree(self.tmp, True)

    def _post(self, timeout=4):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.app.server_address[1], timeout=timeout)
        body = json.dumps({"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "hi"}], "stream": True}).encode()
        conn.request("POST", "/api/chat", body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        return conn

    def test_stop_before_first_token_closes_ollama(self):
        self.fake.mode = "hang"
        conn = self._post()
        self.assertTrue(self.fake.accepted.wait(2), "fake Ollama was not reached")
        conn.sock.close()
        self.assertTrue(self.fake.closed.wait(2), "Ollama connection stayed open after the client hung up")
        self.assertFalse(self.fake.late)
        self.fake.mode = "ok"
        self.fake.accepted.clear()
        conn2 = self._post()
        resp = conn2.getresponse()
        payload = resp.read()
        conn2.close()
        self.assertEqual(resp.status, 200)
        self.assertIn(b"hello", payload)
        self.assertNotIn(b"LATE", payload)
        self.assertGreaterEqual(len(self.fake.bodies), 2)

    def test_stop_midstream_drops_late_chunks(self):
        self.fake.mode = "mid"
        conn = self._post()
        resp = conn.getresponse()
        got = b""
        while b"partial" not in got:
            chunk = resp.read(1)
            self.assertTrue(chunk)
            got += chunk
        resp.close()
        self.assertTrue(self.fake.closed.wait(2))
        self.assertNotIn(b"LATE", got)
        self.assertFalse(self.fake.late)

    def test_timeout_releases_upstream_and_next_request_works(self):
        os.environ["LOCALCHAT_FIRST_TOKEN_S"] = "0.4"
        os.environ["LOCALCHAT_IDLE_S"] = "5"
        os.environ["LOCALCHAT_TOTAL_S"] = "5"
        self.fake.mode = "hang"
        conn = self._post(timeout=3)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        self.assertIn(b"generation_timeout", payload)
        self.assertIn(b"first_token", payload)
        self.assertTrue(self.fake.closed.wait(2))
        self.fake.mode = "ok"
        self.fake.accepted.clear()
        self.fake.closed.clear()
        conn2 = self._post()
        resp2 = conn2.getresponse()
        body = resp2.read()
        conn2.close()
        self.assertEqual(resp2.status, 200)
        self.assertIn(b"hello", body)
        self.assertNotIn(b"generation_timeout", body)

    def test_empty_thinking_stream_is_forwarded_for_the_ui_to_classify(self):
        self.fake.mode = "empty"
        conn = self._post()
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn(b"thinking", payload)
        self.assertIn(b'"done":true', payload)
        self.assertNotIn(b"generation_timeout", payload)

    def test_health_answers_after_the_socket_accepts(self):
        """Smoke: once this process is listening, /health answers.

        This does not reproduce the desktop window. On the installed 1.2.0
        build the window sometimes accepts TCP and then serves nothing. A
        later launch of that same exe answered / and /health. The hang is
        intermittent and is not claimed fixed here.
        """
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.app.server_address[1], timeout=2)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode())
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["version"], server.VERSION)

    def test_health_limits_match_the_ui_defaults(self):
        import http.client
        for key in ("LOCALCHAT_FIRST_TOKEN_S", "LOCALCHAT_IDLE_S", "LOCALCHAT_TOTAL_S"):
            os.environ.pop(key, None)
        conn = http.client.HTTPConnection("127.0.0.1", self.app.server_address[1], timeout=2)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        health = json.loads(resp.read().decode())
        conn.close()
        out = subprocess.check_output(
            ["node", "--input-type=module", "-e",
             "import { DEFAULT_LIMITS } from './reliability.js'; process.stdout.write(JSON.stringify(DEFAULT_LIMITS))"],
            cwd=ROOT, text=True)
        defaults = json.loads(out)
        self.assertEqual(health["version"], "1.3")
        self.assertEqual(health["generation"]["first_token_s"], defaults["first_token_s"])
        self.assertEqual(health["generation"]["idle_s"], defaults["idle_s"])
        self.assertEqual(health["generation"]["total_s"], defaults["total_s"])
        self.assertEqual(server.generation_limits()["first_token_s"], 180)


class TestReliabilityBehavior(unittest.TestCase):
    def test_node_behavior(self):
        proc = subprocess.run(
            ["node", os.path.join(ROOT, "tests", "reliability_behavior.mjs")],
            cwd=ROOT, text=True, capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("reliability behavior ok", proc.stdout)

    def test_code_model_choice_is_unchanged(self):
        with open(os.path.join(ROOT, "app.js"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('[/^qwen2\\.5-coder/i, "best for code"]', text)


if __name__ == "__main__":
    unittest.main()
