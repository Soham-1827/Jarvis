// Jarvis PWA client. Vanilla JS; every message is rendered with textContent, never as HTML.
"use strict";

const STORE = {
  token: "jarvis.token",
  conversation: "jarvis.conversation",
  history: "jarvis.history",
};
const HISTORY_LIMIT = 60;
const GAUGE_CIRCUMFERENCE = 2 * Math.PI * 15;

const $ = (id) => document.getElementById(id);
const els = {
  status: $("status"),
  pair: $("pair"),
  pairForm: $("pair-form"),
  token: $("token"),
  chat: $("chat"),
  log: $("log"),
  thinking: $("thinking"),
  composer: $("composer"),
  input: $("input"),
  send: $("send"),
  newConvo: $("new-convo"),
  gauge5h: $("gauge-5h"),
  gauge7d: $("gauge-7d"),
};

const store = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try { value == null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch { /* private mode */ }
  },
  history() {
    try { return JSON.parse(store.get(STORE.history) || "[]"); } catch { return []; }
  },
};

let socket = null;
let ready = false;
let retryMs = 1000;
let retryTimer = null;

// ---------- rendering ----------

function setStatus(text, state) {
  els.status.textContent = text;
  els.status.dataset.state = state;
}

function timeLabel(iso) {
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function renderMessage({ who, text, at }) {
  const li = document.createElement("li");
  li.className = `msg msg-${who}`;
  const body = document.createElement("span");
  body.textContent = text;
  li.append(body);
  if (who !== "note") {
    const time = document.createElement("time");
    time.className = "msg-time";
    time.dateTime = at;
    time.textContent = who === "jarvis" ? `Jarvis · ${timeLabel(at)}` : timeLabel(at);
    li.append(time);
  }
  els.log.append(li);
  li.scrollIntoView({ block: "end", behavior: "smooth" });
}

function addMessage(who, text, { persist = true } = {}) {
  const entry = { who, text, at: new Date().toISOString() };
  renderMessage(entry);
  if (persist) {
    const history = [...store.history(), entry].slice(-HISTORY_LIMIT);
    store.set(STORE.history, JSON.stringify(history));
  }
}

function addNote(text, isError = false) {
  const li = document.createElement("li");
  li.className = `msg msg-note${isError ? " error" : ""}`;
  li.textContent = text;
  els.log.append(li);
  li.scrollIntoView({ block: "end" });
}

function setGauge(el, value) {
  if (typeof value !== "number") return;
  const clamped = Math.max(0, Math.min(1, value));
  el.querySelector(".fill").style.strokeDashoffset = String(GAUGE_CIRCUMFERENCE * (1 - clamped));
  el.dataset.level = clamped >= 0.85 ? "high" : "normal";
  el.title = `${el.id === "gauge-5h" ? "Five-hour" : "Seven-day"} window: ${Math.round(clamped * 100)}% used`;
}

function setThinking(on) {
  els.thinking.hidden = !on;
  if (on) els.thinking.scrollIntoView({ block: "end", behavior: "smooth" });
}

function updateSendState() {
  els.send.disabled = !ready || els.input.value.trim().length === 0;
}

// ---------- screens ----------

function showPairing() {
  els.pair.hidden = false;
  els.chat.hidden = true;
  els.composer.hidden = true;
  setStatus("Awaiting introduction", "offline");
  els.token.focus();
}

function showChat() {
  els.pair.hidden = true;
  els.chat.hidden = false;
  els.composer.hidden = false;
  if (!els.log.childElementCount) {
    const history = store.history();
    if (history.length) history.forEach(renderMessage);
    else addNote("At your service");
  }
}

// ---------- connection ----------

function connect() {
  const token = store.get(STORE.token);
  if (!token) { showPairing(); return; }
  showChat();
  clearTimeout(retryTimer);
  setStatus("Summoning…", "offline");
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws`);

  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ type: "auth", token }));
  });

  socket.addEventListener("message", (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    handleServerMessage(msg);
  });

  socket.addEventListener("close", (event) => {
    ready = false;
    updateSendState();
    setThinking(false);
    if (event.code === 1008 && !store.get(STORE.token)) return; // unpaired: stay on pairing
    setStatus("Reconnecting…", "error");
    retryTimer = setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 30000);
  });
}

function handleServerMessage(msg) {
  switch (msg.type) {
    case "ready":
      ready = true;
      retryMs = 1000;
      setStatus(`In attendance · ${msg.device}`, "online");
      updateSendState();
      break;
    case "thinking":
      setThinking(true);
      break;
    case "reply":
      setThinking(false);
      store.set(STORE.conversation, msg.conversation_id);
      addMessage("jarvis", msg.text);
      setGauge(els.gauge5h, msg.five_hour_utilization);
      setGauge(els.gauge7d, msg.seven_day_utilization);
      break;
    case "error":
      setThinking(false);
      if (msg.message === "invalid token" || msg.message === "device revoked") {
        store.set(STORE.token, null);
        addNote("This device is no longer recognised", true);
        showPairing();
      } else if (msg.message === "unknown conversation") {
        store.set(STORE.conversation, null);
        addNote("That conversation has ended; starting afresh", true);
      } else {
        addNote(msg.message, true);
      }
      break;
    default:
      break;
  }
}

// ---------- actions ----------

function sendMessage() {
  const text = els.input.value.trim();
  if (!text || !ready) return;
  const conversationId = store.get(STORE.conversation);
  socket.send(JSON.stringify({ type: "chat", text, conversation_id: conversationId || null }));
  addMessage("you", text);
  els.input.value = "";
  autoGrow();
  updateSendState();
}

function autoGrow() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 152)}px`;
}

els.pairForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const token = els.token.value.trim();
  if (token.length < 20) return;
  store.set(STORE.token, token);
  els.token.value = "";
  connect();
});

els.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});

els.input.addEventListener("input", () => { autoGrow(); updateSendState(); });
els.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendMessage();
  }
});

els.newConvo.addEventListener("click", () => {
  store.set(STORE.conversation, null);
  store.set(STORE.history, null);
  els.log.replaceChildren();
  addNote("A fresh page, sir");
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && (!socket || socket.readyState > 1)) connect();
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* offline shell is optional */ });
}

updateSendState();
connect();
