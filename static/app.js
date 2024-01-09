"use strict";

/* Sage frontend: vanilla JS, no frameworks. All dynamic content is rendered
   via textContent/createElement — never innerHTML — so uploaded document text,
   filenames, and model output stay inert in the DOM. */

const state = {
  files: [],
  sessions: [],
  currentSession: null,      // full load_full payload for the active session
  currentSessionId: null,
  streaming: false,
  activeBubble: null,        // assistant bubble element currently being filled
  activeMsgId: null,
  activeClientMsgId: null,
  activeAbort: null,
  activeCitations: [],       // citation chunk ids collected during a stream
};

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  let body = null;
  if (contentType.includes("application/json")) {
    body = await response.json();
  }
  if (!response.ok) {
    const code = body && body.error && body.error.code;
    const message = (body && body.error && body.error.message) || `HTTP ${response.status}`;
    const detail = body && body.error && body.error.detail;
    const err = new Error(detail && detail !== "None" ? `${message} — ${detail}` : message);
    err.status = response.status;
    err.code = code;
    throw err;
  }
  return body;
}

function toast(message) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

function clientMsgId() {
  return (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());
}

function escapeHtmlForTitle(text) {
  // Used only for title attributes (no innerHTML anywhere).
  return String(text).replace(/[\u0000-\u001f]/g, " ");
}

/* ------------------------------------------------------------------ */
/* Health                                                              */
/* ------------------------------------------------------------------ */

async function pollHealth() {
  try {
    const report = await api("/api/health");
    const badge = $("health-badge");
    badge.textContent = report.status;
    badge.className = `badge ${report.status}`;
    let title = `Model configured: ${report.model_configured}\n`;
    title += `Endpoint reachable: ${report.endpoint_reachable}\n`;
    if (report.dependency_errors && report.dependency_errors.length) {
      title += `Dependency errors: ${report.dependency_errors.join(", ")}`;
    }
    badge.title = title;
  } catch (err) {
    const badge = $("health-badge");
    badge.textContent = "error";
    badge.className = "badge error";
    badge.title = err.message;
  }
}

/* ------------------------------------------------------------------ */
/* File library                                                        */
/* ------------------------------------------------------------------ */

async function loadFiles() {
  state.files = await api("/api/files");
  renderLibrary();
  renderFileSelect();
}

function statusChip(status) {
  const chip = document.createElement("span");
  chip.className = `status-chip ${status}`;
  chip.textContent = status;
  return chip;
}

function renderLibrary() {
  const list = $("file-list");
  list.replaceChildren();
  if (!state.files.length) {
    const li = document.createElement("li");
    li.textContent = "No files yet. Add PDF, DOCX, PPTX, Markdown, or text files.";
    li.className = "hint";
    list.appendChild(li);
    return;
  }
  for (const file of state.files) {
    const li = document.createElement("li");
    const row = document.createElement("div");
    row.className = "file-row";

    const name = document.createElement("div");
    name.className = "file-name";
    name.textContent = file.display_name;
    row.appendChild(name);

    const meta = document.createElement("div");
    meta.className = "file-meta";
    const size = document.createElement("span");
    size.textContent = formatBytes(file.size_bytes);
    meta.appendChild(size);
    meta.appendChild(statusChip(file.status));
    const chunks = document.createElement("span");
    chunks.textContent = `${file.num_chunks} chunks`;
    meta.appendChild(chunks);
    if (file.warnings && file.warnings.length) {
      const warn = document.createElement("span");
      warn.textContent = `⚠ ${file.warnings.length} warning(s)`;
      warn.className = "muted";
      warn.title = escapeHtmlForTitle(file.warnings.join("\n"));
      meta.appendChild(warn);
    }
    if (file.error) {
      const err = document.createElement("span");
      err.textContent = "error";
      err.className = "muted";
      err.title = escapeHtmlForTitle(file.error);
      meta.appendChild(err);
    }
    row.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "file-actions";
    if (file.status === "failed") {
      const retry = document.createElement("button");
      retry.textContent = "Retry";
      retry.addEventListener("click", () => retryFile(file.id));
      actions.appendChild(retry);
    }
    const remove = document.createElement("button");
    remove.textContent = "Remove";
    remove.addEventListener("click", () => deleteFile(file.id));
    actions.appendChild(remove);
    row.appendChild(actions);

    li.appendChild(row);
    list.appendChild(li);
  }
}

function renderFileSelect() {
  const list = $("file-select-list");
  list.replaceChildren();
  const ready = state.files.filter((f) => f.status === "ready");
  if (!ready.length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "Upload and successfully extract a file first.";
    list.appendChild(p);
    return;
  }
  for (const file of ready) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = file.id;
    input.dataset.fileId = file.id;
    const span = document.createElement("span");
    span.textContent = file.display_name;
    label.appendChild(input);
    label.appendChild(span);
    list.appendChild(label);
  }
}

function selectedFileIds() {
  return Array.from($("file-select-list").querySelectorAll("input:checked")).map((i) => i.dataset.fileId);
}

async function uploadFiles(fileList) {
  const status = $("upload-status");
  status.hidden = false;
  for (const file of Array.from(fileList)) {
    status.textContent = `Uploading ${file.name}…`;
    const form = new FormData();
    form.append("files", file);
    try {
      await api("/api/files", { method: "POST", body: form });
    } catch (err) {
      toast(`${file.name}: ${err.message}`);
    }
  }
  status.hidden = true;
  await loadFiles();
}

async function deleteFile(id) {
  try {
    await api(`/api/files/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadFiles();
  } catch (err) {
    toast(`Remove failed: ${err.message}`);
  }
}

async function retryFile(id) {
  try {
    await api(`/api/files/${encodeURIComponent(id)}/retry`, { method: "POST" });
    await loadFiles();
  } catch (err) {
    toast(`Retry failed: ${err.message}`);
  }
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* ------------------------------------------------------------------ */
/* Sessions                                                            */
/* ------------------------------------------------------------------ */

async function loadSessions() {
  state.sessions = await api("/api/sessions");
  renderSessionList();
}

function renderSessionList() {
  const list = $("session-list");
  list.replaceChildren();
  if (!state.sessions.length) {
    const li = document.createElement("li");
    li.textContent = "No sessions yet.";
    li.className = "hint";
    list.appendChild(li);
    return;
  }
  for (const session of state.sessions) {
    const li = document.createElement("li");
    li.className = "session-row";
    if (session.id === state.currentSessionId) li.classList.add("active");
    li.textContent = session.goal.length > 60 ? session.goal.slice(0, 60) + "…" : session.goal;
    li.addEventListener("click", () => openSession(session.id));
    list.appendChild(li);
  }
}

async function createSession() {
  const goal = $("goal-input").value.trim();
  if (!goal) {
    toast("Enter a learning goal first.");
    return;
  }
  const button = $("create-session-btn");
  button.disabled = true;
  try {
    const session = await api("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        goal,
        file_ids: selectedFileIds(),
        grounding_mode: $("grounding-select").value,
      }),
    });
    await loadSessions();
    await openSession(session.id);
  } catch (err) {
    toast(`Could not start session: ${err.message}`);
  } finally {
    button.disabled = false;
  }
}

async function openSession(id) {
  if (state.streaming) {
    toast("Finish or stop the current reply first.");
    return;
  }
  const full = await api(`/api/sessions/${encodeURIComponent(id)}`);
  state.currentSession = full;
  state.currentSessionId = id;
  renderSessionView();
  renderSessionList();
}

function renderSessionView() {
  const full = state.currentSession;
  const session = full.session;
  $("new-session-panel").hidden = true;
  $("session-panel").hidden = false;

  $("session-title").textContent = session.title || session.goal.slice(0, 60);
  $("session-goal").textContent = session.goal;

  const phase = $("phase-badge");
  phase.textContent = session.phase;
  phase.className = `badge ${session.phase === "setup" ? "degraded" : "ok"}`;

  const toggle = $("grounding-toggle");
  toggle.checked = session.grounding_mode !== "strict";
  toggle.disabled = state.streaming;

  const note = $("source-note");
  const unavailable = (full.selected_files || []).filter((f) => f.status !== "ready");
  if (unavailable.length) {
    note.hidden = false;
    note.textContent =
      `Note: ${unavailable.map((f) => f.display_name).join(", ")} not ready for context.`;
  } else {
    note.hidden = true;
  }

  renderTranscript(full.messages || []);
  if (window.Study) window.Study.render_study_area(full);
}

function renderTranscript(messages) {
  const transcript = $("transcript");
  transcript.replaceChildren();
  if (!messages.length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "Ask your first question to begin.";
    transcript.appendChild(p);
    return;
  }
  for (const message of messages) {
    transcript.appendChild(renderMessageBubble(message));
  }
  transcript.scrollTop = transcript.scrollHeight;
}

function renderMessageBubble(message) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${message.role === "user" ? "user" : "assistant"}`;
  wrap.dataset.msgId = message.id;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  appendContentWithCitations(bubble, message.content || "", message.citations || []);
  wrap.appendChild(bubble);

  const meta = document.createElement("div");
  meta.className = "msg-meta";
  if (message.citations && message.citations.length) {
    const n = document.createElement("span");
    n.textContent = `${message.citations.length} citation(s)`;
    meta.appendChild(n);
  }
  if (message.content && message.content.length > 40) {
    const when = document.createElement("span");
    when.textContent = new Date(message.created_at).toLocaleTimeString();
    meta.appendChild(when);
  }
  wrap.appendChild(meta);
  return wrap;
}

/* Renders text with [cit:chunkId] markers converted to numbered, clickable
   chips. Everything goes through text nodes — document/model text can never
   inject HTML. */
function appendContentWithCitations(container, text, citations) {
  const markers = citations && citations.length
    ? citations.map((c, i) => ({ marker: `[cit:${c}]`, chunkId: c, number: i + 1 }))
    : [];
  let remaining = text;
  while (markers.length) {
    const entry = markers.shift();
    const index = remaining.indexOf(entry.marker);
    if (index === -1) continue;
    container.appendChild(document.createTextNode(remaining.slice(0, index)));
    const chip = document.createElement("span");
    chip.className = "citation-chip";
    chip.textContent = `[${entry.number}]`;
    chip.dataset.chunkId = entry.chunkId;
    chip.title = "Show source excerpt";
    chip.addEventListener("click", () => openExcerpt(entry.chunkId));
    container.appendChild(chip);
    remaining = remaining.slice(index + entry.marker.length);
  }
  container.appendChild(document.createTextNode(remaining));
}

/* ------------------------------------------------------------------ */
/* Streaming turns                                                     */
/* ------------------------------------------------------------------ */

async function sendTurn() {
  if (state.streaming) return;
  const input = $("message-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";

  state.streaming = true;
  state.activeClientMsgId = clientMsgId();
  state.activeCitations = [];
  state.activeAbort = new AbortController();

  $("send-btn").disabled = true;
  $("stop-btn").hidden = false;
  $("grounding-toggle").disabled = true;

  // Optimistically render the user message.
  const transcript = $("transcript");
  const userWrap = document.createElement("div");
  userWrap.className = "msg user";
  const userBubble = document.createElement("div");
  userBubble.className = "bubble";
  userBubble.textContent = text;
  userWrap.appendChild(userBubble);
  transcript.appendChild(userWrap);

  // Reserve the assistant bubble.
  const assistantWrap = document.createElement("div");
  assistantWrap.className = "msg assistant";
  assistantWrap.dataset.clientMsgId = state.activeClientMsgId;
  const assistantBubble = document.createElement("div");
  assistantBubble.className = "bubble";
  const ellipsis = document.createElement("span");
  ellipsis.textContent = "…";
  assistantBubble.appendChild(ellipsis);
  assistantWrap.appendChild(assistantBubble);
  transcript.appendChild(assistantWrap);
  state.activeBubble = assistantBubble;
  transcript.scrollTop = transcript.scrollHeight;

  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(state.currentSessionId)}/turns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, client_msg_id: state.activeClientMsgId }),
      signal: state.activeAbort.signal,
    });

    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        message = (body.error && body.error.message) || message;
      } catch (_) { /* not JSON */ }
      throw new Error(message);
    }

    await consumeSse(response);
  } catch (err) {
    if (err.name === "AbortError") return; // stop button: error event follows via /stop
    finishStreamWithError(err.message || "Connection error.");
  }
}

async function consumeSse(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data: ")) {
          handleSseEvent(JSON.parse(line.slice(6)));
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function handleSseEvent(event) {
  switch (event.type) {
    case "meta":
      if (event.insufficient) {
        addContextChip("No source excerpts available");
      } else if (event.chunks && event.chunks.length) {
        addContextChip(`Using ${event.chunks.length} excerpt(s)`);
      }
      break;
    case "delta":
      appendDelta(event.delta || "");
      break;
    case "citation":
      state.activeCitations.push(event.chunk_id);
      break;
    case "done":
      finishStream(event.message_id, event.replayed);
      break;
    case "error":
      finishStreamWithError(event.message || "Generation failed.", event.code, event.retryable);
      break;
    default:
      break;
  }
}

function addContextChip(label) {
  if (!state.activeBubble) return;
  const chip = document.createElement("span");
  chip.className = "context-chip";
  chip.textContent = label;
  state.activeBubble.appendChild(chip);
}

function appendDelta(delta) {
  const bubble = state.activeBubble;
  if (!bubble) return;
  // Drop the placeholder ellipsis once real content arrives.
  if (bubble.textContent === "…") bubble.replaceChildren();
  bubble.appendChild(document.createTextNode(delta));
  const transcript = $("transcript");
  transcript.scrollTop = transcript.scrollHeight;
}

function finishStream(messageId, replayed) {
  if (state.activeBubble) {
    // Rebuild with citation chips.
    const text = state.activeBubble.textContent;
    state.activeBubble.replaceChildren();
    appendContentWithCitations(state.activeBubble, text, state.activeCitations);
  }
  if (replayed && state.activeBubble) {
    state.activeBubble.append(" (already answered)");
  }
  endStreamingState();
}

function finishStreamWithError(message, code, retryable) {
  if (state.activeBubble) {
    state.activeBubble.classList.add("error");
    state.activeBubble.textContent = message;
    const retry = document.createElement("button");
    retry.textContent = "Retry";
    retry.addEventListener("click", () => retryTurn(state.activeClientMsgId));
    const parent = state.activeBubble.parentElement;
    if (parent) parent.appendChild(retry);
  }
  endStreamingState();
}

function endStreamingState() {
  state.streaming = false;
  state.activeBubble = null;
  state.activeAbort = null;
  $("send-btn").disabled = false;
  $("stop-btn").hidden = true;
  $("grounding-toggle").disabled = false;
  const transcript = $("transcript");
  transcript.scrollTop = transcript.scrollHeight;
}

async function stopGeneration() {
  if (!state.streaming) return;
  if (state.activeAbort) state.activeAbort.abort();
  try {
    await api(`/api/sessions/${encodeURIComponent(state.currentSessionId)}/stop`, { method: "POST" });
  } catch (err) {
    toast(`Stop request failed: ${err.message}`);
  }
}

async function retryTurn(clientMsgId) {
  if (state.streaming) return;
  state.streaming = true;
  state.activeClientMsgId = clientMsgId;
  state.activeCitations = [];
  state.activeAbort = new AbortController();
  $("send-btn").disabled = true;
  $("stop-btn").hidden = false;

  const transcript = $("transcript");
  const assistantWrap = document.createElement("div");
  assistantWrap.className = "msg assistant";
  const assistantBubble = document.createElement("div");
  assistantBubble.className = "bubble";
  assistantBubble.textContent = "…";
  assistantWrap.appendChild(assistantBubble);
  transcript.appendChild(assistantWrap);
  state.activeBubble = assistantBubble;

  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(state.currentSessionId)}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_msg_id: clientMsgId }),
      signal: state.activeAbort.signal,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error((body && body.error && body.error.message) || `HTTP ${response.status}`);
    }
    await consumeSse(response);
  } catch (err) {
    if (err.name !== "AbortError") {
      finishStreamWithError(err.message || "Retry failed.");
    }
  }
}

/* ------------------------------------------------------------------ */
/* Excerpt modal                                                       */
/* ------------------------------------------------------------------ */

async function openExcerpt(chunkId) {
  try {
    const fileId = chunkId.split(":")[0];
    const data = await api(
      `/api/files/${encodeURIComponent(fileId)}/excerpts?chunk=${encodeURIComponent(chunkId)}`
    );
    $("excerpt-title").textContent = data.file_name;
    $("excerpt-location").textContent = data.location;
    $("excerpt-text").textContent = data.text;
    const context = $("excerpt-context");
    context.replaceChildren();
    if (data.context_around.before) {
      const before = document.createElement("div");
      before.className = "context-chip";
      before.textContent = "before";
      context.appendChild(before);
      context.appendChild(document.createTextNode(data.context_around.before.slice(0, 400)));
    }
    if (data.context_around.after) {
      const after = document.createElement("div");
      after.className = "context-chip";
      after.textContent = "after";
      context.appendChild(after);
      context.appendChild(document.createTextNode(data.context_around.after.slice(0, 400)));
    }
    $("excerpt-modal").hidden = false;
  } catch (err) {
    toast(`Could not open excerpt: ${err.message}`);
  }
}

/* ------------------------------------------------------------------ */
/* Event wiring                                                        */
/* ------------------------------------------------------------------ */

function wireEvents() {
  $("file-input").addEventListener("change", (e) => {
    uploadFiles(e.target.files);
    e.target.value = "";
  });
  $("create-session-btn").addEventListener("click", createSession);
  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    sendTurn();
  });
  $("stop-btn").addEventListener("click", stopGeneration);
  $("grounding-toggle").addEventListener("change", async (e) => {
    if (!state.currentSessionId) return;
    const mode = e.target.checked ? "grounded" : "strict";
    try {
      const updated = await api(`/api/sessions/${encodeURIComponent(state.currentSessionId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ grounding_mode: mode }),
      });
      state.currentSession.session.grounding_mode = updated.grounding_mode;
      const phase = $("phase-badge");
      phase.textContent = updated.phase;
    } catch (err) {
      toast(`Could not change grounding mode: ${err.message}`);
      e.target.checked = mode === "grounded";
    }
  });
  $("excerpt-close").addEventListener("click", () => {
    $("excerpt-modal").hidden = true;
  });
  $("excerpt-modal").addEventListener("click", (e) => {
    if (e.target === $("excerpt-modal")) $("excerpt-modal").hidden = true;
  });
}

async function init() {
  wireEvents();
  pollHealth();
  setInterval(pollHealth, 30000);
  try {
    await Promise.all([loadFiles(), loadSessions()]);
  } catch (err) {
    toast(`Initial load failed: ${err.message}`);
  }
}

document.addEventListener("DOMContentLoaded", init);

/* Exported surface for the study-loop modules (plan.js, quiz.js, data.js,
   study.js). Kept minimal: shared fetch/toast/state plus the session refresh
   and list-reload entry points those modules need. */
window.Sage = { state, api, toast, openSession, loadSessions };
