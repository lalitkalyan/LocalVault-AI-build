# LocalVault AI

Your private, local AI assistant.

A minimal, pure-black chat UI for [Ollama](https://ollama.com). The model runs on your computer:
no accounts, no telemetry, no CDNs. All JavaScript (marked, pdf.js) is vendored in `vendor/`.

The name means local inference. Chats are ordinary JSON files. They are not encrypted by this app, and an iPhone on the same Wi-Fi uses plain HTTP. See the code-runner and LAN notes below.

On Windows the window title is LocalVault AI. The program file stays `Local Chat.exe` and chats stay in `%LOCALAPPDATA%\LocalChat\data`. A typical install folder is `%LOCALAPPDATA%\Programs\LocalChat\`. How to build and refresh shortcuts: [docs/LOCALVAULT.md](docs/LOCALVAULT.md).

```
this PC's browser ──► server.py (127.0.0.1:8080) ──► Ollama (127.0.0.1:11434)
                              ├─ data/chats/*.json   local chat history (auto-deleted after 7 days)
                              ├─ /tools/run          local Python runner (Code toggle)
                              └─ /tools/search       DuckDuckGo search (only when Web is ON)

optional, off by default:
iPhone Safari ──► same app on a private Wi-Fi address, after a one-time pairing code
                  (Ollama is not opened to the network)
```

## Requirements

- **Python 3.9+** (standard library only, nothing to `pip install`)
- **Ollama** installed and running: https://ollama.com/download
- At least one model pulled, for example:

```sh
ollama pull qwen2.5:3b        # fast general chat (or llama3.2:3b, gemma3:4b, qwen3:4b …)
ollama pull qwen3:4b          # supports native Reasoning ("thinking")
ollama pull moondream         # small vision model for images/screenshots (or gemma3:4b, qwen2.5vl:3b, llama3.2-vision)
```

## Run

1. Make sure Ollama is running (`ollama serve`, or just open the Ollama app). Check: `ollama list`.
2. Start the app from this folder:

```sh
python3 server.py
```

3. Open **http://127.0.0.1:8080**.

One-click start scripts (they also open the browser):

- macOS/Linux: `./start.sh`
- Windows: double‑click `start.bat` (uses `py -3` or `python`)

Options: `python3 server.py --port 8080 --ollama http://localhost:11434` (or env `OLLAMA_URL`, `PORT`).
The server binds to `127.0.0.1` only. Stop it with Ctrl+C.

iPhone access is off unless you opt in. On Windows, double-click `Start-iPhone-Access.bat` or run `python desktop.py --lan`, then follow [docs/IPHONE.md](docs/IPHONE.md). `python3 server.py --host 0.0.0.0` is refused.

## Windows desktop app (`Local Chat.exe`)

The window is titled LocalVault AI. Double-click `Local Chat.exe` and it:

1. starts Ollama hidden (`%LOCALAPPDATA%\Programs\Ollama\ollama.exe serve`) if nothing answers on `localhost:11434`,
2. runs `server.py` inside the app process on `127.0.0.1:8080` (no console window),
3. opens the UI in its own window with no tabs or address bar (pywebview with Edge WebView2).
   If WebView2 can't start, it opens an Edge app window (`msedge --app=...`). If Edge is missing too, it uses your default browser,
4. stops the server when you close the last window. It also stops Ollama, but only if the app started it.
   If Ollama was already running, the app leaves it alone.

Double-clicking again while the app is running opens a second window of the same instance. It doesn't start a second server.
If `python server.py` is already serving port 8080, the app opens a window onto that server and doesn't stop it on close.
If another program holds port 8080, the app picks a free port.

### Build it (on the Windows PC)

1. Install Python 3.10+ (3.13 at `C:\Program Files\Python313` is found automatically).
2. Double-click **`build_exe.bat`** in this folder, or run it from a terminal:

```bat
build_exe.bat            :: folder build -> dist\Local Chat\Local Chat.exe  (recommended: starts fast)
build_exe.bat onefile    :: single file  -> dist\Local Chat.exe            (starts slower: unpacks to %TEMP% on every launch)
```

The script makes a private build venv (`.venv-build`), installs `pyinstaller`, `pywebview`, and `pillow` there,
generates the icon (`make_icon.py` writes `desktop_assets\icon.ico`), and runs PyInstaller with `LocalChat.spec`.
It needs internet access the first time (pip). Your system Python isn't modified.

Replace the existing `%LOCALAPPDATA%\Programs\LocalChat\` folder rather than creating a second install. See [docs/LOCALVAULT.md](docs/LOCALVAULT.md). Shortcut labels can be updated with `deploy\Rename-LocalVaultShortcuts.ps1` without changing the exe path.
Close LocalVault AI before you rebuild. The process name is still `Local Chat.exe`.

### Where your data lives

| | |
|---|---|
| Chats + settings | `%LOCALAPPDATA%\LocalChat\data\` (`chats\*.json`, `settings.json`) |
| App log (replaces the console) | `%LOCALAPPDATA%\LocalChat\data\localchat.log` (rotated at 2 MB) |
| Ollama log, when the app started it | `%LOCALAPPDATA%\LocalChat\data\ollama.log` |
| Window storage (WebView2) | `%LOCALAPPDATA%\LocalChat\data\webview\` |

On first launch the app **copies** chats and settings from old v1.0 folders into the new folder. It checks
`data\` next to the exe and `%USERPROFILE%\local-chat-app\data`.
The old files are never changed or deleted. Each old folder is imported only once (recorded in `migrated.json`),
so a chat you delete in the app doesn't come back. If the same chat exists in both places, the newer copy wins.
To import again, run `"Local Chat.exe" --remigrate`.
Extra folders to import: set `LOCALCHAT_LEGACY_DATA` (`;`-separated).

`python server.py` still uses `./data/` next to `server.py`, as in v1.0. Use `--data <folder>` (or `LOCALCHAT_DATA`)
to share the desktop app's folder: `python server.py --data "%LOCALAPPDATA%\LocalChat\data"`.

### Options (for a shortcut's Target field or a terminal)

`--port 8080`, `--ollama http://localhost:11434`, `--data <folder>`, `--ui auto|webview|edge|browser|none`, `--lan` (paired iPhone on this PC's private Wi-Fi; off by default),
`--no-ollama` (never start Ollama), `--keep-ollama` (leave the Ollama the app started running after you close it),
`--remigrate`, `--log` (log to the file even when a console exists).
Environment variables: `LOCALCHAT_DATA`, `LOCALCHAT_PYTHON` (the interpreter for the ▶ Run code button), `LOCALCHAT_OLLAMA_EXE`.

### Desktop notes

- **Code runner in the exe:** the exe can't run Python code by itself, so ▶ Run uses your installed Python
  (`python` on PATH, then `C:\Program Files\Python3*`, `%LOCALAPPDATA%\Programs\Python\Python3*`, then `py -3`).
  Set `LOCALCHAT_PYTHON` to choose one. It still runs as your user, as in v1.0.
- Windowed apps have no console, so request logs and errors go to `localchat.log`. If the app doesn't open, check that file first.
- Run from source without building: `python desktop.py` (needs `pip install pywebview` for the native window; otherwise it uses an Edge app window).
- The native window and the exe use `desktop_assets/icon.ico` (a light chip on a black tile, not a lock). A pinned shortcut keeps the old picture until `deploy\Refresh-LocalVaultTaskbarIcon.ps1` points that shortcut at the icon. The script does not restart Explorer and does not delete other shortcuts. The Edge fallback window keeps Edge's icon.
- Tests (they run on Linux/macOS/Windows): `python -m unittest discover -s tests -v`.
- Windows SmartScreen may warn about an unsigned exe you built yourself: click **More info → Run anyway**.

## Features

- **Layout**: left sidebar of recent chats with timestamps, centered conversation, input with send button and toggles below it.
- **Streaming** answers, **Stop** button (■ or Esc), **Enter** to send, **Shift+Enter** for a newline, Ctrl/Cmd+Shift+O for a new chat.
- **Markdown** with code blocks and **Copy** buttons. Raw HTML from the model is escaped and remote images are never loaded.
- **Model dropdown** from your installed Ollama models. The app remembers your last choice and shows each model's capabilities (vision, thinking, tools).
- **History** is saved as JSON files in `data/chats/` on your disk. Chats older than **7 days** are deleted on startup and whenever the list loads. You can delete one chat (✕) or all chats (Settings).
- **System prompt** (editable in Settings, persisted). Default:
  > You are a direct, no-nonsense assistant. Answer clearly and concisely. Do not add polite filler, moralizing, disclaimers, or unnecessary niceties. Be straightforward and get to the point. If something is wrong or dumb, say so. Prioritize usefulness over being agreeable.
- **Web** toggle. **OFF = fully offline**: nothing leaves your machine except calls to your local Ollama. **ON**: your message is sent to DuckDuckGo, the top 5 results (plus text from the top 2 pages, configurable) are added as context, and the sources are listed under the answer.
- **Reasoning** toggle. For models with native thinking (qwen3, deepseek-r1, gpt-oss, …) the app uses Ollama's `think` parameter and shows the thinking in a collapsible block. For other models it adds a "think step by step" instruction.
- **Images**: paste a screenshot (Ctrl/Cmd+V), drag and drop, or use ＋ to upload. Images are downscaled to 1536 px and sent in Ollama's `images` field. If the selected model has no vision support, the app switches to an installed vision model (qwen2.5vl, gemma3, llama3.2-vision, llava, moondream, …). If none is installed, it tells you to `ollama pull moondream`.
- **PDFs / text files**: text is extracted in the browser with the vendored pdf.js and added as context (up to 60k characters per file).
- **Code** toggle: the model is told it can write ```python blocks. Each block gets a **▶ Run** button that runs it locally (15 s timeout, temp dir, output capped at 20 kB). Use "Send output to model" to continue. Turn on *Auto-run* in Settings to run the first block automatically and send the output back once.
- **Clear errors**: if Ollama isn't reachable, a banner shows the URL being tried and how to fix it. Because requests go through `server.py`, you don't need to set `OLLAMA_ORIGINS`. Only set it if you point a browser directly at Ollama.

## Privacy and security notes

- LocalVault AI runs the model on this PC. Chat files in `%LOCALAPPDATA%\LocalChat\data\` are ordinary JSON, not an encrypted store.
- No analytics, no external fonts or scripts. A strict Content-Security-Policy (`connect-src 'self'`) stops the page from contacting any other host.
- The server only proxies the Ollama endpoints it needs (`tags`, `chat`, `show`, `version`, `ps`, `generate`). It cannot pull or delete models.
- Requests with a foreign `Host` or `Origin`, and writes without a JSON content type, are rejected. Other websites in your browser can't use the local server or its code runner (this blocks CSRF and DNS rebinding). iPhone access adds a pairing cookie on top of those checks and does not allow `0.0.0.0` or a public address. The pairing code is not put in the URL or the log.
- Your data lives in `./data/` (chats + settings) with `python server.py`, or in `%LOCALAPPDATA%\LocalChat\data\` with the desktop app. Delete that folder to wipe everything.

## Limitations

- **The code runner is not a real sandbox.** It is a separate `python3 -I` process with a timeout, a temp working dir, a minimal environment, and (on macOS/Linux) CPU, memory, and file-size rlimits. The code still runs as your user and can read your files and use the network. Only run code you've read. On Windows only the timeout applies.
- Code runs on the standard library plus whatever is installed in the Python that runs `server.py`. Plots or images from code aren't displayed; there's text output only.
- Web search scrapes DuckDuckGo's HTML pages (no API key). DuckDuckGo throttles sometimes; the server retries, and if it still fails, the app answers without web context and says so. Web context is plain injection, not tool calling.
- Small models (≤1B) follow the injected web/PDF context and the code instructions unreliably. Use 3B+ models for good results.
- Scanned PDFs (images only) have no extractable text. There's no OCR; paste a screenshot to a vision model instead.
- Attached images and file text are stored inside the chat JSON (until it's pruned after 7 days). Older images are sent again only if the current model supports vision.
- Long chats aren't summarized or trimmed. Very long histories can exceed the model's context window (Ollama truncates silently). Start a new chat.
- One person, one PC. Loopback has no extra login. iPhone access is opt-in, private-IPv4 only, and closed until a pairing code from the PC is entered. See [docs/IPHONE.md](docs/IPHONE.md). Do not publish this server, forward the port, or point Ollama at `0.0.0.0`.
- No chat search, export, edit, or branching yet.

## Files

```
server.py        stdlib HTTP server: static files, Ollama proxy, chat store, /tools/search, /tools/run, LAN pairing
lan.py            private-IPv4 discovery and in-memory pairing sessions (no tokens on disk)
index.html       markup
app.js           all UI logic (ES module)
style.css        pure black theme
vendor/          marked 18.1.2 (MIT), pdf.js 6.4.299 legacy build (Apache-2.0)
start.sh / start.bat
data/            created at runtime by `python server.py` (chats + settings), git-ignored

desktop.py              v1.1 desktop launcher (Ollama start, in-process server, native window, single instance)
build_exe.bat           Windows build script -> dist\Local Chat\Local Chat.exe
LocalChat.spec          PyInstaller spec used by build_exe.bat
requirements-desktop.txt  build-only deps (pyinstaller, pywebview, pillow)
make_icon.py            draws desktop_assets/icon.ico (16–256, BMP frames through 128)
desktop_assets/icon.ico taskbar, window, and exe icon
manifest.webmanifest    Home Screen name: LocalVault AI
tests/test_desktop.py   tests for desktop.py and server.py
tests/test_lan.py        pairing, auth boundaries, shutdown
tests/test_brand.py      visible name, subtitle, and install-path compatibility
docs/IPHONE.md           iPhone connection steps, troubleshooting, and the security limits
docs/LOCALVAULT.md       Windows build and shortcut rename without moving data
deploy/                  firewall, update, shortcut rename, and taskbar-icon refresh
Start-iPhone-Access.bat  starts the desktop app with iPhone access on
```
