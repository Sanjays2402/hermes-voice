/* Hermes screen (docs/show-screen.md).
 *
 * Talks to Home Assistant over its websocket and the two commands hermes_voice adds:
 *   hermes_voice/screen/subscribe   the Echo's state, what was heard, Hermes' reply as it is written, its tools
 *   hermes_voice/screen/overview    scheduled jobs, recent sessions, gateway status
 *
 * URL options:
 *   ?device=<device id>   follow one voice device (default: every device, the one that spoke last)
 *   #token=<token>        a Home Assistant long-lived token; kept in this browser, then removed from the URL.
 *                         Without one, the login of Home Assistant's own frontend in this browser is used.
 *   ?hold=<seconds>       how long an answer stays up after the Echo goes idle (default 60)
 *   ?avatar=<url>         a picture to use instead of the default avatar
 *   ?eyes=<x>,<y>         where the eyes are in that picture (fractions, e.g. 0.5,0.4): the idle screen zooms there
 *   ?zoom=<n>             how far the idle screen zooms in (default 8)
 *   ?demo                 play a scripted conversation, no Home Assistant needed (?demo=idle: stay idle)
 */
(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  let holdMs = Math.max(5, Number(params.get("hold")) || 60) * 1000; // the demo shortens it
  const STORE = "hermesScreen.";
  const $ = (id) => document.getElementById(id);
  const body = document.body;

  // ---------------------------------------------------------------------------------- storage --
  const store = {
    get(key) { try { return localStorage.getItem(STORE + key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(STORE + key, value); } catch { /* private mode */ } },
  };

  // ------------------------------------------------------------------------------------- state --
  let connected = false;
  const state = {
    device: DEMO ? null : params.get("device") || store.get("device") || null, // null: every device
    focus: null, // the device shown when following every device
    satellite: {}, // device id -> idle | listening | processing | responding | unavailable
    turn: emptyTurn(),
    mode: "offline",
    holdUntil: 0,
    tz: undefined,
    locale: undefined,
    overview: null,
    overviewAt: 0,
    devices: {}, // device id -> {mute, dnd, ringing, ringing_names, can_stop} (hassmic's controls)
    timers: [], // running and paused voice timers of the followed device(s)
  };

  function emptyTurn() {
    return { heard: "", reply: "", tools: [], phase: "idle", by: "hermes", at: 0, error: "" };
  }

  // -------------------------------------------------------------------------------- rendering --
  const STATUS = {
    offline: "Offline",
    idle: "Hermes Voice",
    listening: "Listening",
    thinking: "Thinking",
    answering: "Answering",
    done: "Hermes Voice",
    error: "Something went wrong",
  };

  function setMode(mode) {
    if (state.mode === mode) return;
    state.mode = mode;
    body.dataset.mode = mode;
    $("status").textContent =
      ["answering", "done"].includes(mode) && state.turn.by === "home_assistant" ? "Home Assistant" : STATUS[mode];
    if (mode !== "idle" && mode !== "offline") closeOverview();
    renderTurn();
  }

  /** Decide what to show from the Echo's state and the current turn. */
  function updateMode() {
    if (!connected && !DEMO) return setMode("offline");
    const sat = state.satellite[state.device || state.focus] || "idle";
    const t = state.turn;
    if (sat === "listening") return setMode("listening");
    if (sat === "processing") return setMode(t.reply ? "answering" : t.phase === "error" ? "error" : "thinking");
    if (sat === "responding") return setMode(t.phase === "error" ? "error" : "answering");
    // idle, unavailable, or a typed turn with no voice device
    if (t.phase === "thinking") return setMode("thinking");
    if (t.phase === "answering") return setMode("answering");
    if (t.phase === "error" && Date.now() < state.holdUntil) return setMode("error");
    if (t.phase === "done" && Date.now() < state.holdUntil) return setMode("done");
    setMode("idle");
  }

  const TOOL_NAMES = {
    web_search: "Searching the web",
    web_extract: "Reading a page",
    x_search: "Searching X",
    kanban: "Checking the task board",
    browser: "Browsing",
    session_search: "Searching past conversations",
    memory: "Checking memory",
    memory_search: "Checking memory",
    terminal: "Running a command",
    execute_code: "Running code",
    read_file: "Reading a file",
    write_file: "Writing a file",
    patch: "Editing a file",
    search_files: "Searching files",
    vision_analyze: "Looking at an image",
    image_generate: "Making an image",
    homeassistant: "Talking to Home Assistant",
    cronjob: "Scheduling",
    delegate_task: "Handing off a task",
    todo: "Updating the plan",
  };

  function toolText(tool) {
    const name = TOOL_NAMES[tool.tool] || "Using " + String(tool.tool || "a tool").replace(/_/g, " ");
    return name + (tool.done ? "" : "…");
  }

  /** A web address shows as its site; anything else is cut to a chip's length. */
  function shortLabel(text) {
    const url = text.match(/^https?:\/\/([^/\s]+)/);
    if (url) return url[1].replace(/^www\./, "");
    return text.length > 48 ? text.slice(0, 47) + "\u2026" : text;
  }

  // The answer appears at about the pace it is spoken: Hermes writes it in a few seconds, Piper takes
  // 15-20 s to say it, and a screen that races ahead reads as "printed, then spoken". Measured on the
  // Echo Show 5 with Piper "ryan": about 13.5 characters a second, first sound about 0.6 s after the
  // voice starts (the satellite turns "responding"). Text Home Assistant answered itself, typed turns and
  // a finished reply show at once.
  const SPEAK_RATE = 13.5;
  const SPEAK_LEAD_MS = 600;
  function visibleText(t) {
    const dev = state.device || state.focus;
    if (t.by === "home_assistant" || t.spoken || state.satellite[dev] === undefined) return t.reply;
    if (!t.speakAt || !t.firstTextAt) return "";
    const start = Math.max(t.speakAt, t.firstTextAt) + SPEAK_LEAD_MS;
    const n = Math.floor(((Date.now() - start) / 1000) * SPEAK_RATE);
    if (n <= 0) return "";
    if (n >= t.reply.length) return t.reply;
    const end = t.reply.indexOf(" ", n); // whole words only
    return end < 0 ? t.reply : t.reply.slice(0, end);
  }
  setInterval(() => {
    const t = state.turn;
    if (["answering", "done"].includes(state.mode) && !t.spoken && t.reply && visibleText(t) !== t.shown) {
      t.shown = visibleText(t);
      renderTurn();
    }
  }, 150);

  function renderTurn() {
    const t = state.turn;
    body.dataset.by = t.by;
    $("heard").textContent = state.mode === "listening" ? "" : t.heard;
    const tools = $("tools");
    tools.replaceChildren(
      ...t.tools.slice(-4).map((tool) => {
        const el = document.createElement("span");
        el.className = "tool" + (tool.done ? " done" : "");
        const spin = document.createElement("i");
        spin.className = "spin";
        const label = document.createElement("small");
        label.textContent = shortLabel(tool.label || "");
        el.append(spin, toolText(tool), label);
        return el;
      }),
    );
    const reply = $("reply");
    if (state.mode === "error") {
      reply.innerHTML = '<p class="error-text">Hermes could not answer this time.</p>';
    } else if (state.mode === "listening" || state.mode === "thinking") {
      reply.innerHTML = "";
    } else {
      reply.innerHTML = (t.by === "home_assistant" ? '<div class="by-ha">Home Assistant</div>' : "") + markdown(visibleText(t));
    }
    fitReply();
  }

  /** Largest font that fits; while it is written, keep the newest words in view. */
  function fitReply() {
    const reply = $("reply");
    if (!reply.textContent.trim()) return;
    let size = 40;
    reply.style.fontSize = size + "px";
    while (reply.scrollHeight > reply.clientHeight + 1 && size > 24) {
      size -= 2;
      reply.style.fontSize = size + "px";
    }
    reply.scrollTop = reply.scrollHeight;
  }

  // A little markdown: Hermes writes for chat, the screen shows what speech left out.
  function markdown(text) {
    const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
    const host = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } };
    const inline = (s) =>
      esc(s)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
        .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, (_, t, u) => `<span class="link">${t}<small>${esc(host(u))}</small></span>`)
        .replace(/(^|[\s(])(https?:\/\/[^\s)<]+)/g, (_, pre, u) => `${pre}<span class="link">${esc(host(u))}</span>`);
    const out = [];
    let list = null;
    for (const raw of String(text || "").split(/\n/)) {
      const line = raw.trim();
      const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (item) {
        const kind = /^\d/.test(line) ? "ol" : "ul";
        if (!list || list.kind !== kind) { list = { kind, items: [] }; out.push(list); }
        list.items.push(inline(item[1]));
        continue;
      }
      list = null;
      if (!line) continue;
      const heading = line.replace(/^#{1,6}\s+/, "");
      out.push(heading !== line ? `<p><b>${inline(heading)}</b></p>` : `<p>${inline(line)}</p>`);
    }
    return out.map((b) => (typeof b === "string" ? b : `<${b.kind}>${b.items.map((i) => `<li>${i}</li>`).join("")}</${b.kind}>`)).join("");
  }

  // ------------------------------------------------------------------------------ clock, idle --
  function timeParts(date) {
    const opts = { hour: "numeric", minute: "2-digit" };
    if (state.tz) opts.timeZone = state.tz;
    const parts = new Intl.DateTimeFormat(state.locale, opts).formatToParts(date);
    const ampm = parts.find((p) => p.type === "dayPeriod");
    const time = parts.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim();
    return { time, ampm: ampm ? ampm.value : "" };
  }

  function relTime(when) {
    const t = typeof when === "number" ? when * (when < 1e12 ? 1000 : 1) : Date.parse(when);
    if (!t) return "";
    const s = Math.round((t - Date.now()) / 1000);
    const a = Math.abs(s);
    const rtf = new Intl.RelativeTimeFormat(state.locale, { numeric: "auto" });
    if (a < 45) return s > 5 ? "in a moment" : "just now";
    if (a < 3600) return rtf.format(Math.round(s / 60), "minute");
    if (a < 86400) return rtf.format(Math.round(s / 3600), "hour");
    return rtf.format(Math.round(s / 86400), "day");
  }

  function tick() {
    const now = new Date();
    const { time, ampm } = timeParts(now);
    $("clock").innerHTML = time + (ampm ? `<span class="ampm">${ampm}</span>` : "");
    $("corner-clock").textContent = time + (ampm ? " " + ampm : "");
    const dopts = { weekday: "long", month: "long", day: "numeric" };
    if (state.tz) dopts.timeZone = state.tz;
    $("date").textContent = new Intl.DateTimeFormat(state.locale, dopts).format(now);
    $("context").innerHTML = contextLine();
    if (connected && (state.mode === "idle" || !$("overview").hidden)) refreshOverview(false);
    if (state.holdUntil && Date.now() >= state.holdUntil) { state.holdUntil = 0; updateMode(); }
  }

  const escHtml = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);

  /** "12 min", "1 h 5 min" since an epoch-seconds time. */
  function since(sec) {
    const m = Math.max(1, Math.round((Date.now() / 1000 - sec) / 60));
    return m < 60 ? m + " min" : Math.floor(m / 60) + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
  }

  /** Background work, most urgent first: running, needs you, just finished (screen_activity.py via hermes_voice). */
  function workItems(parts = ["running", "attention", "recent"]) {
    const act = (state.overview && state.overview.activity) || {};
    const all = act.scope === "all";
    const who = (t) => (all && t.assignee ? " · " + escHtml(t.assignee) : "");
    const pick = (part) => (parts.includes(part) && act[part]) || [];
    return [
      ...pick("running").map((t) => `<span class="act run">●</span> <b>${escHtml(t.title)}</b>${who(t)} · ${since(t.started_at || Date.now() / 1000)}`),
      ...pick("attention").map((t) => `<span class="act need">●</span> ${t.status === "review" ? "Ready for your review" : "Blocked"}: <b>${escHtml(t.title)}</b>${who(t)}`),
      ...pick("recent").map((t) => `<span class="act done">✓</span> <b>${escHtml(t.title)}</b>${who(t)} · ${escHtml(relTime(t.completed_at))}`),
    ];
  }

  /** "4:32", "1:02:05" */
  function clock(sec) {
    const s = Math.max(0, Math.round(sec));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(r).padStart(2, "0");
  }

  /** Voice timers first (they are about now), counting down. */
  function timerItems() {
    return state.timers.map((t) => {
      const left = t.active && t.ends_at ? t.ends_at - Date.now() / 1000 : t.left;
      const name = t.name ? escHtml(t.name) + " timer" : "Timer";
      return `<span class="act timer">●</span> <b class="countdown">${clock(left)}</b> ${name}${t.active ? "" : " · paused"}`;
    });
  }

  /** The idle screen's lines: timers, background work, else the next scheduled job, else nothing. */
  function contextLine() {
    const esc = escHtml;
    const work = [...timerItems(), ...workItems()];
    if (work.length) return work.slice(0, 3).map((w) => `<div class="work">${w}</div>`).join("");
    const jobs = (state.overview && state.overview.jobs) || [];
    const next = jobs
      .filter((j) => j.enabled !== false && j.next_run_at)
      .sort((a, b) => Date.parse(a.next_run_at) - Date.parse(b.next_run_at))[0];
    if (next) return `Next: <b>${esc(next.name || "a scheduled job")}</b> ${esc(relTime(next.next_run_at))}`;
    if (state.mode === "offline") return "Waiting for Home Assistant\u2026";
    return "";
  }

  // ------------------------------------------------------- mute, do not disturb, ringing timer --
  function followed() {
    return state.devices[state.device || state.focus] || Object.values(state.devices)[0] || {};
  }

  function renderDevice() {
    const d = followed();
    body.dataset.muted = d.mute ? "1" : "0";
    const badges = [];
    if (d.mute) badges.push('<span class="badge mute">Mic off</span>');
    if (d.dnd) badges.push('<span class="badge">Do not disturb</span>');
    $("badges").innerHTML = badges.join("");
    // A ringing timer covers everything, with a Stop button (hassmic's own "Stop ringing").
    const ringingDev = Object.keys(state.devices).find((id) => state.devices[id].ringing && (!state.device || id === state.device));
    const ringing = ringingDev !== undefined;
    $("ringing").hidden = !ringing;
    if (ringing) {
      const names = state.devices[ringingDev].ringing_names;
      $("ringing-name").textContent = names ? names : "";
      $("stop-timer").dataset.device = ringingDev;
      $("stop-timer").hidden = !state.devices[ringingDev].can_stop;
    }
  }

  $("stop-timer").addEventListener("click", (e) => {
    e.stopPropagation();
    const id = e.currentTarget.dataset.device;
    if (DEMO) { state.devices[id] = { ...state.devices[id], ringing: false }; return renderDevice(); }
    call({ type: "hermes_voice/screen/stop_timer", device_id: id }).catch(() => {});
  });

  // ---------------------------------------------------------------------------- feed handling --
  function onEvent(ev) {
    const dev = ev.device_id === undefined ? null : ev.device_id;
    if (ev.type === "snapshot") {
      // Home Assistant came back with a different page (an update): load it. The first snapshot sets it.
      if (ev.page && state.page && ev.page !== state.page) return location.reload();
      state.page = state.page || ev.page;
      Object.assign(state.devices, ev.devices || {});
      renderDevice();
      Object.assign(state.satellite, ev.states || {});
      const turns = ev.turns || {};
      const key = state.device || latestDevice(turns);
      if (turns[key] !== undefined) {
        const t = turns[key];
        state.turn = { ...emptyTurn(), ...t, tools: (t.tools || []).map((x) => ({ ...x, done: !x.running })), at: (t.at || 0) * 1000, spoken: true };
        state.focus = key;
        if (t.phase === "done" || t.phase === "error") state.holdUntil = state.turn.at + holdMs;
      }
      renderTurn();
      return updateMode();
    }
    if (ev.type === "device") {
      state.devices[dev] = ev;
      return renderDevice();
    }
    if (ev.type === "timers") {
      state.timers = (ev.timers || []).filter((t) => !state.device || t.device_id === state.device);
      return tick();
    }
    if (ev.type === "state") {
      state.satellite[dev] = ev.state;
      if (!state.device && ev.state === "listening") state.focus = dev;
      if (ev.state === "listening" && dev === (state.device || state.focus)) {
        state.turn = { ...emptyTurn(), phase: "listening", at: Date.now() };
        renderTurn();
      }
      if (ev.state === "responding" && dev === (state.device || state.focus) && !state.turn.speakAt) {
        state.turn.speakAt = Date.now();
      }
      if (ev.state === "idle" && dev === (state.device || state.focus)) {
        state.turn.spoken = true; // the voice is done: show all of it
        if (["done", "error"].includes(state.turn.phase)) state.holdUntil = Date.now() + holdMs;
      }
      return updateMode();
    }
    if (!state.device) state.focus = dev; // following every device: show the one that is talking
    const t = state.turn;
    switch (ev.type) {
      case "heard":
        state.turn = { ...emptyTurn(), heard: ev.text || "", phase: "thinking", by: ev.by || "hermes", at: Date.now() };
        break;
      case "tool_start":
        t.tools = t.tools.filter((x) => x.id !== ev.id).concat({ id: ev.id, tool: ev.tool, label: ev.label, done: false });
        if (t.phase !== "answering") t.phase = "thinking";
        break;
      case "tool_end":
        t.tools = t.tools.map((x) => (x.id === ev.id ? { ...x, done: true } : x));
        break;
      case "delta":
        if (!t.firstTextAt) t.firstTextAt = Date.now();
        t.reply += ev.text || "";
        t.phase = "answering";
        break;
      case "reply_done":
        t.reply = ev.text || t.reply;
        t.tools = t.tools.map((x) => ({ ...x, done: true }));
        t.phase = "done";
        state.holdUntil = Date.now() + holdMs;
        break;
      case "local_answer":
        state.turn = { ...emptyTurn(), heard: ev.heard || "", reply: ev.text || "", phase: "done", by: "home_assistant", at: Date.now() };
        state.holdUntil = Date.now() + holdMs;
        break;
      case "error":
        t.phase = "error";
        t.error = ev.message || "";
        state.holdUntil = Date.now() + holdMs / 2;
        break;
      default:
        return;
    }
    renderTurn();
    updateMode();
  }

  function latestDevice(turns) {
    let best = null;
    let at = -1;
    for (const [dev, t] of Object.entries(turns)) if ((t.at || 0) > at) { at = t.at || 0; best = dev === "null" ? null : dev; }
    return best;
  }

  // ------------------------------------------------------------------------------- connection --
  let ws = null;
  let nextId = 1;
  const pending = new Map();
  let retry = 0;

  async function token() {
    const hash = new URLSearchParams(location.hash.slice(1));
    if (hash.get("token")) {
      store.set("token", hash.get("token"));
      if (params.get("device")) store.set("device", params.get("device"));
      history.replaceState(null, "", location.pathname + location.search);
    }
    const own = store.get("token");
    if (own) return own;
    // The login of Home Assistant's own frontend in this browser, refreshed when it has expired.
    let ha;
    try { ha = JSON.parse(localStorage.getItem("hassTokens") || "null"); } catch { ha = null; }
    if (!ha || !ha.access_token) return null;
    if (ha.expires && ha.expires > Date.now() + 30000) return ha.access_token;
    if (!ha.refresh_token) return ha.access_token;
    const form = new URLSearchParams({ grant_type: "refresh_token", refresh_token: ha.refresh_token, client_id: ha.clientId || location.origin + "/" });
    const resp = await fetch("/auth/token", { method: "POST", body: form });
    if (!resp.ok) return null;
    const fresh = await resp.json();
    return fresh.access_token;
  }

  function call(msg) {
    return new Promise((resolve, reject) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return reject(new Error("not connected"));
      const id = nextId++;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ ...msg, id }));
    });
  }

  async function connect() {
    let tok;
    try { tok = await token(); } catch { tok = null; }
    if (!tok) return showSetup();
    $("setup").hidden = true;
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/websocket");
    let subId = null;
    ws.onmessage = async (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "auth_required") return ws.send(JSON.stringify({ type: "auth", access_token: tok }));
      if (msg.type === "auth_invalid") {
        store.set("token", "");
        ws.close();
        return showSetup("Home Assistant did not accept the token.");
      }
      if (msg.type === "auth_ok") {
        connected = true;
        retry = 0;
        startHeartbeat(ws);
        try {
          const cfg = await call({ type: "get_config" });
          state.tz = cfg.time_zone;
          state.locale = [cfg.language, cfg.country].filter(Boolean).join("-") || undefined;
        } catch { /* keep the browser's */ }
        subId = nextId;
        call({ type: "hermes_voice/screen/subscribe", ...(state.device ? { device_id: state.device } : {}) }).catch(() =>
          showSetup("Home Assistant answered, but Hermes Voice is not set up there (or is too old)."),
        );
        refreshOverview(true);
        tick();
        return updateMode();
      }
      if (msg.type === "event" && msg.id === subId) return onEvent(msg.event);
      if (msg.type === "pong" && pending.has(msg.id)) {
        pending.get(msg.id).resolve(null);
        pending.delete(msg.id);
        return;
      }
      if (msg.type === "result" && pending.has(msg.id)) {
        const p = pending.get(msg.id);
        pending.delete(msg.id);
        msg.success ? p.resolve(msg.result) : p.reject(new Error((msg.error && msg.error.message) || "failed"));
      }
    };
    ws.onclose = () => {
      connected = false;
      clearInterval(heartbeat);
      for (const p of pending.values()) p.reject(new Error("closed"));
      pending.clear();
      updateMode();
      retry = Math.min(retry + 1, 6);
      setTimeout(connect, [1, 2, 3, 5, 8, 13, 20][retry] * 1000);
    };
  }

  // A connection can look open after Home Assistant is gone (a restart behind Docker Desktop's port
  // forwarding, Wi-Fi dropping on the Echo): ping every 30 s and start over when no pong comes in 10 s.
  let heartbeat = null;
  function startHeartbeat(sock) {
    clearInterval(heartbeat);
    heartbeat = setInterval(() => {
      const timer = setTimeout(() => {
        // A dead connection may not close for a long while: run the close handling now, once.
        const onclose = sock.onclose;
        sock.onclose = null;
        try { sock.close(); } catch { /* already gone */ }
        if (onclose) onclose();
      }, 10000);
      call({ type: "ping" }).then(() => clearTimeout(timer), () => clearTimeout(timer));
    }, 30000);
  }

  function showSetup(problem) {
    $("setup").hidden = false;
    $("setup-text").innerHTML =
      (problem ? problem + "<br><br>" : "") +
      "Open this page once with a Home Assistant long-lived token: <code>#token=&lt;token&gt;</code> " +
      "(Profile → Security → Long-lived access tokens), optionally with <code>?device=&lt;device id&gt;</code>. " +
      "Or log in to Home Assistant in this browser first.";
  }

  // --------------------------------------------------------------------------------- overview --
  let refreshing = false;
  async function refreshOverview(force) {
    if (!force && Date.now() - state.overviewAt < 30000) return;
    if (refreshing) return;
    refreshing = true;
    try {
      state.overview = DEMO ? demoOverview() : await call({ type: "hermes_voice/screen/overview" });
      state.overviewAt = Date.now();
      renderOverview();
    } catch { /* shown as stale */ } finally { refreshing = false; }
  }

  function renderOverview() {
    const ov = state.overview || {};
    const li = (title, sub) => {
      const el = document.createElement("li");
      const t = document.createElement("div");
      t.className = "t";
      t.textContent = title;
      el.append(t);
      if (sub) {
        const s = document.createElement("div");
        s.className = "s";
        s.textContent = sub;
        el.append(s);
      }
      return el;
    };
    const empty = (text) => {
      const el = document.createElement("li");
      el.className = "empty";
      el.textContent = text;
      return el;
    };
    $("ov-profile").textContent = ov.profile ? "· " + ov.profile : "";
    $("ov-updated").textContent = state.overviewAt ? "updated " + relTime(state.overviewAt / 1000) : "";
    const jobs = ov.jobs || [];
    // Work: what is running or needs you, then what is scheduled. Finished: what is done.
    const asItems = (htmls) => htmls.map((html) => {
      const el = document.createElement("li");
      el.className = "w";
      el.innerHTML = html;
      return el;
    });
    const work = workItems(["running", "attention"]).slice(0, 4).map((html) => {
      const el = document.createElement("li");
      el.className = "w";
      el.innerHTML = html;
      return el;
    });
    const scheduled = jobs.slice(0, Math.max(1, 6 - work.length)).map((j) =>
      li(j.name || "Job", [j.schedule_display, j.next_run_at ? "next " + relTime(j.next_run_at) : j.enabled === false ? "paused" : ""].filter(Boolean).join(" · ")),
    );
    const note = ov.errors && ov.errors.activity ? [empty("Background work: helper not reachable")] : [];
    $("ov-jobs").replaceChildren(
      ...(work.length || scheduled.length ? [...work, ...scheduled] : [empty(ov.errors && ov.errors.jobs ? "Could not load jobs" : "Nothing running or scheduled")]),
      ...note,
    );
    // Chat sessions are not shown: what was said to an assistant is not worth a screen (user, 2026-11-05).
    const finished = asItems(workItems(["recent"]).slice(0, 6));
    $("ov-sessions").replaceChildren(...(finished.length ? finished : [empty("Nothing finished in the last 3 hours")]));
    const h = ov.health || {};
    const m = h.metrics_today || {};
    const rows = [
      ["Gateway", h.status ? `<span class="${h.status === "ok" ? "ok" : "warn"}">${healthText(h)}</span>` : "\u2014"],
      ["Working", h.gateway_busy ? '<span class="warn">busy</span>' : h.active_agents ? h.active_agents + " running" : "idle"],
      ["Requests", m.requests != null ? m.requests : "—"],
      ["Messages", m.messages != null ? m.messages : "—"],
      ["Tokens", m.tokens != null ? Number(m.tokens).toLocaleString() : "—"],
      ["Slowest", m.latency_p95_ms != null ? (m.latency_p95_ms / 1000).toFixed(1) + " s" : "—"],
    ];
    $("ov-status").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  }

  /** "ok", or what Hermes' readiness checks flag: "low disk (98%)", "model degraded". */
  function healthText(h) {
    const w = (h.warnings || [])[0];
    if (!w || h.status === "ok") return escHtml(h.status);
    if (w.check === "disk") return "low disk" + (w.used_percent != null ? ` (${Math.round(w.used_percent)}%)` : "");
    return escHtml(w.check.replace(/_/g, " ") + " " + (w.status || ""));
  }

  let overviewTimer = null;
  function openOverview() {
    if (!["idle", "done", "offline"].includes(state.mode)) return;
    $("overview").hidden = false;
    renderOverview();
    refreshOverview(true);
    clearTimeout(overviewTimer);
    overviewTimer = setTimeout(closeOverview, 90000);
  }
  function closeOverview() {
    $("overview").hidden = true;
    clearTimeout(overviewTimer);
  }
  $("live").addEventListener("click", openOverview);
  $("ov-back").addEventListener("click", (e) => { e.stopPropagation(); closeOverview(); });

  // Screen brightness, the whole screen. In the Hermes Screen app it sets the backlight (remembered by the
  // app); in a browser it dims the page (remembered by the browser). Low enough for a bedside table.
  const slider = $("brightness");
  const bridge = window.HermesScreen && window.HermesScreen.setBrightness ? window.HermesScreen : null;
  const MIN = 0.02;
  function applyLevel(pct, save) {
    const level = Math.max(MIN, Math.min(1, pct / 100));
    if (bridge) {
      if (save) bridge.setBrightness(level);
    } else {
      document.documentElement.style.filter = level < 1 ? `brightness(${level})` : "";
      if (save) store.set("brightness", String(Math.round(level * 100)));
    }
  }
  let start = 100;
  try {
    start = bridge ? Math.round(bridge.getBrightness() * 100) : Number(store.get("brightness") || 100);
  } catch { /* full */ }
  slider.value = String(start);
  applyLevel(start, false);
  slider.addEventListener("input", () => {
    applyLevel(Number(slider.value), true);
    clearTimeout(overviewTimer);
    overviewTimer = setTimeout(closeOverview, 90000);
  });
  slider.addEventListener("click", (e) => e.stopPropagation());

  // ----------------------------------------------------------------------------------- avatar --
  // <config>/www/hermes_voice/avatar.(svg|png|webp|jpg|gif) replaces the default; avatar-<state>.* per state.
  const AVATAR_DIR = "/local/hermes_voice/";
  const EXT = ["svg", "png", "webp", "jpg", "gif"];
  const avatars = {};

  async function findAvatar(name) {
    for (const ext of EXT) {
      try {
        const r = await fetch(AVATAR_DIR + name + "." + ext, { method: "HEAD", cache: "no-store" });
        if (r.ok) return AVATAR_DIR + name + "." + ext;
      } catch { /* none */ }
    }
    return null;
  }

  async function loadAvatars() {
    if (params.get("avatar")) avatars.base = params.get("avatar");
    else if (!DEMO) avatars.base = await findAvatar("avatar");
    if (!DEMO) for (const s of ["listening", "thinking", "answering"]) avatars[s] = await findAvatar("avatar-" + s);
    applyAvatar();
  }

  // Idle: the avatar fills the screen behind the clock, zoomed onto her eyes. The default drawing's eyes sit at
  // about (0.33, 0.45) of the drawing; a custom picture is shown with "cover", so its point is of the circle.
  const ZOOM = Math.max(2, Number(params.get("zoom")) || 8);
  function eyes() {
    const given = (params.get("eyes") || "").split(",").map(Number);
    if (given.length === 2 && given.every((n) => n >= 0 && n <= 1)) return given;
    return avatars.base ? [0.5, 0.4] : [0.33, 0.45];
  }
  function updateZoom() {
    const wrap = $("avatar-wrap");
    const size = 300; // .avatar-wrap
    const side = 34;
    const left = innerWidth - side - size;
    const top = innerHeight / 2 - size / 2;
    // Where the eyes are inside the wrap: the avatar is 86% of it, centred; the default drawing is a mask
    // 92% wide, placed at 42% down, with the drawing's own aspect ratio (5487 × 5559).
    const av = size * 0.86;
    const inset = (size - av) / 2;
    const [fx, fy] = eyes();
    let px;
    let py;
    if (avatars.base) {
      px = inset + fx * av;
      py = inset + fy * av;
    } else {
      const w = av * 0.92;
      const h = w * (5559 / 5487);
      px = inset + (av - w) / 2 + fx * w;
      py = inset + (av - h) * 0.42 + fy * h;
    }
    // Put the eyes a little right of centre, where the clock leaves room.
    const tx = innerWidth * 0.66 - left - ZOOM * px;
    const ty = innerHeight * 0.44 - top - ZOOM * py;
    wrap.style.setProperty("--zoom", `translate(${tx.toFixed(1)}px, ${ty.toFixed(1)}px) scale(${ZOOM})`);
  }
  addEventListener("resize", updateZoom);

  function applyAvatar() {
    updateZoom();
    const el = $("avatar");
    const url = avatars[state.mode] || avatars.base;
    if (!url) {
      el.classList.remove("picture");
      el.style.removeProperty("--avatar-image");
      return;
    }
    el.classList.add("picture");
    el.style.setProperty("--avatar-image", `url("${url}")`);
  }
  new MutationObserver(applyAvatar).observe(body, { attributes: true, attributeFilter: ["data-mode"] });

  // ------------------------------------------------------------------------------------- demo --
  function demoOverview() {
    const now = Date.now();
    return {
      profile: "voice",
      jobs: [
        { name: "Morning brief", schedule_display: "weekdays 7:00", next_run_at: new Date(now + 13 * 3600e3).toISOString() },
        { name: "Check the build", schedule_display: "every 2 h", next_run_at: new Date(now + 47 * 60e3).toISOString() },
      ],
      activity: {
        scope: "all",
        running: [{ title: "Research Echo Show 8 support", assignee: "research", started_at: now / 1000 - 12 * 60 }],
        attention: [{ title: "Upstream issue draft for echo-dot-assist", assignee: "voice", status: "review" }],
        recent: [{ title: "Nightly research digest", assignee: "research", completed_at: now / 1000 - 20 * 60 }],
      },
      sessions: [
        { title: "Weather in Dallas", last_active: now / 1000 - 120, message_count: 4, source: "api_server" },
        { title: "Days until Christmas", last_active: now / 1000 - 3600, message_count: 2, source: "api_server" },
        { title: "Recap Echo rescue and next steps", last_active: now / 1000 - 86400, message_count: 18, source: "cli" },
      ],
      health: { status: "ok", gateway_busy: false, active_agents: 0, metrics_today: { requests: 23, messages: 41, tokens: 182340, latency_p95_ms: 4800 } },
      errors: {},
    };
  }

  function runDemo() {
    state.tz = undefined;
    state.overview = demoOverview();
    const dev = "demo";
    const reply = "Tomorrow in **Dallas**: a high near 71°F, more clouds than sun and little chance of rain. North winds 5–10 mph, low around 54. Source: [National Weather Service](https://forecast.weather.gov/).";
    const steps = [
      [2500, { type: "state", state: "listening" }],
      [2600, { type: "state", state: "processing" }],
      [300, { type: "heard", text: "What's the weather in Dallas tomorrow?" }],
      [900, { type: "tool_start", id: "c1", tool: "web_search", label: "Dallas weather tomorrow" }],
      [1800, { type: "tool_end", id: "c1" }],
      [200, { type: "tool_start", id: "c2", tool: "web_extract", label: "forecast.weather.gov" }],
      [1200, { type: "tool_end", id: "c2" }],
      [300, { type: "state", state: "responding" }],
      ...reply.split(/(?<= )/).map((w) => [70, { type: "delta", text: w }]),
      [300, { type: "reply_done", text: reply }],
      [3000, { type: "state", state: "idle" }],
      [9000, { type: "state", state: "listening" }],
      [2200, { type: "state", state: "processing" }],
      [400, { type: "local_answer", heard: "What time is it?", text: "" }],
      [2500, { type: "state", state: "idle" }],
    ];
    holdMs = 6000;
    let i = 0;
    const next = () => {
      if (i >= steps.length) { i = 0; setTimeout(next, 8000); return; }
      const [wait, ev] = steps[i++];
      setTimeout(() => {
        const now = timeParts(new Date());
        onEvent({ ...ev, ...(ev.type === "local_answer" ? { text: (now.time + " " + now.ampm).trim() } : {}), device_id: dev });
        next();
      }, wait);
    };
    state.focus = dev;
    state.satellite[dev] = "idle";
    setMode("idle");
    if (params.get("demo") !== "idle") next();
  }
  // ------------------------------------------------------------------------------------ start --
  setInterval(tick, 1000);
  tick();
  updateZoom();
  loadAvatars();
  if (DEMO) runDemo();
  else connect();
})();
