// LocalVault AI v1.3 — frontend. No external requests: everything goes to this PC's server.
import { splitThink, turnNote, planRetry, sameGeneration, canSwitchChat, DEFAULT_LIMITS } from "./reliability.js";
const DEFAULT_SYSTEM_PROMPT = "You are a direct, no-nonsense assistant. Answer clearly and concisely. Do not add polite filler, moralizing, disclaimers, or unnecessary niceties. Be straightforward and get to the point. If something is wrong or dumb, say so. Prioritize usefulness over being agreeable.";
const CODE_PROMPT = "\n\nCode interpreter: you can run Python 3 (standard library only, no network, no input(), 15s limit). When computing, verifying, or processing data helps, write ONE complete runnable script in a ```python code block that prints its results. The user runs it locally and sends you the output; then give the final answer based on that output. Do not invent outputs.";
const REASON_PROMPT = "\n\nReasoning mode: think through the problem step by step before answering. Show the key steps briefly, then give the final answer clearly marked.";
const PRUNE_MS = 7 * 24 * 3600 * 1000;
const VISION_PREFS = ["qwen3.5:4b", "qwen3.5:2b", "qwen3.5", "qwen2.5vl", "qwen3-vl", "gemma3", "llama3.2-vision", "llava-llama3", "llava-phi3", "llava", "minicpm-v", "moondream", "bakllava", "granite3.2-vision", "mistral-small3.1", "llama4"];
const MAX_FILE_CHARS = 60000;

const $ = (s) => document.querySelector(s);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

const state = {
  settings: { systemPrompt: DEFAULT_SYSTEM_PROMPT, temperature: 0.6, autoRun: false, fetchPages: true, web: false, reason: false, code: false, autoModel: true, lastModel: "" },
  models: [], caps: {}, chats: [], chat: null, pending: [], streaming: null, searching: false, ollamaOk: false, userPickedModel: false, pairing: false, generation: 0, limits: DEFAULT_LIMITS, version: "1.3",
};

// ------------------------------------------------------------------ API
async function api(path, opts = {}) {
  const o = { credentials: "same-origin", ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch { /* non-json */ }
  if (r.status === 401 && data && data.error === "auth_required") {
    showPairScreen();
    const e = new Error("auth_required"); e.data = data; e.status = 401; throw e;
  }
  if (!r.ok) { const e = new Error((data && (data.error || data.detail)) || r.statusText); e.data = data; e.status = r.status; throw e; }
  return data;
}

// ------------------------------------------------------------------ markdown
const renderer = {
  code({ text, lang }) {
    const l = (lang || "").trim().split(/\s+/)[0].toLowerCase().replace(/[^a-z0-9+#._-]/g, "");
    const isPy = ["python", "py", "python3"].includes(l);
    const run = isPy && state.settings.code ? `<button class="run" data-act="run">▶ Run</button>` : "";
    return `<div class="codeblock"><div class="cb-head"><span>${esc(l || "text")}</span><span class="spacer"></span>${run}<button data-act="copy">Copy</button></div><pre><code>${esc(text)}</code></pre></div>`;
  },
  html({ text }) { return esc(text); },                         // never render raw HTML from the model
  image({ href, text }) { return `<span class="muted">[image: ${esc(text || href)}]</span>`; }, // no remote loads
  link({ href, title, tokens }) {
    const inner = this.parser.parseInline(tokens);
    if (!/^(https?:|mailto:|#)/i.test(href || "")) return inner;
    return `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer"${title ? ` title="${esc(title)}"` : ""}>${inner}</a>`;
  },
};
window.marked.use({ renderer, gfm: true, breaks: false });
const md = (s) => { try { return window.marked.parse(s || ""); } catch { return `<pre>${esc(s)}</pre>`; } };

// ------------------------------------------------------------------ settings
async function loadSettings() {
  let local = {}, remote = {};
  try { local = JSON.parse(localStorage.getItem("lc.settings") || "{}"); } catch {}
  try { remote = await api("/store/settings"); } catch {}
  // newest wins (server file is shared across browsers; localStorage may hold an unsynced change)
  const [older, newer] = (local.savedAt || 0) > (remote.savedAt || 0) ? [remote, local] : [local, remote];
  Object.assign(state.settings, older, newer);
  if ((local.savedAt || 0) > (remote.savedAt || 0)) api("/store/settings", { method: "PUT", body: state.settings }).catch(() => {});
  if (!state.settings.systemPrompt) state.settings.systemPrompt = DEFAULT_SYSTEM_PROMPT;
  if (state.settings.autoModel === undefined) state.settings.autoModel = true;
  $("#tWeb").checked = !!state.settings.web; $("#tReason").checked = !!state.settings.reason; $("#tCode").checked = !!state.settings.code;
  const ta = $("#tAuto"); if (ta) ta.checked = !!state.settings.autoModel;
}
let saveSettingsT;
function saveSettings() {
  state.settings.savedAt = Date.now();
  localStorage.setItem("lc.settings", JSON.stringify(state.settings));
  clearTimeout(saveSettingsT);
  saveSettingsT = setTimeout(() => api("/store/settings", { method: "PUT", body: state.settings }).catch(() => {}), 200);
}

// ------------------------------------------------------------------ models
function banner(html) { const b = $("#banner"); if (!html) { b.hidden = true; b.innerHTML = ""; return; } b.innerHTML = html; b.hidden = false; }
// Dropdown groups, best first. A model can appear in several groups.
const MODEL_GROUPS = [
  ["Chat", [
    [/^qwen3\.5:4b/i, "better, slower"],
    [/^qwen3\.5:2b/i, "fast"],
  ]],
  ["Images", [
    [/^qwen3\.5:4b/i, "better vision"],
    [/^qwen3\.5:2b/i, "fast vision"],
  ]],
  ["Web search", [
    [/^qwen3\.5:4b/i, "better at results"],
    [/^qwen3\.5:2b/i, "fast lookups"],
  ]],
  ["Code", [
    [/^qwen2\.5-coder/i, "best for code"],
    [/^qwen3\.5:2b/i, "fast code fallback"],
  ]],
];
const MODEL_DESCS = [
  [/coder|codellama|starcoder|deepseek-coder|codegemma/i, "code"],
  [/qwen3\.5:4b/i, "better, slower · chat + images + thinking"],
  [/qwen3\.5:2b/i, "fast · chat + images + thinking"],
  [/qwen3\.5/i, "chat + images + thinking"],
  [/moondream/i, "images"],
  [/llava|vision|qwen2\.5vl|qwen3-vl|minicpm-v|bakllava/i, "images + chat"],
  [/gemma3(?!.*:1b)/i, "chat + images"],
  [/qwen3|deepseek-r1|phi4-reasoning|magistral/i, "reasoning + chat"],
  [/qwen2\.5:(7b|14b|32b)|llama3\.1|mistral|gemma2/i, "all-rounder, smarter, slower"],
  [/qwen2\.5|phi3/i, "all-rounder, fast"],
  [/llama3\.2/i, "chat"],
  [/embed|bge|nomic/i, "embeddings"],
];
function modelDesc(name) { for (const [re, d] of MODEL_DESCS) if (re.test(name)) return d; return ""; }
let retryT;
async function loadModels() {
  clearTimeout(retryT);
  try {
    const data = await api("/api/tags");
    state.ollamaOk = true;
    state.models = (data.models || []).sort((a, b) => a.name.localeCompare(b.name));
    if (!state.models.length) {
      banner(`Ollama is running but no models are installed. In a terminal run <code>ollama pull qwen3.5:2b</code> (chat + images) and <code>ollama pull qwen2.5-coder:3b</code> (code), then <button class="btn-ghost" data-act="retry">Retry</button>`);
    } else banner(null);
  } catch (e) {
    state.ollamaOk = false; state.models = [];
    if (e.data && e.data.error === "ollama_unreachable") {
      banner(`Can't reach Ollama at <code>${esc(e.data.ollama)}</code>. Start it with <code>ollama serve</code> (or open the Ollama app), then <button class="btn-ghost" data-act="retry">Retry</button><br><span class="small">Other host/port? Restart with <code>python3 server.py --ollama http://HOST:11434</code>. CORS/<code>OLLAMA_ORIGINS</code> is not needed because requests go through server.py; only set <code>OLLAMA_ORIGINS</code> if you call Ollama from the browser directly.</span>`);
    } else if (e instanceof TypeError) {
      banner(`The local server (server.py) isn't responding. Start it with <code>python3 server.py</code>.`);
    } else banner(`Ollama error: ${esc(e.message)} <button class="btn-ghost" data-act="retry">Retry</button>`);
    retryT = setTimeout(loadModels, 5000);
  }
  const sel = $("#modelSelect");
  sel.innerHTML = "";
  if (!state.models.length) sel.append(new Option("no models", ""));
  const used = new Set();
  for (const [group, ranking] of MODEL_GROUPS) {
    const og = document.createElement("optgroup"); og.label = group;
    let rank = 0;
    for (const [re, note] of ranking) {
      for (const m of state.models.filter((x) => re.test(x.name))) {
        const gb = m.size ? ` · ${(m.size / 1e9).toFixed(1)} GB` : "";
        og.append(new Option(`${++rank}. ${m.name} : ${note}${gb}`, m.name));
        used.add(m.name);
      }
    }
    if (og.children.length) sel.append(og);
  }
  const rest = state.models.filter((m) => !used.has(m.name));
  if (rest.length) {
    const og = document.createElement("optgroup"); og.label = "Other";
    for (const m of rest) {
      const gb = m.size ? ` · ${(m.size / 1e9).toFixed(1)} GB` : "";
      const d = modelDesc(m.name);
      og.append(new Option(m.name + (d ? ` : ${d}` : "") + gb, m.name));
    }
    sel.append(og);
  }
  const want = state.settings.lastModel;
  if (want && state.models.some((m) => m.name === want)) sel.value = want;
  else if (state.models[0]) { sel.value = state.models[0].name; }
  await updateCaps();
}
async function getCaps(model) {
  if (!model) return [];
  if (state.caps[model]) return state.caps[model];
  try {
    const d = await api("/api/show", { method: "POST", body: { model } });
    let caps = d.capabilities || [];
    if (!d.capabilities) { // older Ollama: guess
      const fam = JSON.stringify(d.details || {}) + model;
      if (/clip|mllama|vision|llava|moondream/i.test(fam) || d.projector_info) caps = ["completion", "vision"];
      else caps = ["completion"];
    }
    state.caps[model] = caps;
  } catch { state.caps[model] = []; }
  return state.caps[model];
}
async function updateCaps() {
  const m = $("#modelSelect").value;
  const caps = await getCaps(m);
  $("#modelCaps").textContent = caps.filter((c) => c !== "completion").join(" · ");
  updateHint();
}
function updateHint() {
  const m = $("#modelSelect").value, caps = state.caps[m] || [];
  const parts = [];
  if (state.settings.autoModel) parts.push("auto model");
  if (state.settings.reason && !caps.includes("thinking")) parts.push("reasoning via prompt (no native thinking)");
  if (state.settings.web) parts.push("web on: your message is sent to DuckDuckGo");
  else parts.push("offline");
  $("#hint").textContent = parts.join(" · ");
}
async function findVisionModel() {
  const names = state.models.map((m) => m.name);
  const ranked = [...names].sort((a, b) => rank(a) - rank(b));
  function rank(n) { const i = VISION_PREFS.findIndex((p) => n.startsWith(p)); return i < 0 ? 99 : i; }
  for (const n of ranked) { if ((await getCaps(n)).includes("vision")) return n; }
  return null;
}

// ------------------------------------------------------------------ auto model
const CODE_HINT = /\b(code|coding|function|bug|refactor|python|javascript|typescript|html|css|sql|algorithm|compile|stack\s*trace|debug|syntax|regex|linter|typeerror|nullpointer|dockerfile|kubernetes|api\s*endpoint|console\.log|npm|pip\s+install)\b|\b(js|ts)\b|\b(class|def|import)\s+\w+/i;
const IMAGE_HINT = /\b(this|the|attached|pasted)\s+(image|picture|photo|screenshot|pic)\b|\b(describe|look at|what('s| is) in)\s+(this|the)\s+(image|picture|photo|screenshot|pic)\b|\b(ocr|read the text in)\b/i;

function groupRanking(label) {
  const g = MODEL_GROUPS.find(([l]) => l === label);
  return g ? g[1] : [];
}
function firstInstalled(ranking) {
  const names = state.models.map((m) => m.name);
  for (const [re] of ranking) {
    const hit = names.find((n) => re.test(n));
    if (hit) return hit;
  }
  return null;
}
function classifyTask(text, hasImages) {
  if (hasImages || IMAGE_HINT.test(text || "")) return "Images";
  if (state.settings.code || CODE_HINT.test(text || "")) return "Code";
  if (state.settings.web) return "Web search";
  return "Chat";
}
function pickAutoModel(text, hasImages) {
  const task = classifyTask(text, hasImages);
  let model = firstInstalled(groupRanking(task));
  if (!model && task === "Code") model = firstInstalled(groupRanking("Chat"));
  // Reasoning on + chat: prefer qwen3.5 (native thinking), 4b then 2b
  if (model && state.settings.reason && task === "Chat") {
    const q35 = state.models.find((m) => /^qwen3\.5:4b/i.test(m.name))
      || state.models.find((m) => /^qwen3\.5:2b/i.test(m.name))
      || state.models.find((m) => /^qwen3\.5/i.test(m.name));
    if (q35) model = q35.name;
  }
  return { model, task };
}
const TASK_NOTICE = { Images: "images", Code: "code", "Web search": "web search", Chat: "chat" };

// ------------------------------------------------------------------ chats
async function loadChats() {
  try { const d = await api("/store/chats"); state.chats = d.chats.filter((c) => Date.now() - c.updated < PRUNE_MS); }
  catch { state.chats = []; }
  renderSidebar();
}
function fmtTime(ts) {
  const d = new Date(ts), now = new Date();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = (day(now) - day(d)) / 86400000;
  if (diff === 0) return `Today ${hm}`;
  if (diff === 1) return `Yesterday ${hm}`;
  return `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${hm}`;
}
function renderSidebar() {
  const list = $("#chatList"); list.innerHTML = "";
  if (!state.chats.length) list.append(el("div", "muted small", "<div style='padding:8px 10px'>No chats yet</div>"));
  for (const c of state.chats) {
    const it = el("div", "chat-item" + (state.chat && state.chat.id === c.id ? " active" : ""));
    it.dataset.id = c.id;
    it.innerHTML = `<div class="ci-main"><div class="ci-title">${esc(c.title || "Untitled")}</div><div class="ci-time">${esc(fmtTime(c.updated))}</div></div><button class="ci-del" title="Delete chat" data-del="${esc(c.id)}">✕</button>`;
    list.append(it);
  }
}
async function openChat(id) {
  if (!canSwitchChat(!!state.streaming)) return;
  try { state.chat = await api(`/store/chats/${id}`); } catch { return; }
  if (state.chat.model && state.models.some((m) => m.name === state.chat.model)) { $("#modelSelect").value = state.chat.model; await updateCaps(); }
  renderSidebar(); renderMessages();
}
function newChat() {
  if (state.streaming) stop();
  state.chat = null; state.pending = []; renderAttachments(); renderSidebar(); renderMessages(); $("#input").focus();
}
async function deleteChat(id) {
  await api(`/store/chats/${id}`, { method: "DELETE" }).catch(() => {});
  if (state.chat && state.chat.id === id) state.chat = null;
  await loadChats(); renderMessages();
}
async function saveChat(target) {
  const c = target || state.chat; if (!c) return;
  c.updated = Date.now();
  try { await api(`/store/chats/${c.id}`, { method: "PUT", body: c }); } catch (e) { notice(`Couldn't save chat: ${esc(e.message)}`); }
  const i = state.chats.findIndex((x) => x.id === c.id);
  const meta = { id: c.id, title: c.title, created: c.created, updated: c.updated, model: c.model };
  if (i >= 0) state.chats.splice(i, 1);
  state.chats.unshift(meta);
  renderSidebar();
}

// ------------------------------------------------------------------ rendering
function renderAssistantInner(m, live) {
  const { t, c } = splitThink(m.content, m.thinking);
  let h = "";
  if (t) h += `<details class="think"${live && !c ? " open" : ""}><summary>${live && !c ? "Thinking…" : "Thought process"}</summary><div class="think-body">${esc(t)}</div></details>`;
  h += `<div class="md${live ? " cursor" : ""}">${md(c)}</div>`;
  if (m.sources && m.sources.length) {
    h += `<div class="sources"><span class="s-label">Sources</span>${m.sources.map((s, i) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" title="${esc(s.url)}">[${i + 1}] ${esc(s.title || s.url)}</a>`).join("")}</div>`;
  }
  if (!live) {
    const note = turnNote(m);
    if (note) h += `<div class="turn-note">${esc(note.text)}</div>`;
    else if (m.error) h += `<div class="notice">⚠ ${esc(m.error)}</div>`;
  }
  return h;
}
function renderMessages() {
  const box = $("#messages"); box.innerHTML = "";
  const msgs = state.chat ? state.chat.messages : [];
  if (!msgs.length) { box.append(el("div", "empty", `<div class="empty-title">What do you need?</div><div class="muted small">Your private, local AI assistant.</div>`)); return; }
  msgs.forEach((m, i) => box.append(renderMsg(m, i)));
  box.scrollTop = box.scrollHeight;
}
function renderMsg(m, i) {
  if (m.role === "user") {
    const w = el("div", "msg user"); w.dataset.idx = i;
    const col = el("div"); col.style.maxWidth = "85%"; col.style.display = "flex"; col.style.flexDirection = "column"; col.style.alignItems = "flex-end";
    if ((m.images && m.images.length) || (m.files && m.files.length)) {
      const att = el("div", "att");
      (m.images || []).forEach((b, k) => { const img = el("img"); img.src = `data:${(m.imageTypes || [])[k] || "image/png"};base64,${b}`; att.append(img); });
      (m.files || []).forEach((f) => att.append(el("span", "chip", `📄 ${esc(f.name)}`)));
      col.append(att);
    }
    if (m.content) { const b = el("div", "bubble"); b.style.maxWidth = "100%"; b.textContent = m.content; col.append(b); }
    const meta = el("div", "meta", `${m.web ? "🌐 web · " : ""}${esc(fmtTime(m.ts))}`);
    col.append(meta); w.append(col); return w;
  }
  if (m.role === "tool") {
    const r = m.result || {};
    const w = el("div", "output"); w.dataset.idx = i;
    const status = r.timed_out ? "timed out" : `exit ${r.exit_code}`;
    w.innerHTML = `<div class="o-head"><span>python output · ${esc(status)} · ${r.duration_ms ?? "?"} ms</span><span class="spacer"></span></div><pre>${esc(r.stdout || "")}${r.stderr ? `<span class="err">${esc(r.stderr)}</span>` : ""}${!r.stdout && !r.stderr ? '<span class="muted">(no output)</span>' : ""}</pre>`;
    if (i === state.chat.messages.length - 1 && !state.streaming) {
      const b = el("button", "btn-ghost", "Send output to model"); b.dataset.act = "continue"; b.style.margin = "0 0 6px 12px"; b.style.fontSize = "12px";
      w.append(b);
    }
    return w;
  }
  if (m.role === "notice") return el("div", "notice", m.html);
  const w = el("div", "msg assistant"); w.dataset.idx = i;
  const b = el("div", "bubble"); b.innerHTML = renderAssistantInner(m, false); w.append(b);
  const last = i === state.chat.messages.length - 1;
  const note = last && !state.streaming ? turnNote(m) : null;
  let actions = `<button data-act="copymsg">Copy</button>`;
  if (last && !(note && note.action)) actions += `<button data-act="regen">Regenerate</button>`;
  if (note && note.action === "retry-noreason") actions += `<button class="recovery" type="button" data-act="retry-noreason">Retry without reasoning</button>`;
  if (note && note.action === "retry-same") actions += `<button class="recovery" type="button" data-act="retry-same">Retry</button>`;
  const flag = m.stopped ? " · stopped" : note && note.kind === "timeout" ? " · timed out" : note && note.kind === "empty_final" ? " · no answer" : "";
  actions += `<span class="meta" style="margin:2px 6px 0">${esc(m.model || "")}${flag} · ${esc(fmtTime(m.ts))}</span>`;
  w.append(el("div", "msg-actions", actions));
  return w;
}
function notice(html) {
  const box = $("#messages"); const e = $("#empty") || box.querySelector(".empty"); if (e) e.remove();
  box.append(el("div", "notice", html)); box.scrollTop = box.scrollHeight;
}
function setStatus(t) { $("#status").textContent = t || ""; }

// ------------------------------------------------------------------ attachments
function renderAttachments() {
  const a = $("#attachments"); a.innerHTML = "";
  state.pending.forEach((p, i) => {
    const c = el("span", "chip");
    c.innerHTML = p.kind === "image" ? `<img src="data:${p.type};base64,${p.b64}"> ${esc(p.name)}` : `📄 ${esc(p.name)} <span class="muted">${esc(p.info || "")}</span>`;
    const x = el("button", "", "✕"); x.title = "Remove"; x.onclick = () => { state.pending.splice(i, 1); renderAttachments(); };
    c.append(x); a.append(c);
  });
}
function readAs(file, how) { return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r[how](file); }); }
async function imageToB64(file) {
  const url = await readAs(file, "readAsDataURL");
  const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
  const max = 1536, s = Math.min(1, max / Math.max(img.width, img.height));
  const type = file.type === "image/png" ? "image/png" : "image/jpeg";
  if (s === 1 && /^image\/(png|jpeg)$/.test(file.type)) return { b64: url.split(",")[1], type: file.type };
  const cv = document.createElement("canvas"); cv.width = Math.round(img.width * s); cv.height = Math.round(img.height * s);
  cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
  return { b64: cv.toDataURL(type, 0.9).split(",")[1], type };
}
const fmtChars = (n) => (n < 1000 ? `${n} chars` : `${Math.round(n / 1000)}k chars`);
let pdfjsP;
async function pdfText(file) {
  pdfjsP = pdfjsP || import("./vendor/pdf.min.mjs").then((m) => { m.GlobalWorkerOptions.workerSrc = "./vendor/pdf.worker.min.mjs"; return m; });
  const pdfjs = await pdfjsP;
  const data = new Uint8Array(await readAs(file, "readAsArrayBuffer"));
  const task = pdfjs.getDocument({ data, isEvalSupported: false, disableFontFace: true, useSystemFonts: false });
  const doc = await task.promise;
  let out = "";
  for (let p = 1; p <= doc.numPages && out.length < MAX_FILE_CHARS; p++) {
    const tc = await (await doc.getPage(p)).getTextContent();
    out += `\n--- page ${p} ---\n` + tc.items.map((it) => it.str + (it.hasEOL ? "\n" : " ")).join("");
  }
  const pages = doc.numPages; try { await task.destroy(); } catch {}
  return { text: out.slice(0, MAX_FILE_CHARS), pages, truncated: out.length > MAX_FILE_CHARS };
}
async function addFiles(files) {
  for (const f of files) {
    try {
      if (f.type.startsWith("image/")) {
        const { b64, type } = await imageToB64(f);
        state.pending.push({ kind: "image", name: f.name || "pasted image", b64, type });
      } else if (f.type === "application/pdf" || /\.pdf$/i.test(f.name)) {
        setStatus(`Reading ${f.name}…`);
        const r = await pdfText(f);
        if (!r.text.replace(/--- page \d+ ---|\s/g, "")) notice(`⚠ ${esc(f.name)} has no extractable text (scanned PDF?). OCR isn't supported in v1.0.`);
        state.pending.push({ kind: "file", name: f.name, text: r.text, info: `${r.pages} pages · ${fmtChars(r.text.length)}${r.truncated ? " (truncated)" : ""}` });
      } else {
        if (f.size > 2e6) { notice(`⚠ ${esc(f.name)} is too large for a text attachment.`); continue; }
        const t = await readAs(f, "readAsText");
        state.pending.push({ kind: "file", name: f.name, text: t.slice(0, MAX_FILE_CHARS), info: `${fmtChars(t.length)}` });
      }
    } catch (e) { notice(`⚠ Couldn't read ${esc(f.name)}: ${esc(e.message || e)}`); }
  }
  setStatus(""); renderAttachments();
}

// ------------------------------------------------------------------ sending
function toOllamaMessages(model, caps, chat, think) {
  const reason = think === undefined ? !!state.settings.reason : !!think;
  let sys = state.settings.systemPrompt || DEFAULT_SYSTEM_PROMPT;
  if (state.settings.code) sys += CODE_PROMPT;
  if (reason && !caps.includes("thinking")) sys += REASON_PROMPT;
  const out = [{ role: "system", content: sys }];
  const vision = caps.includes("vision");
  for (const m of chat.messages) {
    if (m.role === "user") {
      let content = "";
      for (const f of m.files || []) content += `[Attached file: ${f.name}]\n${f.text}\n[End of file]\n\n`;
      if (m.context) content += m.context + "\n\n";
      content += m.content || (m.images && m.images.length ? "Describe this image." : "");
      const msg = { role: "user", content };
      if (vision && m.images && m.images.length) msg.images = m.images;
      out.push(msg);
    } else if (m.role === "assistant") {
      if (m.omitFromModel) continue;
      if (!splitThink(m.content, m.thinking).c.trim() && m.errorKind === "empty_final") continue;
      out.push({ role: "assistant", content: splitThink(m.content, "").c });
    } else if (m.role === "tool") {
      const r = m.result || {};
      out.push({ role: "user", content: `Output of the Python code (exit ${r.timed_out ? "timeout" : r.exit_code}):\n\`\`\`\n${(r.stdout || "") + (r.stderr ? "\n[stderr]\n" + r.stderr : "") || "(no output)"}\n\`\`\`\nUse this output to answer.` });
    }
  }
  return out;
}

async function webContext(query, signal) {
  setStatus("Searching the web…");
  const d = await api("/tools/search", { method: "POST", body: { query, fetch_pages: state.settings.fetchPages ? 2 : 0, max_results: 5 }, signal });
  const res = d.results || [];
  if (!res.length) throw new Error("no results (DuckDuckGo may be rate-limiting)");
  const today = new Date().toISOString().slice(0, 10);
  let ctx = `Web search results (retrieved ${today}). Use them if relevant and cite sources inline as [1], [2], etc. Ignore irrelevant results.\n`;
  res.forEach((r, i) => {
    ctx += `\n[${i + 1}] ${r.title}\nURL: ${r.url}\n${r.snippet}\n`;
    if (r.text) ctx += `Page excerpt: ${r.text.slice(0, 2500)}\n`;
  });
  return { context: ctx + "\nQuestion:", sources: res.map((r) => ({ title: r.title, url: r.url })) };
}

async function send() {
  if (state.streaming || state.searching) return;
  const input = $("#input");
  const text = input.value.trim();
  if (!text && !state.pending.length) return;
  if (!state.ollamaOk) { await loadModels(); if (!state.ollamaOk) return; }
  const images = state.pending.filter((p) => p.kind === "image");
  let model = $("#modelSelect").value;
  let switched = "";
  // Auto-pick best installed model for this request (unless user just overrode the dropdown).
  if (state.settings.autoModel && !state.userPickedModel && state.models.length) {
    const { model: auto, task } = pickAutoModel(text, images.length > 0);
    if (auto && auto !== model) {
      switched = `Auto: switched to <b>${esc(auto)}</b> for ${TASK_NOTICE[task] || task.toLowerCase()}.`;
      model = auto;
      $("#modelSelect").value = auto;
      state.settings.lastModel = auto;
      saveSettings();
    } else if (auto) {
      model = auto;
    }
  }
  state.userPickedModel = false;
  if (!model) { notice("Pick a model first."); return; }
  let caps = await getCaps(model);
  if (images.length && !caps.includes("vision")) {
    let vm = null;
    if (state.settings.autoModel) vm = firstInstalled(groupRanking("Images"));
    if (!vm || !(await getCaps(vm)).includes("vision")) vm = await findVisionModel();
    if (!vm) {
      notice(`⚠ <b>${esc(model)}</b> can't see images and no vision model is installed. Run <code>ollama pull moondream</code> (small) or <code>ollama pull qwen2.5vl:3b</code> / <code>gemma3:4b</code> (better), then retry.`);
      return;
    }
    switched = state.settings.autoModel
      ? `Auto: switched to <b>${esc(vm)}</b> for images.`
      : `Switched to <b>${esc(vm)}</b> because <b>${esc(model)}</b> doesn't support images.`;
    model = vm; $("#modelSelect").value = vm; state.settings.lastModel = vm; saveSettings();
    caps = await getCaps(vm); await updateCaps();
  } else if (switched) {
    await updateCaps();
  }
  if (!state.chat) {
    const title = (text || state.pending[0]?.name || "New chat").replace(/\s+/g, " ").slice(0, 60);
    state.chat = { id: uid(), title, created: Date.now(), updated: Date.now(), model, messages: [] };
  }
  if (switched) state.chat.messages.push({ role: "notice", html: switched, ts: Date.now() });
  const um = { role: "user", content: text, ts: Date.now() };
  if (images.length) { um.images = images.map((p) => p.b64); um.imageTypes = images.map((p) => p.type); }
  const files = state.pending.filter((p) => p.kind === "file");
  if (files.length) um.files = files.map((f) => ({ name: f.name, text: f.text }));
  input.value = ""; autosize(); state.pending = []; renderAttachments();
  let sources = [];
  state.chat.messages.push(um);
  renderMessages();
  const prep = new AbortController();
  const prepId = ++state.generation;
  state.streaming = { genId: prepId, ctrl: prep, reason: null, chatId: state.chat.id };
  setSending(true);
  if (state.settings.web && text) {
    um.web = true; state.searching = true;
    try { const w = await webContext(text, prep.signal); um.context = w.context; sources = w.sources; }
    catch (e) {
      if (prep.signal.aborted || e.name === "AbortError") {
        state.chat.messages.push({ role: "notice", html: "Stopped before the reply started.", ts: Date.now() });
      } else {
        state.chat.messages.push({ role: "notice", html: `⚠ Web search failed (${esc(e.message)}). Answered without it.`, ts: Date.now() });
      }
    }
    setStatus(""); state.searching = false;
  }
  if (prep.signal.aborted) {
    if (state.streaming && state.streaming.genId === prepId) { state.streaming = null; setSending(false); }
    await saveChat(state.chat);
    renderMessages();
    return;
  }
  if (state.streaming && state.streaming.genId === prepId) state.streaming = null;
  await saveChat();
  await generate(model, caps, { sources });
}

function timeoutFromPayload(j) {
  return { errorKind: "timeout", phase: j.phase || "total", limit_s: j.limit_s, error: "generation_timeout" };
}

async function generate(model, caps, { sources = [], followup = false, thinkOverride, recovery = false } = {}) {
  const chat = state.chat;
  if (!chat) return;
  chat.model = model;
  const requestThink = thinkOverride === undefined ? !!state.settings.reason : !!thinkOverride;
  const am = { role: "assistant", content: "", thinking: "", model, ts: Date.now(), sources, requestThink };
  const body = {
    model, messages: toOllamaMessages(model, caps, chat, requestThink), stream: true,
    options: { temperature: Number(state.settings.temperature) },
  };
  if (caps.includes("thinking")) body.think = requestThink;
  chat.messages.push(am);
  if (state.chat === chat) renderMessages();
  const node = state.chat === chat ? $("#messages").lastElementChild : null;
  const bubble = node ? node.querySelector(".bubble") : null;
  const ctrl = new AbortController();
  const genId = ++state.generation;
  state.streaming = { genId, ctrl, reason: null, chatId: chat.id };
  setSending(true); setStatus("Generating…");
  const limits = state.limits || DEFAULT_LIMITS;
  const backstop = setTimeout(() => {
    if (state.streaming && sameGeneration(state.streaming.genId, genId)) {
      state.streaming.reason = "timeout";
      ctrl.abort();
    }
  }, (Number(limits.total_s) + 15) * 1000);
  let raf = 0;
  const paint = () => {
    raf = 0;
    if (state.chat !== chat || !bubble) return;
    bubble.innerHTML = renderAssistantInner(am, true);
    const box = $("#messages");
    if (box.scrollHeight - box.scrollTop - box.clientHeight < 200) box.scrollTop = box.scrollHeight;
  };
  const applyLine = (j) => {
    if (!sameGeneration(state.streaming && state.streaming.genId, genId)) return;
    if (j.error === "generation_timeout") {
      Object.assign(am, timeoutFromPayload(j));
      return;
    }
    if (j.error) throw new Error(typeof j.error === "string" ? j.error : "generation failed");
    if (j.message) { am.content += j.message.content || ""; am.thinking += j.message.thinking || ""; }
    if (j.done) { am.stats = { eval_count: j.eval_count, eval_ms: Math.round((j.eval_duration || 0) / 1e6) }; }
    if (!raf) raf = requestAnimationFrame(paint);
  };
  try {
    const r = await fetch("/api/chat", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctrl.signal });
    if (r.status === 401) { showPairScreen(); throw new Error("This phone needs to pair again."); }
    if (!r.ok) {
      let msg = r.statusText, payload = null;
      try { payload = await r.json(); msg = payload.error === "ollama_unreachable" ? `Can't reach Ollama at ${payload.ollama}. Is it running?` : (payload.error || msg); } catch {}
      if (payload && payload.error === "generation_timeout") Object.assign(am, timeoutFromPayload(payload));
      else throw new Error(msg);
    } else {
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done || !sameGeneration(state.streaming && state.streaming.genId, genId)) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
          if (!line) continue;
          let j; try { j = JSON.parse(line); } catch { continue; }
          applyLine(j);
          if (am.errorKind === "timeout") break;
        }
        if (am.errorKind === "timeout") break;
      }
    }
  } catch (e) {
    const reason = state.streaming && state.streaming.reason;
    if (e.name === "AbortError" || ctrl.signal.aborted) {
      if (reason === "timeout" && am.errorKind !== "timeout") am.errorKind = "timeout";
      else if (am.errorKind !== "timeout") { am.stopped = true; am.errorKind = "cancelled"; }
    } else if (!am.errorKind) {
      am.error = /does not support thinking/i.test(e.message) ? e.message + " (turn off Reasoning)" : e.message;
      am.errorKind = "error";
    }
  } finally {
    clearTimeout(backstop);
    cancelAnimationFrame(raf);
    if (!am.stopped && am.errorKind !== "timeout" && am.errorKind !== "error" && am.errorKind !== "cancelled") {
      const note = turnNote(am);
      if (note && note.kind === "empty_final") am.errorKind = "empty_final";
    }
    if (state.streaming && sameGeneration(state.streaming.genId, genId)) state.streaming = null;
    setSending(!!state.streaming);
    if (state.chat === chat) {
      setStatus(am.stats && am.stats.eval_ms ? `${(am.stats.eval_count / (am.stats.eval_ms / 1000)).toFixed(1)} tok/s` : "");
      renderMessages();
    }
    if (!am.thinking) delete am.thinking;
    await saveChat(chat);
  }
  if (!am.stopped && !am.errorKind && !am.error && !followup && !recovery && state.settings.code && state.settings.autoRun && state.chat === chat) {
    const code = firstPython(am.content);
    if (code) { await runCode(code); await generate(model, caps, { followup: true }); }
  }
}

async function retryAssistant(mode) {
  if (state.streaming || state.searching || !state.chat) return;
  const plan = planRetry(state.chat.messages, mode);
  if (!plan.ok) return;
  state.chat.messages[plan.assistantIndex].omitFromModel = true;
  const failed = state.chat.messages[plan.assistantIndex];
  const model = failed.model || $("#modelSelect").value;
  if (!model) return;
  await generate(model, await getCaps(model), { sources: plan.sources, thinkOverride: plan.think, recovery: true });
}
function firstPython(s) { const m = (s || "").match(/```(?:python|py|python3)[^\n]*\n([\s\S]*?)```/i); return m ? m[1] : null; }

async function runCode(code) {
  setStatus("Running Python…");
  let result;
  try { result = await api("/tools/run", { method: "POST", body: { code } }); }
  catch (e) { result = { stdout: "", stderr: `Runner error: ${e.message}`, exit_code: -1 }; }
  setStatus("");
  state.chat.messages.push({ role: "tool", code, result, ts: Date.now() });
  renderMessages(); await saveChat();
}

function stop() {
  if (!state.streaming) return;
  if (state.streaming.reason !== "timeout") state.streaming.reason = "cancelled";
  state.streaming.ctrl.abort();
}
function setSending(on) { const b = $("#sendBtn"); b.textContent = on ? "■" : "↑"; b.title = on ? "Stop" : "Send"; b.classList.toggle("stop", on); }
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 240) + "px"; }

// ------------------------------------------------------------------ events
function bind() {
  $("#sendBtn").onclick = () => (state.streaming ? stop() : send());
  const input = $("#input");
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); state.streaming ? null : send(); } });
  input.addEventListener("input", autosize);
  document.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.items || [])].filter((i) => i.kind === "file").map((i) => i.getAsFile()).filter(Boolean);
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  $("#attachBtn").onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = (e) => { addFiles([...e.target.files]); e.target.value = ""; };
  const comp = $("#composer");
  comp.addEventListener("dragover", (e) => { e.preventDefault(); comp.classList.add("drag"); });
  comp.addEventListener("dragleave", () => comp.classList.remove("drag"));
  comp.addEventListener("drop", (e) => { e.preventDefault(); comp.classList.remove("drag"); addFiles([...e.dataTransfer.files]); });
  $("#newChat").onclick = newChat;
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "o") { e.preventDefault(); newChat(); }
    if (e.key === "Escape") {
      if (!$("#lanModal").hidden) { closeLanModal(); return; }
      if (!$("#settingsModal").hidden) $("#settingsModal").hidden = true; else stop();
    }
  });
  $("#chatList").onclick = (e) => {
    const d = e.target.closest("[data-del]");
    if (d) { e.stopPropagation(); if (confirm("Delete this chat?")) deleteChat(d.dataset.del); return; }
    const it = e.target.closest(".chat-item"); if (it) openChat(it.dataset.id);
  };
  $("#toggleSidebar").onclick = () => $("#app").classList.toggle("collapsed");
  $("#modelSelect").onchange = async () => { state.settings.lastModel = $("#modelSelect").value; state.userPickedModel = true; saveSettings(); await updateCaps(); };
  for (const [id, key] of [["#tWeb", "web"], ["#tReason", "reason"], ["#tCode", "code"], ["#tAuto", "autoModel"]]) {
    const node = $(id); if (!node) continue;
    node.onchange = () => {
      state.settings[key] = node.checked; saveSettings(); updateHint();
      if (key === "code") renderMessages();
      if (key === "autoModel" && node.checked) state.userPickedModel = false;
    };
  }
  $("#banner").onclick = (e) => { if (e.target.closest("[data-act=retry]")) loadModels(); };
  $("#messages").onclick = async (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const act = b.dataset.act;
    if (act === "copy") { copy(b.closest(".codeblock").querySelector("code").textContent, b); }
    else if (act === "run") { if (state.streaming) return; b.disabled = true; await runCode(b.closest(".codeblock").querySelector("code").textContent); }
    else if (act === "copymsg") { const i = +b.closest("[data-idx]").dataset.idx; copy(splitThink(state.chat.messages[i].content, "").c, b); }
    else if (act === "retry-noreason") { await retryAssistant("without_reasoning"); }
    else if (act === "retry-same") { await retryAssistant("same"); }
    else if (act === "regen") {
      if (state.streaming) return;
      const msgs = state.chat.messages; const last = msgs[msgs.length - 1];
      if (last && last.role === "assistant") msgs.pop();
      const model = $("#modelSelect").value;
      await generate(model, await getCaps(model), { sources: (last && last.sources) || [] });
    } else if (act === "continue") {
      if (state.streaming) return;
      const model = $("#modelSelect").value; await generate(model, await getCaps(model), { followup: true });
    }
  };
  // settings modal
  $("#openSettings").onclick = () => {
    $("#sysPrompt").value = state.settings.systemPrompt; $("#temperature").value = state.settings.temperature;
    $("#tempVal").textContent = state.settings.temperature; $("#autoRun").checked = !!state.settings.autoRun; $("#fetchPages").checked = !!state.settings.fetchPages;
    $("#settingsModal").hidden = false;
  };
  $("#temperature").oninput = () => ($("#tempVal").textContent = $("#temperature").value);
  $("#closeSettings").onclick = () => ($("#settingsModal").hidden = true);
  $("#settingsModal").onclick = (e) => { if (e.target.id === "settingsModal") $("#settingsModal").hidden = true; };
  $("#resetPrompt").onclick = () => ($("#sysPrompt").value = DEFAULT_SYSTEM_PROMPT);
  $("#saveSettings").onclick = () => {
    Object.assign(state.settings, { systemPrompt: $("#sysPrompt").value.trim() || DEFAULT_SYSTEM_PROMPT, temperature: Number($("#temperature").value), autoRun: $("#autoRun").checked, fetchPages: $("#fetchPages").checked });
    saveSettings(); $("#settingsModal").hidden = true;
  };
  $("#clearAll").onclick = async () => {
    if (!confirm("Delete ALL chats? This can't be undone.")) return;
    for (const c of state.chats) await api(`/store/chats/${c.id}`, { method: "DELETE" }).catch(() => {});
    state.chat = null; await loadChats(); renderMessages(); $("#settingsModal").hidden = true;
  };
  $("#main").addEventListener("click", (e) => {
    if (e.target.closest("#toggleSidebar")) return;
    if (window.matchMedia("(max-width: 760px)").matches && !$("#app").classList.contains("collapsed")) {
      $("#app").classList.add("collapsed");
    }
  });
  $("#openLan").onclick = () => openLanModal();
  $("#closeLan").onclick = () => closeLanModal();
  $("#lanModal").onclick = (e) => { if (e.target.id === "lanModal") closeLanModal(); };
  $("#pairBtn").onclick = () => submitPair();
  $("#pairCode").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submitPair(); } });
}
function copy(text, btn) {
  const done = () => { const o = btn.textContent; btn.textContent = "Copied"; setTimeout(() => (btn.textContent = o), 1200); };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback); else fallback();
  function fallback() { const t = el("textarea"); t.value = text; document.body.append(t); t.select(); document.execCommand("copy"); t.remove(); done(); }
}

// ------------------------------------------------------------------ iPhone pairing
function node(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function fmtLeft(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}
function showPairScreen() {
  if (state.pairing) return;
  state.pairing = true;
  $("#app").hidden = true;
  $("#pairScreen").hidden = false;
  $("#lanModal").hidden = true;
  $("#settingsModal").hidden = true;
  const input = $("#pairCode");
  if (input) setTimeout(() => input.focus(), 50);
  window.__lcReady = true;
}
async function submitPair() {
  const err = $("#pairError");
  const code = $("#pairCode").value.trim();
  err.hidden = true;
  if (!code) { err.textContent = "Enter the code from the PC."; err.hidden = false; return; }
  try {
    const r = await fetch("/lan/pair", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, label: "iPhone" }),
    });
    let data = {};
    try { data = await r.json(); } catch { /* ignore */ }
    if (!r.ok) { err.textContent = (data && data.error) || "Couldn't pair."; err.hidden = false; return; }
    location.reload();
  } catch {
    err.textContent = "Couldn't reach the PC. Check the address and that both devices are on the same Wi-Fi.";
    err.hidden = false;
  }
}
async function disconnectPhone() {
  try { await api("/lan/logout", { method: "POST", body: {} }); } catch (e) { if (!e || e.status !== 401) return; }
  location.reload();
}
function maybeIosHint() {
  const ua = navigator.userAgent || "";
  const ios = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (!ios || localStorage.getItem("lc.iosHint") || !$("#banner").hidden) return;
  const bar = $("#banner");
  bar.hidden = false;
  bar.innerHTML = `To keep LocalVault AI on your Home Screen, tap Share, then Add to Home Screen. If that icon asks for a code, enter a new one from the PC. <button class="btn-ghost" type="button" id="dismissIos">OK</button>`;
  const dismiss = $("#dismissIos");
  if (dismiss) dismiss.onclick = () => { localStorage.setItem("lc.iosHint", "1"); banner(null); };
}
function setupAccess(status) {
  state.access = status || null;
  const btn = $("#openLan");
  if (!status) { btn.hidden = true; return; }
  if (status.loopback) {
    btn.hidden = false;
    btn.textContent = "iPhone";
    btn.onclick = () => openLanModal();
    return;
  }
  if (status.paired) {
    btn.hidden = false;
    btn.textContent = "Disconnect";
    btn.onclick = () => disconnectPhone();
    const badge = $("#localBadge");
    badge.textContent = "● this Wi-Fi";
    badge.title = "Paired to this PC on your Wi-Fi. Chats stay on the computer.";
  }
}
function addressBlock(entry, live) {
  const wrap = node("div", "");
  wrap.append(node("div", "muted small", (entry.preferred ? "Use this address" : (entry.name || "Address")) + (live ? "" : " (not listening yet)")));
  wrap.append(node("div", "lan-url", entry.url || ""));
  if (live && entry.url) {
    const btn = node("button", "btn-ghost", "Copy address");
    btn.type = "button";
    btn.onclick = () => copy(entry.url, btn);
    wrap.append(btn);
  }
  return wrap;
}
function renderLan(status, codeInfo) {
  const body = $("#lanBody");
  body.textContent = "";
  if (!status || !status.loopback) {
    body.append(node("p", "lan-note", "iPhone settings are only available on this PC."));
    return;
  }
  body.append(node("p", "lan-note", "A paired iPhone can read and write chats on this PC, use your models, search the web, and run Python here. Only devices on this PC's private subnet can try, and only a phone that enters the code gets in. Traffic on the Wi-Fi is not encrypted. Use a trusted network, not a public or guest network. Ollama stays on this PC. Closing LocalVault AI turns iPhone access off."));
  if (!status.lan) {
    const label = node("label", "check");
    const box = document.createElement("input");
    box.type = "checkbox"; box.id = "lanAck";
    label.append(box, document.createTextNode(" I understand a paired iPhone can use chats, search, and code on this PC."));
    body.append(label);
    const err = node("p", "pair-error", "");
    err.id = "lanError"; err.hidden = true;
    body.append(err);
    const go = node("button", "btn-primary", "Allow iPhone on this Wi-Fi");
    go.type = "button"; go.onclick = () => enableLan();
    body.append(go);
    const preview = status.addresses || [];
    if (!preview.length) body.append(node("p", "muted small", "No private Wi-Fi address found. Connect this PC to Wi-Fi, then open this panel again."));
    else {
      body.append(node("p", "muted small", "When this is on, Safari on the iPhone will use:"));
      preview.forEach((entry) => body.append(addressBlock(entry, false)));
    }
    return;
  }
  body.append(node("p", "lan-note", "iPhone access is on. In Safari, type the address below, then enter the code. Do not put the code in the address."));
  (status.addresses || []).forEach((entry) => body.append(addressBlock(entry, true)));
  (status.warnings || []).forEach((warning) => body.append(node("p", "pair-error", String(warning))));
  if (codeInfo && codeInfo.code) {
    body.append(node("p", "field-label", "Pairing code"));
    body.append(node("div", "pair-code", codeInfo.code));
    const left = codeInfo.expires_in != null ? codeInfo.expires_in : status.code_expires_in;
    body.append(node("p", "muted small", "Works once, for about " + fmtLeft(left) + ". It is shown here only, not in the link or the log."));
  } else if (status.has_code) body.append(node("p", "muted small", "A pairing code is active. Close this panel and open it again if it is not shown."));
  else if (status.code_burned) body.append(node("p", "pair-error", "Too many wrong attempts. Create a new code."));
  else body.append(node("p", "muted small", "The last code was used or it expired."));
  const actions = node("div", "lan-actions");
  const newer = node("button", "btn-ghost", "New code");
  newer.type = "button";
  newer.onclick = async () => { try { await api("/lan/code", { method: "POST", body: {} }); await refreshLan(); } catch (e) { window.alert(e.message); } };
  const stop = node("button", "btn-danger", "Stop iPhone access");
  stop.type = "button";
  stop.onclick = async () => {
    if (!window.confirm("Stop iPhone access and disconnect paired phones?")) return;
    try { await api("/lan/disable", { method: "POST", body: {} }); await refreshLan(); } catch (e) { window.alert(e.message); }
  };
  actions.append(newer, stop);
  body.append(actions);
  body.append(node("p", "muted small", "If the iPhone cannot connect, Windows is probably blocking it. In an Administrator PowerShell run deploy\\Enable-LocalChatLanFirewall.ps1. That rule is Private networks only, this app's port only, and your local subnet only. It does not open Ollama and it does not change Public or Private. Closing this window also stops iPhone access. Remove the firewall rule later with Disable-LocalChatLanFirewall.ps1."));
  const sessions = status.sessions || [];
  if (sessions.length) {
    body.append(node("p", "field-label", "Paired phones"));
    sessions.forEach((session) => {
      const row = node("div", "lan-session");
      row.append(node("span", "", (session.label || "iPhone") + " · " + (session.ip || "")));
      const rev = node("button", "btn-ghost", "Revoke");
      rev.type = "button";
      rev.onclick = async () => { try { await api("/lan/revoke", { method: "POST", body: { id: session.id } }); await refreshLan(); } catch (e) { window.alert(e.message); } };
      row.append(rev);
      body.append(row);
    });
  }
}
async function enableLan() {
  const ack = $("#lanAck");
  const err = $("#lanError");
  if (!ack || !ack.checked) {
    if (err) { err.textContent = "Check the box first. A paired iPhone can use chats, models, web search, and Python on this PC."; err.hidden = false; }
    return;
  }
  try {
    const info = await api("/lan/enable", { method: "POST", body: { acknowledge: true } });
    renderLan(Object.assign({ loopback: true, sessions: [] }, info), { code: info.code, expires_in: info.code_expires_in });
  } catch (e) {
    if (err) { err.textContent = e.message || "Could not turn on iPhone access."; err.hidden = false; }
  }
}
let lanPoll = 0;
function closeLanModal() {
  $("#lanModal").hidden = true;
  clearInterval(lanPoll);
  lanPoll = 0;
}
async function refreshLan() {
  if ($("#lanModal").hidden) return;
  let status;
  try { status = await api("/lan/status"); } catch { return; }
  let codeInfo = null;
  if (status && status.loopback && status.has_code) {
    try { codeInfo = await api("/lan/code"); } catch { codeInfo = null; }
  }
  renderLan(status, codeInfo);
}
function openLanModal() {
  $("#lanModal").hidden = false;
  refreshLan();
  clearInterval(lanPoll);
  lanPoll = setInterval(refreshLan, 2000);
}
function bindViewport() {
  const vv = window.visualViewport;
  const root = document.documentElement;
  const apply = () => {
    const height = vv ? Math.round(vv.height) : window.innerHeight;
    const top = vv ? Math.round(vv.offsetTop) : 0;
    const kb = vv ? Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)) : 0;
    root.style.setProperty("--app-height", height + "px");
    root.style.setProperty("--vv-top", top + "px");
    root.style.setProperty("--kb", kb + "px");
    root.classList.add("vv");
  };
  apply();
  if (!vv) return;
  vv.addEventListener("resize", apply);
  vv.addEventListener("scroll", apply);
  window.addEventListener("orientationchange", () => setTimeout(apply, 250));
  $("#input").addEventListener("focus", () => setTimeout(() => {
    apply();
    const box = $("#messages");
    if (box) box.scrollTop = box.scrollHeight;
  }, 300));
}

// ------------------------------------------------------------------ boot
(async function boot() {
  bind();
  bindViewport();
  const phoneQuery = window.matchMedia("(max-width: 760px)");
  const syncPhoneLayout = () => {
    if (phoneQuery.matches) {
      $("#app").classList.add("collapsed");
      $("#input").placeholder = "Message…";
    } else {
      $("#app").classList.remove("collapsed");
      $("#input").placeholder = "Message… (Enter to send, Shift+Enter for newline, paste images)";
    }
  };
  syncPhoneLayout();
  if (phoneQuery.addEventListener) phoneQuery.addEventListener("change", syncPhoneLayout);
  else if (phoneQuery.addListener) phoneQuery.addListener(syncPhoneLayout);
  let status = null;
  try {
    const health = await api("/health");
    state.limits = health.generation || DEFAULT_LIMITS;
    state.version = health.version || "1.3";
  } catch { state.limits = DEFAULT_LIMITS; state.version = "1.3"; }
  const badge = $("#localBadge");
  if (badge) {
    badge.textContent = `● local · v${state.version}`;
    badge.title = `LocalVault AI ${state.version}. Chats stay on this PC.`;
  }
  try { status = await api("/lan/status"); } catch { status = null; }
  if (status && status.loopback === false && !status.paired) { showPairScreen(); return; }
  setupAccess(status);
  try {
    await loadSettings();
    await Promise.all([loadModels(), loadChats()]);
  } catch (e) {
    if (e && e.status === 401) return;
  }
  renderMessages(); updateHint(); maybeIosHint();
  if (!window.matchMedia("(max-width: 760px)").matches) $("#input").focus();
  window.__lcReady = true;
})();
