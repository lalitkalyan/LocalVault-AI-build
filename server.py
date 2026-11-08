#!/usr/bin/env python3
"""LocalVault AI v1.2 - stdlib-only local server.

Serves the static UI, proxies a whitelisted subset of the Ollama API (streaming),
stores chats as local JSON files, and offers two opt-in local tools:
  /tools/search  (DuckDuckGo HTML web search; only called when Web Access is on)
  /tools/run     (Python code execution in a subprocess with limits)

No telemetry. The server only makes outbound requests to Ollama, and to the web
only when the UI explicitly calls /tools/search.

Default bind is 127.0.0.1. --lan (or a private --host) also listens on this PC's
private IPv4 addresses and requires a one-time pairing code before a phone can
reach chats, the model proxy, search, or the code runner. Ollama stays on
localhost. 0.0.0.0 is refused: a wildcard bind would make the Host allow-list
either wrong (Safari sends the LAN IP, not 0.0.0.0) or wide open.
"""
import argparse
import html
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import lan

VERSION = "1.2"
# Static files live next to this file, or in the PyInstaller bundle (sys._MEIPASS) when frozen.
APP_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
DATA_DIR = CHAT_DIR = SETTINGS_FILE = ""


def set_data_dir(path):
    """Point the chat/settings store at `path` (desktop.py uses a per-user folder)."""
    global DATA_DIR, CHAT_DIR, SETTINGS_FILE
    DATA_DIR = os.path.abspath(path)
    CHAT_DIR = os.path.join(DATA_DIR, "chats")
    SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


set_data_dir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
PRUNE_DAYS = 7
RUN_TIMEOUT = 15
RUN_OUTPUT_LIMIT = 20000
MAX_BODY = 64 * 1024 * 1024  # images as base64 can be large

STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/favicon.svg": "favicon.svg",
    "/apple-touch-icon.png": "apple-touch-icon.png",
    "/manifest.webmanifest": "manifest.webmanifest",
}
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
}
# Only these Ollama endpoints are reachable through the proxy (no pull/delete/push).
OLLAMA_ALLOWED = {"/api/tags", "/api/chat", "/api/show", "/api/version", "/api/ps", "/api/generate"}


def _public_static(path):
    if path in STATIC_FILES:
        return True
    if path.startswith("/vendor/"):
        name = path[len("/vendor/"):]
        return bool(re.fullmatch(r"[A-Za-z0-9._-]+", name)) and not name.startswith(".")
    return False

CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; font-src 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_store_lock = threading.Lock()

CONFIG = {"ollama": "http://localhost:11434", "port": 8080, "host": "127.0.0.1"}
LanError = lan.LanError
_LAN_ADMIN = {"/lan/enable", "/lan/disable", "/lan/code", "/lan/revoke"}
_SECRET_QUERY_KEYS = {"code", "token", "session", "password", "secret", "lc_session"}
_lan_lock = threading.Lock()
_lan_listeners = []  # (server, thread)


# ---------------------------------------------------------------- chat store
def ensure_dirs():
    os.makedirs(CHAT_DIR, exist_ok=True)


def prune_chats():
    cutoff = time.time() - PRUNE_DAYS * 86400
    removed = 0
    for name in os.listdir(CHAT_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(CHAT_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                updated = json.load(f).get("updated", 0) / 1000.0
        except Exception:
            updated = os.path.getmtime(path)
        if updated < cutoff:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


def list_chats():
    out = []
    for name in os.listdir(CHAT_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(CHAT_DIR, name), encoding="utf-8") as f:
                c = json.load(f)
            out.append({"id": c.get("id"), "title": c.get("title", "Untitled"),
                        "created": c.get("created", 0), "updated": c.get("updated", 0),
                        "model": c.get("model", "")})
        except Exception:
            continue
    out.sort(key=lambda c: c["updated"], reverse=True)
    return out


def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- web search
class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        t = "".join(self.parts)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\n\s*\n+", "\n", t)
        return t.strip()


def _http_get(url, data=None, timeout=10, limit=2_000_000):
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        raw = r.read(limit)
        charset = r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), ctype


def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def ddg_lite(query, max_results=5):
    body = urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode()
    page, _ = _http_get("https://lite.duckduckgo.com/lite/", data=body)
    links = re.findall(r"<a[^>]+href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>(.*?)</a>", page, re.S)
    snips = re.findall(r"<td[^>]*class='result-snippet'[^>]*>(.*?)</td>", page, re.S)
    out = []
    for i, (href, title) in enumerate(links):
        href = html.unescape(href)
        if "duckduckgo.com/l/?" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if not href.startswith("http") or "duckduckgo.com/y.js" in href:
            continue
        out.append({"title": _clean(title), "url": href, "snippet": _clean(snips[i]) if i < len(snips) else ""})
        if len(out) >= max_results:
            break
    return out


def _bing_url(href):
    href = html.unescape(href)
    if "bing.com/ck/a" in href:
        u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            import base64
            b = u[2:]
            try:
                return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "replace")
            except Exception:
                return ""
    return href


def bing_html(query, max_results=5):
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "setlang": "en", "count": "10"})
    page, _ = _http_get(url)
    out = []
    for b in re.split(r'<li[^>]+class="b_algo[^"]*"', page)[1:]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        href = _bing_url(m.group(1))
        if not href.startswith("http"):
            continue
        sn = re.search(r'<p[^>]*>(.*?)</p>', b, re.S)
        out.append({"title": _clean(m.group(2)), "url": href,
                    "snippet": _clean(sn.group(1)) if sn else ""})
        if len(out) >= max_results:
            break
    return out


def ddg_search(query, max_results=5):
    # Bing first (DDG often answers 202 to scripted requests), then DDG endpoints with backoff.
    last_err = None
    for attempt, fn in enumerate((bing_html, ddg_html, ddg_lite, bing_html)):
        if attempt:
            time.sleep(0.8 * attempt)
        try:
            res = fn(query, max_results)
            if res:
                return res
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return []


def ddg_html(query, max_results=5):
    body = urllib.parse.urlencode({"q": query, "kl": "wt-wt"}).encode()
    page, _ = _http_get("https://html.duckduckgo.com/html/", data=body)
    results = []
    blocks = re.split(r'<div[^>]+class="[^"]*\bresult\b[^"]*"', page)[1:]
    for b in blocks:
        if "result--ad" in b[:200]:
            continue
        m = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        href = html.unescape(m.group(1))
        if "duckduckgo.com/l/?" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = qs.get("uddg", [href])[0]
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith("http") or "duckduckgo.com/y.js" in href:
            continue
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', b, re.S)
        results.append({"title": _clean(m.group(2)), "url": href,
                        "snippet": _clean(sn.group(1)) if sn else ""})
        if len(results) >= max_results:
            break
    return results


def fetch_text(url, limit=3000):
    try:
        page, ctype = _http_get(url, timeout=8)
        if "html" not in ctype and "text" not in ctype:
            return ""
        p = _TextExtractor()
        p.feed(page)
        return p.text()[:limit]
    except Exception:
        return ""


def do_search(query, fetch_pages=2, max_results=5):
    results = ddg_search(query, max_results)
    if fetch_pages:
        threads = []
        for r in results[:fetch_pages]:
            def work(r=r):
                r["text"] = fetch_text(r["url"])
            t = threading.Thread(target=work, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(10)
    return results


# ---------------------------------------------------------------- code runner
def _limits():  # runs in the child (POSIX only)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (RUN_TIMEOUT, RUN_TIMEOUT))
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass
    os.setsid()


_python_cmd = None


def python_command():
    """Interpreter for /tools/run. In a frozen exe sys.executable is the app itself, so find a real Python."""
    global _python_cmd
    if _python_cmd is not None:
        return _python_cmd
    override = os.environ.get("LOCALCHAT_PYTHON")
    if override:
        _python_cmd = [override]
    elif not getattr(sys, "frozen", False):
        _python_cmd = [sys.executable]
    else:
        _python_cmd = _find_system_python()
    return _python_cmd


def _find_system_python():
    import glob
    if os.name != "nt":
        exe = shutil.which("python3") or shutil.which("python")
        return [exe] if exe else []
    exe = shutil.which("python")
    if exe and "windowsapps" not in exe.lower():  # skip the Microsoft Store alias stub
        return [exe]
    patterns = []
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python")):
        if root:
            patterns.append(os.path.join(root, "Python3*", "python.exe"))
    found = sorted({f for pat in patterns for f in glob.glob(pat)},
                   key=lambda f: [int(x) for x in re.findall(r"\d+", os.path.basename(os.path.dirname(f)))],
                   reverse=True)
    if found:
        return [found[0]]
    py = shutil.which("py")
    return [py, "-3"] if py else []


def run_python(code):
    cmd = python_command()
    if not cmd:
        return {"stdout": "", "stderr": "No Python interpreter found for the code runner. Install Python 3 "
                "or set LOCALCHAT_PYTHON to python.exe.", "exit_code": -1, "timed_out": False, "duration_ms": 0}
    tmp = tempfile.mkdtemp(prefix="lca-run-")
    try:
        path = os.path.join(tmp, "main.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp, "TMPDIR": tmp,
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg"}
        if os.name == "nt":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        kw = {}
        if os.name == "posix":
            kw["preexec_fn"] = _limits
        elif os.name == "nt":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash in the GUI app
        start = time.time()
        timed_out = False
        try:
            p = subprocess.run(cmd + ["-I", path], cwd=tmp, env=env,
                               stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=RUN_TIMEOUT, **kw)
            out, err, rc = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            out, err, rc, timed_out = e.stdout or b"", e.stderr or b"", -1, True

        def trim(b):
            s = b.decode("utf-8", errors="replace")
            if len(s) > RUN_OUTPUT_LIMIT:
                s = s[:RUN_OUTPUT_LIMIT] + f"\n... [truncated, {len(s) - RUN_OUTPUT_LIMIT} more chars]"
            return s
        return {"stdout": trim(out), "stderr": trim(err), "exit_code": rc,
                "timed_out": timed_out, "duration_ms": int((time.time() - start) * 1000)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- HTTP handler
class Handler(BaseHTTPRequestHandler):
    server_version = "LocalChat/" + VERSION
    sys_version = ""

    def log_message(self, fmt, *args):
        if os.environ.get("LCA_QUIET"):
            return
        stream = sys.stderr
        if stream is None:  # pythonw.exe / windowed exe: no console, writing would crash the handler
            return
        try:
            stream.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))
        except Exception:
            pass

    def log_request(self, code="-", size="-"):
        # Path only. Query strings are never logged (a client might wrongly put a code there).
        path = urllib.parse.urlparse(self.path).path
        self.log_message('"%s %s %s" %s', self.command, path, self.request_version, str(code))

    # --- security helpers
    def _peer_ip(self):
        ip = self.client_address[0] if self.client_address else ""
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        return ip

    def _is_loopback_peer(self):
        return self._peer_ip() in ("127.0.0.1", "::1")

    def _allowed_hosts(self):
        p = CONFIG["port"]
        hosts = {f"127.0.0.1:{p}", f"localhost:{p}", f"[::1]:{p}"}
        bound = (CONFIG.get("host") or "").lower()
        # Never trust a wildcard bind address as a Host header. Phones send the real IP.
        if bound and bound not in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::", ""):
            hosts.add(f"{bound}:{p}")
        hosts.update(lan.GATE.host_headers())
        return hosts

    def _query_has_secret(self):
        query = urllib.parse.urlparse(self.path).query
        if not query:
            return False
        for key, _value in urllib.parse.parse_qsl(query, keep_blank_values=True):
            if key.lower() in _SECRET_QUERY_KEYS or key.lower().startswith("token"):
                return True
        return False

    def _origin_host(self):
        origin = self.headers.get("Origin") or ""
        return origin.lower().split("//", 1)[-1]

    def _guard(self, method, path):
        """Host, Origin, CSRF, and LAN-session checks. Loopback keeps the v1.1 rules."""
        if self._query_has_secret():
            self._json(403, {"error": "forbidden"})
            return False
        host = (self.headers.get("Host") or "").strip().lower()
        allowed = self._allowed_hosts()
        if host not in allowed:  # blocks DNS-rebinding
            self._json(403, {"error": "forbidden host"})
            return False
        loopback = self._is_loopback_peer()
        if not loopback:
            # A LAN peer may only claim the private address it actually connected to.
            if host not in lan.GATE.host_headers():
                self._json(403, {"error": "forbidden host"})
                return False
            if not lan.GATE.peer_allowed(self._peer_ip()):
                self._json(403, {"error": "forbidden network"})
                return False
        mutating = method in ("POST", "PUT", "DELETE", "PATCH")
        if mutating and not path.startswith("/__"):
            origin_host = self._origin_host()
            if not loopback:
                if not origin_host or origin_host not in lan.GATE.host_headers():
                    self._json(403, {"error": "forbidden origin"})
                    return False
            elif self.headers.get("Origin") and origin_host not in allowed:
                self._json(403, {"error": "forbidden origin"})
                return False
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":  # forces a CORS preflight for cross-site callers
                self._json(415, {"error": "Content-Type must be application/json"})
                return False
            if path.startswith("/lan/") and int(self.headers.get("Content-Length") or 0) > 8192:
                self._json(413, {"error": "body too large"})
                return False
        if loopback:
            return True
        if path in _LAN_ADMIN or path.startswith("/__desktop"):
            self._json(403, {"error": "forbidden"})
            return False
        if (path == "/lan/status" and method == "GET") or (path == "/lan/pair" and method == "POST"):
            return True
        if path == "/lan/logout" and method == "POST":
            return True
        if method == "GET" and _public_static(path):
            return True
        if lan.GATE.session_ok(self.headers.get("Cookie"), self._peer_ip()):
            return True
        self._json(401, {"error": "auth_required"})
        return False

    def _check_request(self, mutating):
        # Kept for older callers. Prefer _guard, which also knows the path.
        path = urllib.parse.urlparse(self.path).path
        if mutating and path.startswith("/__"):
            return self._guard("GET", path)
        return self._guard("POST" if mutating else "GET", path)

    def _common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Permitted-Cross-Domain-Policies", "none")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Cache-Control", "no-store")

    def _json(self, code, obj, extra_headers=None):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._common_headers()
        for key, value in extra_headers or ():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        return self.rfile.read(n) if n else b""

    def _json_body(self):
        raw = self._body()
        return json.loads(raw or b"{}")

    # --- routing
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method):
        path = urllib.parse.urlparse(self.path).path
        if not self._guard(method, path):
            return
        try:
            if path == "/lan/status" and method == "GET":
                return self._lan_status()
            if path == "/lan/pair" and method == "POST":
                return self._lan_pair()
            if path == "/lan/logout" and method == "POST":
                return self._lan_logout()
            if path == "/lan/enable" and method == "POST":
                return self._lan_enable()
            if path == "/lan/disable" and method == "POST":
                return self._lan_disable()
            if path == "/lan/code" and method == "GET":
                return self._lan_code()
            if path == "/lan/code" and method == "POST":
                return self._lan_code_rotate()
            if path == "/lan/revoke" and method == "POST":
                return self._lan_revoke()
            if path.startswith("/api/"):
                return self._proxy(method, path)
            if path.startswith("/store/"):
                return self._store(method, path)
            if path == "/tools/search" and method == "POST":
                return self._search()
            if path == "/tools/run" and method == "POST":
                return self._run()
            if path == "/health":
                return self._json(200, {"ok": True, "ollama": CONFIG["ollama"]})
            if method == "GET":
                return self._static(path)
            self._json(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                msg = str(e) if self._is_loopback_peer() else "internal error"
                self._json(500, {"error": msg})
            except Exception:
                pass

    def _lan_view(self):
        snap = lan.GATE.snapshot()
        port = int(CONFIG["port"])
        if snap["enabled"] and snap["bindings"]:
            addresses = []
            for row in snap["bindings"]:
                addresses.append({
                    "ip": row["ip"], "prefix": row["prefix"], "name": row["name"],
                    "preferred": bool(row.get("preferred")), "listening": True,
                    "url": lan.address_url(row["ip"], row["port"]),
                })
        else:
            addresses = []
            for row in lan.list_private_ipv4():
                addresses.append({
                    "ip": row["ip"], "prefix": row["prefix"], "name": row["name"],
                    "preferred": bool(row.get("preferred")), "listening": False,
                    "url": lan.address_url(row["ip"], port),
                    "prefix_assumed": bool(row.get("prefix_assumed")),
                })
        ohost = (urllib.parse.urlparse(CONFIG["ollama"]).hostname or "").lower()
        return {
            "loopback": True,
            "lan": bool(snap["enabled"]),
            "port": port,
            "addresses": addresses,
            "has_code": snap["has_code"],
            "code_expires_in": snap["code_expires_in"],
            "code_burned": snap["code_burned"],
            "sessions": lan.GATE.public_sessions() if snap["enabled"] else [],
            "warnings": snap["warnings"],
            "ollama_local": ohost in ("localhost", "127.0.0.1", "::1"),
        }

    def _lan_status(self):
        if not self._is_loopback_peer():
            paired = lan.GATE.session_ok(self.headers.get("Cookie"), self._peer_ip())
            return self._json(200, {"loopback": False, "lan": True, "paired": paired})
        return self._json(200, self._lan_view())

    def _lan_pair(self):
        if not lan.GATE.snapshot()["enabled"]:
            return self._json(404, {"error": "iPhone access is off"})
        peer = self._peer_ip()
        try:
            body = self._json_body()
        except Exception:
            return self._json(400, {"error": "bad request"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "bad request"})
        result = lan.GATE.check_code(str(body.get("code", "")), peer)
        if result == "locked":
            return self._json(429, {"error": "Too many tries from this phone. Wait 15 minutes, then create a new code on the PC."})
        if result == "expired":
            return self._json(401, {"error": "That code expired. On the PC, create a new pairing code."})
        if result != "ok":
            return self._json(401, {"error": "That code doesn't match. Check the PC screen."})
        token, record = lan.GATE.open_session(peer, body.get("label"))
        if not token:
            return self._json(429, {"error": "Too many phones are paired. Revoke one on the PC."})
        self._json(200, {"ok": True}, extra_headers=(
            ("Set-Cookie", lan.session_cookie(token, lan.SESSION_TTL)),))

    def _lan_logout(self):
        lan.GATE.revoke_cookie(self.headers.get("Cookie"))
        self._json(200, {"ok": True}, extra_headers=(("Set-Cookie", lan.clear_cookie()),))

    def _require_loopback(self):
        if self._is_loopback_peer():
            return True
        self._json(403, {"error": "forbidden"})
        return False

    def _lan_enable(self):
        if not self._require_loopback():
            return
        try:
            body = self._json_body()
        except Exception:
            return self._json(400, {"error": "bad request"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "expected object"})
        try:
            info = enable_lan(CONFIG["port"], acknowledge=body.get("acknowledge") is True, handler=type(self))
        except LanError as e:
            return self._json(400, {"error": str(e)})
        self._json(200, info)

    def _lan_disable(self):
        if not self._require_loopback():
            return
        self._json_body()
        disable_lan()
        self._json(200, {"ok": True, "lan": False})

    def _lan_code(self):
        if not self._require_loopback():
            return
        current = lan.GATE.current_code()
        if not current:
            return self._json(404, {"error": "no pairing code"})
        self._json(200, current)

    def _lan_code_rotate(self):
        if not self._require_loopback():
            return
        self._json_body()
        if not lan.GATE.snapshot()["enabled"]:
            return self._json(400, {"error": "iPhone access is off"})
        self._json(200, lan.GATE.issue_code())

    def _lan_revoke(self):
        if not self._require_loopback():
            return
        body = self._json_body()
        if body.get("all") is True:
            lan.GATE.revoke(all_sessions=True)
        else:
            sid = str(body.get("id") or "")
            if not re.fullmatch(r"[0-9a-f]{8}", sid):
                return self._json(400, {"error": "bad id"})
            lan.GATE.revoke(sid=sid)
        self._json(200, {"ok": True, "sessions": lan.GATE.public_sessions()})

    def _static(self, path):
        rel = STATIC_FILES.get(path)
        if rel is None and path.startswith("/vendor/"):
            name = path[len("/vendor/"):]
            if re.fullmatch(r"[A-Za-z0-9._-]+", name) and not name.startswith("."):
                rel = os.path.join("vendor", name)
        if rel is None:
            return self._json(404, {"error": "not found"})
        full = os.path.join(APP_DIR, rel)
        if not os.path.isfile(full):
            return self._json(404, {"error": "not found"})
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self._common_headers()
        if rel == "index.html":
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    # --- Ollama proxy (streams the response through unchanged)
    def _proxy(self, method, path):
        if path not in OLLAMA_ALLOWED:
            return self._json(404, {"error": f"endpoint {path} not proxied"})
        body = self._body() if method in ("POST", "PUT") else None
        u = urllib.parse.urlparse(CONFIG["ollama"])
        conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=900)
        try:
            try:
                conn.request(method, (u.path.rstrip("/") + path), body=body,
                             headers={"Content-Type": "application/json"} if body else {})
                resp = conn.getresponse()
            except (ConnectionError, OSError, http.client.HTTPException) as e:
                return self._json(502, {"error": "ollama_unreachable", "ollama": CONFIG["ollama"],
                                        "detail": str(e)})
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
            self._common_headers()
            self.send_header("Connection", "close")
            self.end_headers()
            reader = resp.read1 if hasattr(resp, "read1") else (lambda n: resp.readline())
            while True:
                chunk = reader(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)  # client abort -> BrokenPipe -> upstream closed below
                self.wfile.flush()
        finally:
            conn.close()  # closing the upstream socket makes Ollama cancel generation
            self.close_connection = True

    # --- chat/settings storage
    def _store(self, method, path):
        parts = path.strip("/").split("/")
        with _store_lock:
            ensure_dirs()
            if parts == ["store", "settings"]:
                if method == "GET":
                    try:
                        with open(SETTINGS_FILE, encoding="utf-8") as f:
                            return self._json(200, json.load(f))
                    except FileNotFoundError:
                        return self._json(200, {})
                if method == "PUT":
                    obj = self._json_body()
                    if not isinstance(obj, dict):
                        return self._json(400, {"error": "expected object"})
                    atomic_write(SETTINGS_FILE, obj)
                    return self._json(200, {"ok": True})
            if parts == ["store", "chats"] and method == "GET":
                pruned = prune_chats()
                return self._json(200, {"chats": list_chats(), "pruned": pruned})
            if len(parts) == 3 and parts[:2] == ["store", "chats"]:
                cid = parts[2]
                if not CHAT_ID_RE.match(cid):
                    return self._json(400, {"error": "bad id"})
                fp = os.path.join(CHAT_DIR, cid + ".json")
                if method == "GET":
                    if not os.path.exists(fp):
                        return self._json(404, {"error": "not found"})
                    with open(fp, encoding="utf-8") as f:
                        return self._json(200, json.load(f))
                if method == "PUT":
                    obj = self._json_body()
                    if not isinstance(obj, dict):
                        return self._json(400, {"error": "expected object"})
                    obj["id"] = cid
                    obj.setdefault("created", int(time.time() * 1000))
                    obj["updated"] = int(obj.get("updated") or time.time() * 1000)
                    atomic_write(fp, obj)
                    return self._json(200, {"ok": True})
                if method == "DELETE":
                    if os.path.exists(fp):
                        os.remove(fp)
                    return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})

    def _search(self):
        req = self._json_body()
        q = str(req.get("query", "")).strip()[:400]
        if not q:
            return self._json(400, {"error": "empty query"})
        fetch_pages = int(req.get("fetch_pages", 2))
        try:
            results = do_search(q, fetch_pages=max(0, min(fetch_pages, 5)),
                                max_results=max(1, min(int(req.get("max_results", 5)), 10)))
        except Exception as e:
            return self._json(502, {"error": "search_failed", "detail": str(e)})
        self._json(200, {"query": q, "results": results})

    def _run(self):
        req = self._json_body()
        code = str(req.get("code", ""))
        if not code.strip():
            return self._json(400, {"error": "empty code"})
        if len(code) > 200_000:
            return self._json(400, {"error": "code too large"})
        self._json(200, run_python(code))


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    if os.name == "nt":
        # On Windows SO_REUSEADDR lets a second process bind the same port silently; use exclusive binding
        # so a second start fails with OSError instead of splitting requests between two servers.
        allow_reuse_address = False

        def server_bind(self):
            import socket
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()


def _stop_listener(srv, thread, timeout=5.0):
    done = threading.Event()

    def _shutdown():
        try:
            srv.shutdown()
        finally:
            done.set()

    threading.Thread(target=_shutdown, name="lan-shutdown", daemon=True).start()
    done.wait(timeout)
    try:
        srv.server_close()
    except Exception:
        pass
    if thread is not None:
        thread.join(timeout)


def lan_is_on():
    with _lan_lock:
        return bool(_lan_listeners)


def _select_lan_addresses(only_ips):
    found = lan.list_private_ipv4()
    if not only_ips:
        return found[:8]
    picked = []
    by_ip = {row["ip"]: row for row in found}
    missing = []
    for ip in only_ips:
        if ip in ("0.0.0.0", "::", ""):
            raise LanError("Refusing to listen on all interfaces. Use --lan.")
        if not lan.is_private_ipv4(ip):
            raise LanError("Refusing to bind a public address.")
        row = by_ip.get(ip)
        if row is None:
            missing.append(ip)
        else:
            picked.append(row)
    if missing:
        raise LanError("That address is not a private adapter on this PC: " + ", ".join(missing))
    return picked[:8]


def _lan_payload(code, already=False):
    snap = lan.GATE.snapshot()
    addresses = [{
        "ip": row["ip"], "prefix": row["prefix"], "name": row["name"],
        "preferred": bool(row.get("preferred")), "listening": True,
        "url": lan.address_url(row["ip"], row["port"]),
    } for row in snap["bindings"]]
    # `code` is returned to the loopback caller only. Do not log this dict.
    return {
        "ok": True, "already": already, "lan": True, "addresses": addresses,
        "warnings": snap["warnings"], "has_code": True,
        "code_expires_in": code.get("expires_in", 0), "code": code.get("code", ""),
    }


def enable_lan(port, acknowledge=False, only_ips=None, handler=None, addresses=None):
    """Listen on private IPv4 addresses at `port` and issue a pairing code.

    The loopback server is separate and keeps working without a code. Pass
    acknowledge=True only after the operator opts in (--lan or the iPhone button).
    """
    if acknowledge is not True:
        raise LanError("acknowledgement required")
    port = int(port)
    if port == 11434:
        raise LanError("refusing to use the Ollama port")
    if port == 0 or not 1 <= port <= 65535:
        raise LanError("bad port")
    ohost = (urllib.parse.urlparse(CONFIG["ollama"]).hostname or "").lower()
    if ohost not in ("localhost", "127.0.0.1", "::1"):
        raise LanError("Ollama must stay on localhost")
    if addresses is None:
        selected = _select_lan_addresses(only_ips)
    else:
        selected = []
        for row in addresses:
            if row["ip"] in ("0.0.0.0", "::") or not lan.is_private_ipv4(row["ip"]):
                raise LanError("refusing a non-private address")
            selected.append(row)
    if not selected:
        raise LanError("no private network address found")
    handler = handler or Handler
    with _lan_lock:
        if _lan_listeners:
            code = lan.GATE.current_code() or lan.GATE.issue_code()
            return _lan_payload(code, already=True)
        started, warnings, bound = [], [], []
        for row in selected:
            try:
                srv = LocalServer((row["ip"], port), handler)
            except OSError as exc:
                warnings.append("%s:%s (%s)" % (row["ip"], port, getattr(exc, "errno", None) or exc.__class__.__name__))
                continue
            thread = threading.Thread(
                target=srv.serve_forever, kwargs={"poll_interval": 0.25},
                name="lan-http-%s" % row["ip"], daemon=True)
            thread.start()
            started.append((srv, thread))
            bound.append({
                "ip": row["ip"], "prefix": int(row.get("prefix") or 24),
                "name": (row.get("name") or "network")[:64], "port": port,
                "preferred": bool(row.get("preferred")),
            })
        if not started:
            raise LanError("could not listen on a private address: " + "; ".join(warnings))
        _lan_listeners.extend(started)
        lan.GATE.set_listening(bound, warnings)
        return _lan_payload(lan.GATE.issue_code(), already=False)


def disable_lan():
    """Close LAN sockets and drop every paired phone. Safe to call when LAN is off."""
    with _lan_lock:
        listeners = list(_lan_listeners)
        _lan_listeners.clear()
    lan.GATE.clear()
    for srv, thread in listeners:
        _stop_listener(srv, thread)
    return {"ok": True, "lan": False}


def make_server(host="127.0.0.1", port=8080, ollama=None, handler=None):
    """Create (but don't start) the HTTP server. Raises OSError if the port is taken."""
    CONFIG.update(host=host, port=port)
    if ollama:
        CONFIG["ollama"] = ollama.rstrip("/")
    ensure_dirs()
    srv = LocalServer((host, port), handler or Handler)
    if port == 0:  # ephemeral port: allowed Host headers must use the real one
        CONFIG["port"] = srv.server_address[1]
    return srv


def main():
    ap = argparse.ArgumentParser(description="Local AI Chat server")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    ap.add_argument("--host", default="127.0.0.1",
                    help="default 127.0.0.1. A private IPv4 address turns on paired iPhone access. "
                         "0.0.0.0 is refused; use --lan")
    ap.add_argument("--lan", action="store_true",
                    help="also listen on this PC's private IPv4 addresses; phones must enter a pairing code")
    ap.add_argument("--ollama", default=os.environ.get("OLLAMA_URL") or "http://localhost:11434",
                    help="Ollama base URL (default http://localhost:11434, env OLLAMA_URL). Stays on localhost.")
    ap.add_argument("--data", default=os.environ.get("LOCALCHAT_DATA") or DATA_DIR,
                    help="folder for chats and settings (default ./data, env LOCALCHAT_DATA)")
    a = ap.parse_args()
    if a.host in ("0.0.0.0", "::"):
        ap.error("Refusing to listen on all interfaces. Use --lan to allow paired phones on private addresses only.")
    only = None
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        if not lan.is_private_ipv4(a.host):
            ap.error("Refusing to bind a public address. Use --lan on a private network.")
        only = [a.host]
    set_data_dir(a.data)
    srv = make_server("127.0.0.1", a.port, a.ollama)
    port = srv.server_address[1]
    lan_note = ""
    if a.lan or only is not None:
        try:
            info = enable_lan(port, acknowledge=True, only_ips=only)
        except LanError as exc:
            srv.server_close()
            ap.error(str(exc))
        urls = ", ".join(row["url"] for row in info["addresses"])
        lan_note = f"  iPhone LAN: {urls} (pairing required; the code is not printed)"
    n = prune_chats()
    print(f"LocalVault AI v{VERSION} -> http://127.0.0.1:{port}  (Ollama: {CONFIG['ollama']}, "
          f"pruned {n} old chats, data: {DATA_DIR}){lan_note}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        disable_lan()
        srv.server_close()


if __name__ == "__main__":
    main()
