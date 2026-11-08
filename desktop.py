#!/usr/bin/env python3
"""LocalVault AI desktop launcher.

The window title is LocalVault AI. The program file stays `Local Chat.exe` and the
data folder stays `%LOCALAPPDATA%\\LocalChat` so an existing install keeps working.

Double-click flow (this is the entry point of `Local Chat.exe`):
  1. If a LocalVault AI window/server is already running, ask it to open another window and exit.
  2. Start Ollama hidden if it isn't answering on localhost:11434.
  3. Run server.py's HTTP server in-process (background thread) on 127.0.0.1:8080.
     --lan also listens on this PC's private IPv4 addresses. A phone must enter a
     pairing code. Closing the window stops that listener and revokes phones.
  4. Show the UI in a native window (pywebview + WebView2). Fallback: `msedge --app=...`,
     then the default browser.
  5. When the last window closes: stop the server (and the Ollama we started, if any).

Data lives in a per-user folder (%LOCALAPPDATA%\\LocalChat\\data on Windows), never next to the exe or
inside PyInstaller's temp extraction dir. Chats from the old v1.0 folder are copied in once.

Run from source:  python desktop.py [--help]
"""
import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

APP_NAME = "LocalVault AI"  # window title, dialogs, and the browser page. Not the exe name.
APP_ID = "LocalChat"  # data folder. Do not rename; existing chats live under this id.
DEFAULT_PORT = 8080
DEFAULT_OLLAMA = "http://localhost:11434"
FROZEN = bool(getattr(sys, "frozen", False))
IS_WIN = os.name == "nt"
LOG_MAX_BYTES = 2 * 1024 * 1024
INSTANCE_FILE = "instance.json"
MIGRATION_FILE = "migrated.json"
CHAT_FILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\.json$")
# Extra legacy folders can be added with LOCALCHAT_LEGACY_DATA. The home
# folder local-chat-app\data is discovered separately for whoever is logged in.
KNOWN_LEGACY_DIRS = []

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


# ---------------------------------------------------------------- paths
def resource_dir():
    """Folder holding index.html/app.js/...: the PyInstaller bundle when frozen, else this file's folder."""
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))


def install_dir():
    """Folder of the exe when frozen (NOT _MEIPASS), else this file's folder."""
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_data_dir(env=None, windows=IS_WIN):
    env = os.environ if env is None else env
    if env.get("LOCALCHAT_DATA"):
        return os.path.abspath(env["LOCALCHAT_DATA"])
    if windows:
        base = env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP_ID, "data")


def _same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def legacy_data_dirs(data_dir, env=None):
    """Old v1.0 `data` folders to import from (existing ones only, target excluded, de-duplicated)."""
    env = os.environ if env is None else env
    cands = [os.path.join(install_dir(), "data")]
    if not FROZEN:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    cands.append(os.path.join(os.path.expanduser("~"), "local-chat-app", "data"))
    cands += KNOWN_LEGACY_DIRS
    cands += [p for p in (env.get("LOCALCHAT_LEGACY_DATA") or "").split(os.pathsep) if p]
    out = []
    for c in cands:
        if not os.path.isdir(os.path.join(c, "chats")) and not os.path.isfile(os.path.join(c, "settings.json")):
            continue
        if _same_path(c, data_dir) or any(_same_path(c, o) for o in out):
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------- logging
_log_lock = threading.Lock()


def log(msg):
    stream = sys.stderr
    if stream is None:
        return
    with _log_lock:
        try:
            stream.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
            stream.flush()
        except Exception:
            pass


def setup_logging(data_dir, force=False):
    """Under pythonw / a windowed exe sys.stdout and sys.stderr are None, and anything that writes to them
    (BaseHTTPRequestHandler.log_message, print, pywebview's logger) would crash. Send both to a log file."""
    if not (force or sys.stdout is None or sys.stderr is None):
        return None
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "localchat.log")
    try:
        if os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass
    f = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stderr = f
    return path


def install_excepthooks():
    def hook(tp, val, tb):
        log("UNCAUGHT " + "".join(traceback.format_exception(tp, val, tb)))

    def thook(args):
        if args.exc_type is SystemExit:
            return
        log("UNCAUGHT in thread %s: %s" % (
            getattr(args.thread, "name", "?"),
            "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))))
    sys.excepthook = hook
    threading.excepthook = thook


# ---------------------------------------------------------------- migration
def _chat_updated(path):
    try:
        with open(path, encoding="utf-8") as f:
            return float(json.load(f).get("updated") or 0)
    except Exception:
        return os.path.getmtime(path) * 1000.0


def migrate_legacy(data_dir, sources, force=False):
    """Copy chats/settings from old data folders into data_dir, once per source folder.

    Sources are only read. Existing chats are overwritten only by a newer copy (by `updated`).
    A marker file remembers which sources were imported, so chats deleted in the app don't come back.
    """
    chat_dir = os.path.join(data_dir, "chats")
    os.makedirs(chat_dir, exist_ok=True)
    marker = os.path.join(data_dir, MIGRATION_FILE)
    try:
        with open(marker, encoding="utf-8") as f:
            done = json.load(f).get("sources", {})
    except Exception:
        done = {}
    stats = {"copied": 0, "updated": 0, "skipped": 0, "settings": False, "sources": []}
    for src in sources:
        key = os.path.normcase(os.path.realpath(src))
        if key in done and not force:
            continue
        sdir = os.path.join(src, "chats")
        names = sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []
        for name in names:
            if not CHAT_FILE_RE.match(name):
                continue
            sp, dp = os.path.join(sdir, name), os.path.join(chat_dir, name)
            try:
                if not os.path.exists(dp):
                    shutil.copy2(sp, dp)
                    stats["copied"] += 1
                elif _chat_updated(sp) > _chat_updated(dp):
                    shutil.copy2(sp, dp + ".tmp")
                    os.replace(dp + ".tmp", dp)
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            except OSError as e:
                log(f"migrate: could not copy {sp}: {e}")
        ss, ds = os.path.join(src, "settings.json"), os.path.join(data_dir, "settings.json")
        if os.path.isfile(ss) and not os.path.exists(ds):
            try:
                shutil.copy2(ss, ds)
                stats["settings"] = True
            except OSError as e:
                log(f"migrate: could not copy settings: {e}")
        done[key] = {"path": src, "at": int(time.time())}
        stats["sources"].append(src)
    if stats["sources"]:
        tmp = marker + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"sources": done}, f, indent=1)
        os.replace(tmp, marker)
    return stats


# ---------------------------------------------------------------- single instance
def _request(url, method="GET", headers=None, timeout=1.0):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=b"{}" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(65536)


def probe_app(port, timeout=0.8):
    """True if a Local Chat server (desktop or plain server.py) answers on 127.0.0.1:port."""
    try:
        status, body = _request(f"http://127.0.0.1:{port}/health", timeout=timeout)
        return status == 200 and json.loads(body).get("ok") is True
    except Exception:
        return False


def read_instance(data_dir):
    try:
        with open(os.path.join(data_dir, INSTANCE_FILE), encoding="utf-8") as f:
            info = json.load(f)
        return info if isinstance(info, dict) and info.get("port") and info.get("token") else None
    except Exception:
        return None


def write_instance(data_dir, port, token):
    path = os.path.join(data_dir, INSTANCE_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "port": port, "token": token, "started": int(time.time())}, f)
    os.replace(tmp, path)


def clear_instance(data_dir, token):
    info = read_instance(data_dir)
    if info and info.get("token") == token:
        try:
            os.remove(os.path.join(data_dir, INSTANCE_FILE))
        except OSError:
            pass


def handoff(data_dir, timeout=2.0):
    """Ask an already-running desktop instance to open a window. Returns its port, or None."""
    info = read_instance(data_dir)
    if not info:
        return None
    try:
        status, _ = _request(f"http://127.0.0.1:{int(info['port'])}/__desktop/open", method="POST",
                             headers={"X-LocalChat-Token": str(info["token"]),
                                      "Content-Type": "application/json"}, timeout=timeout)
        return int(info["port"]) if status == 200 else None
    except Exception:
        return None


def make_handler(server_mod, token, on_open):
    """server.Handler plus POST /__desktop/open (token-protected) used by the single-instance handoff."""
    class DesktopHandler(server_mod.Handler):
        def _route(self, method):
            if urllib.parse.urlparse(self.path).path != "/__desktop/open":
                return super()._route(method)
            if not self._guard(method, "/__desktop/open"):
                return None
            if method != "POST":
                return self._json(405, {"error": "method not allowed"})
            if not hmac.compare_digest(self.headers.get("X-LocalChat-Token", ""), token):
                return self._json(403, {"error": "bad token"})
            self._body()
            try:
                on_open()
            except Exception as e:
                log(f"open window failed: {e!r}")
                return self._json(500, {"error": str(e)})
            return self._json(200, {"ok": True})
    return DesktopHandler


class ServerThread:
    """server.py's HTTP server running in a background thread of this process."""

    def __init__(self, srv):
        self.srv = srv
        self.port = srv.server_address[1]
        self.thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.25},
                                       name="http-server", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self, timeout=5.0):
        done = threading.Event()

        def _shutdown():
            self.srv.shutdown()
            done.set()
        threading.Thread(target=_shutdown, daemon=True).start()
        done.wait(timeout)
        self.srv.server_close()
        self.thread.join(timeout)
        return not self.thread.is_alive()


def acquire_server(server_mod, port, ollama_url, handler, data_dir, retries=6):
    """Bind 127.0.0.1:port. Returns (ServerThread, port) when we own the server, (None, port) when another
    instance already serves Local Chat there. Falls back to a free port if something else holds `port`."""
    for attempt in range(retries):
        try:
            return ServerThread(server_mod.make_server("127.0.0.1", port, ollama_url, handler=handler)).start(), port
        except OSError as e:
            log(f"port {port} busy ({e}); attempt {attempt + 1}")
        other = handoff(data_dir)
        if other:
            return None, -other  # negative: another desktop instance opened the window for us
        if probe_app(port):
            return None, port
        time.sleep(0.5)
    log(f"port {port} is used by another program; using a free port instead")
    st = ServerThread(server_mod.make_server("127.0.0.1", 0, ollama_url, handler=handler)).start()
    return st, st.port


# ---------------------------------------------------------------- Ollama
def ollama_alive(url, timeout=1.0):
    try:
        status, _ = _request(url.rstrip("/") + "/api/version", timeout=timeout)
        return status == 200
    except Exception:
        return False


def find_ollama(env=None):
    env = os.environ if env is None else env
    cands = [env.get("LOCALCHAT_OLLAMA_EXE")]
    if env.get("LOCALAPPDATA"):
        cands.append(os.path.join(env["LOCALAPPDATA"], "Programs", "Ollama", "ollama.exe"))
    for root in (env.get("ProgramFiles"), env.get("ProgramW6432")):
        if root:
            cands.append(os.path.join(root, "Ollama", "ollama.exe"))
    cands.append(shutil.which("ollama"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _is_local_url(url):
    return (urllib.parse.urlparse(url).hostname or "").lower() in ("localhost", "127.0.0.1", "::1")


def start_ollama(url, data_dir, wait=20.0, started=None):
    """Start `ollama serve` hidden if nothing answers at url. Returns the Popen we started, or None.
    The Popen is also appended to `started` right away, so the caller can clean up after a Ctrl+C."""
    if ollama_alive(url):
        log("ollama already running")
        return None
    if not _is_local_url(url):
        log(f"ollama at {url} is not local; not starting it")
        return None
    exe = find_ollama()
    if not exe:
        log("ollama.exe not found; the UI will show how to start it")
        return None
    env = dict(os.environ)
    port = urllib.parse.urlparse(url).port
    if port and port != 11434:
        env["OLLAMA_HOST"] = f"127.0.0.1:{port}"
    kw = {}
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kw = {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, "startupinfo": si}
    logf = open(os.path.join(data_dir, "ollama.log"), "ab")
    try:
        proc = subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                                env=env, close_fds=True, **kw)
    except OSError as e:
        log(f"could not start ollama: {e}")
        return None
    finally:
        logf.close()
    if started is not None:
        started.append(proc)
    log(f"started ollama (pid {proc.pid}): {exe} serve")
    deadline = time.time() + wait
    while time.time() < deadline:
        if ollama_alive(url, timeout=0.5):
            log("ollama is up")
            return proc
        if proc.poll() is not None:
            log(f"ollama exited early with code {proc.returncode}; see ollama.log")
            return None
        time.sleep(0.3)
    log("ollama did not answer in time; continuing")
    return proc


def stop_process_tree(proc, timeout=5.0):
    if proc is None or proc.poll() is not None:
        return
    try:
        if IS_WIN:  # also kills the model runner children so VRAM is freed
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, timeout=timeout)
        else:
            proc.terminate()
        proc.wait(timeout)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------- windows
WINDOW_OPTS = {"width": 1200, "height": 820, "min_size": (480, 400)}


def find_edge(env=None):
    env = os.environ if env is None else env
    cands = []
    for root in (env.get("ProgramFiles(x86)"), env.get("ProgramFiles"), env.get("LOCALAPPDATA")):
        if root:
            cands.append(os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"))
    cands += [shutil.which(n) for n in ("msedge", "microsoft-edge", "microsoft-edge-stable",
                                        "google-chrome", "chromium", "chromium-browser")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def edge_command(exe, url, profile_dir):
    w, h = WINDOW_OPTS["width"], WINDOW_OPTS["height"]
    return [exe, f"--app={url}", f"--user-data-dir={profile_dir}", "--no-first-run",
            "--no-default-browser-check", f"--window-size={w},{h}"]


class WebviewUI:
    name = "webview"

    def __init__(self, url, data_dir, icon):
        import webview  # noqa: F401  (ImportError -> caller falls back)
        self.webview = webview
        self.url, self.data_dir, self.icon = url, data_dir, icon
        self.shown = threading.Event()

    def _window(self):
        w = self.webview.create_window(APP_NAME, self.url, background_color="#000000",
                                       text_select=True, zoomable=True, **WINDOW_OPTS)
        try:
            w.events.shown += self.shown.set
        except Exception:
            self.shown.set()
        return w

    def open_another(self):
        self._window()

    def run(self):
        """Blocks until every window is closed."""
        self._window()
        kw = {"private_mode": False, "storage_path": os.path.join(self.data_dir, "webview")}
        if self.icon and os.path.isfile(self.icon):
            kw["icon"] = self.icon
        if IS_WIN:
            kw["gui"] = "edgechromium"
        self.webview.start(**kw)


class EdgeAppUI:
    name = "edge-app"

    def __init__(self, url, data_dir, exe):
        self.url, self.exe = url, exe
        self.profile = os.path.join(data_dir, "edge-profile")

    def _launch(self):
        kw = {"creationflags": CREATE_NO_WINDOW} if IS_WIN else {}
        return subprocess.Popen(edge_command(self.exe, self.url, self.profile), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)

    def open_another(self):
        self._launch()  # same profile -> joins the running browser process

    def run(self):
        # A private --user-data-dir gets its own browser process, which lives until its last window closes.
        start = time.time()
        rc = self._launch().wait()
        if time.time() - start < 3:
            raise RuntimeError(f"edge exited immediately (code {rc})")


class BrowserUI:
    """Last resort: default browser plus a 'running' dialog; closing the dialog stops the server."""
    name = "browser"

    def __init__(self, url):
        self.url = url
        self.stop = threading.Event()

    def open_another(self):
        import webbrowser
        webbrowser.open(self.url)

    def run(self):
        self.open_another()
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f"{APP_NAME} is running at {self.url}\n\nClick OK to stop it.", APP_NAME, 0x40040)
            return
        try:
            while not self.stop.wait(1):
                pass
        except KeyboardInterrupt:
            pass


class DeferredOpen:
    """Handoff requests can arrive before the UI exists; queue them until it does."""

    def __init__(self):
        self.ui, self.pending, self.lock = None, 0, threading.Lock()

    def __call__(self):
        with self.lock:
            if self.ui is None:
                self.pending += 1
                return
        self.ui.open_another()

    def attach(self, ui):
        with self.lock:
            self.ui, n = ui, self.pending
            self.pending = 0
        for _ in range(n):
            ui.open_another()


def run_ui(url, data_dir, mode, opener):
    """Show the UI, falling back webview -> Edge --app -> default browser. Blocks until closed."""
    icon = os.path.join(resource_dir(), "desktop_assets", "icon.ico")
    order = {"auto": ["webview", "edge", "browser"], "webview": ["webview"], "edge": ["edge", "browser"],
             "browser": ["browser"]}[mode]
    for kind in order:
        try:
            if kind == "webview":
                ui = WebviewUI(url, data_dir, icon)
            elif kind == "edge":
                exe = find_edge()
                if not exe:
                    log("edge not found")
                    continue
                ui = EdgeAppUI(url, data_dir, exe)
            else:
                ui = BrowserUI(url)
            opener.attach(ui)
            log(f"opening UI via {ui.name}: {url}")
            ui.run()
            if kind == "webview" and not ui.shown.is_set():
                raise RuntimeError("webview returned without showing a window")
            return kind
        except Exception as e:
            log(f"UI '{kind}' failed: {e!r}")
            opener.ui = None
    return None


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def install_signal_handlers():
    """SIGTERM (and SIGINT when a parent ignored it) -> normal shutdown path (server stop, Ollama stop)."""
    import signal
    if threading.current_thread() is not threading.main_thread():
        return
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _raise_interrupt)
            except (OSError, ValueError):
                pass


def set_app_user_model_id():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalChat.Desktop")
        except Exception:
            pass


# ---------------------------------------------------------------- main
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="LocalVault AI", description="LocalVault AI desktop app (program file: Local Chat.exe)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--data", default=None, help="data folder (default: per-user, env LOCALCHAT_DATA)")
    ap.add_argument("--ollama", default=os.environ.get("OLLAMA_URL") or DEFAULT_OLLAMA)
    ap.add_argument("--ui", choices=["auto", "webview", "edge", "browser", "none"], default="auto",
                    help="none = headless server until Ctrl+C (for testing)")
    ap.add_argument("--lan", action="store_true",
                    help="allow a paired iPhone on this PC's private Wi-Fi (off unless set, or turned on in the window)")
    ap.add_argument("--no-ollama", action="store_true", help="don't start Ollama")
    ap.add_argument("--keep-ollama", action="store_true", help="leave an Ollama we started running on exit")
    ap.add_argument("--remigrate", action="store_true", help="import old v1.0 chats again")
    ap.add_argument("--log", action="store_true", help="always log to <data>/localchat.log")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    data_dir = os.path.abspath(args.data or default_data_dir())
    os.makedirs(data_dir, exist_ok=True)
    setup_logging(data_dir, force=args.log)
    install_excepthooks()
    install_signal_handlers()
    set_app_user_model_id()
    log(f"{APP_NAME} start: frozen={FROZEN} exe={sys.executable} resources={resource_dir()} data={data_dir}")

    # 1. single instance: an existing desktop instance opens the window for us
    if args.ui != "none" and handoff(data_dir):
        log("handed off to the running instance")
        return 0

    import server  # after logging is set up
    server.set_data_dir(data_dir)
    try:
        st = migrate_legacy(data_dir, legacy_data_dirs(data_dir), force=args.remigrate)
        if st["sources"]:
            log(f"migrated from {st['sources']}: {st['copied']} new, {st['updated']} updated, "
                f"settings={st['settings']}")
    except Exception as e:
        log(f"migration failed: {e!r}")

    token = secrets.token_urlsafe(24)
    opener = DeferredOpen()
    handler = make_handler(server, token, opener)
    srv, port = acquire_server(server, args.port, args.ollama, handler, data_dir)
    if port < 0:
        log("another instance opened the window")
        return 0
    if srv:
        write_instance(data_dir, port, token)
        try:
            log(f"server on http://127.0.0.1:{port} (pruned {server.prune_chats()} old chats)")
        except Exception as e:
            log(f"prune failed: {e!r}")
    else:
        log(f"reusing the LocalVault AI server already on port {port} (not owned by this window)")

    url = f"http://127.0.0.1:{port}/"
    ollama_procs = []
    try:
        if args.lan:
            if not srv:
                log("LAN not started: this process does not own the server")
            else:
                try:
                    info = server.enable_lan(port, acknowledge=True, handler=handler)
                    urls = ", ".join(row["url"] for row in info.get("addresses") or [])
                    log("LAN access on %s (pairing required; code is shown in the iPhone panel, not logged)" % urls)
                except server.LanError as exc:
                    log("LAN access not started: %s" % exc)
        if not args.no_ollama:
            start_ollama(args.ollama, data_dir, started=ollama_procs)
        if args.ui == "none":
            log("headless mode; Ctrl+C to stop")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        elif not run_ui(url, data_dir, args.ui, opener):
            log("no UI could be opened")
            return 1
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        log("shutting down")
        was_lan = False
        try:
            was_lan = server.lan_is_on()
            server.disable_lan()
        except Exception as exc:
            log("LAN stop failed: %r" % (exc,))
        if was_lan:
            log("LAN access stopped; paired phones revoked; iPhone listener closed")
        if srv:
            srv.stop()
            clear_instance(data_dir, token)
        for proc in ollama_procs:
            if args.keep_ollama:
                log(f"leaving ollama (pid {proc.pid}) running")
            else:
                stop_process_tree(proc)
                log("stopped ollama")
        log("bye")


def _exit(code):
    """Hard exit after our own cleanup: GUI toolkits can leave non-daemon threads behind that would keep
    a windowless process alive (seen with pywebview's Qt backend)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:
            pass
    os._exit(code)


if __name__ == "__main__":
    _exit(main() or 0)
