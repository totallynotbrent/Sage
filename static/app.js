// sage client app

(function () {
  'use strict';

  // state
  const state = {
    sessionId: null,
    sessions: [],
    files: [],
    planNodes: [],
    watchDirs: [],
    messages: [],
    streaming: false,
    streamController: null,
    sidebarOpen: true,
    viewerOpen: false,
    viewerFileId: null,
    viewerPage: 1,
    viewerTotal: 1,
    pendingFiles: [],
  };

  // dom refs
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const dom = {
    menuBtn: $('#menu-btn'),
    sidebar: $('#sidebar'),
    sessionTitle: $('#session-title'),
    phaseBadge: $('#phase-badge'),
    newSessionBtn: $('#new-session-btn'),
    sessionsList: $('#sessions-list'),
    filesList: $('#files-list'),
    planNodes: $('#plan-nodes'),
    watchList: $('#watch-list'),
    uploadBtn: $('#upload-btn'),
    addWatchBtn: $('#add-watch-btn'),
    messages: $('#messages'),
    emptyState: $('#empty-state'),
    messageInput: $('#message-input'),
    sendBtn: $('#send-btn'),
    stopBtn: $('#stop-btn'),
    attachBtn: $('#attach-btn'),
    viewer: $('#viewer'),
    viewerTitle: $('#viewer-title'),
    viewerContent: $('#viewer-content'),
    viewerPage: $('#viewer-page'),
    viewerPrev: $('#viewer-prev'),
    viewerNext: $('#viewer-next'),
    viewerClose: $('#viewer-close'),
    uploadModal: $('#upload-modal'),
    uploadBackdrop: $('#upload-backdrop'),
    uploadClose: $('#upload-close'),
    uploadZone: $('#upload-zone'),
    fileInput: $('#file-input'),
    uploadList: $('#upload-list'),
    uploadCancel: $('#upload-cancel'),
    uploadSubmit: $('#upload-submit'),
  };

  // api
  const api = {
    base: '/api',

    async get(path) {
      const res = await fetch(this.base + path);
      if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
      return res.json();
    },

    async post(path, body) {
      const res = await fetch(this.base + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `POST ${path} failed: ${res.status}`);
      }
      return res.json();
    },

    async del(path) {
      const res = await fetch(this.base + path, { method: 'DELETE' });
      if (!res.ok) throw new Error(`DELETE ${path} failed: ${res.status}`);
      return res.json().catch(() => ({}));
    },

    async upload(file) {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch(this.base + '/files', { method: 'POST', body: form });
      if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
      return res.json();
    },

    streamTurn(sessionId, message, onMeta, onDelta, onCitation, onDone, onError) {
      const ctrl = new AbortController();
      state.streamController = ctrl;

      fetch(this.base + `/sessions/${sessionId}/turns`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, client_msg_id: crypto.randomUUID() }),
        signal: ctrl.signal,
      }).then(async (res) => {
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          onError(err.detail || `Stream failed: ${res.status}`);
          return;
        }
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop();
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const ev = JSON.parse(line.slice(6));
                if (ev.event === 'meta') onMeta(ev);
                else if (ev.event === 'delta') onDelta(ev.delta);
                else if (ev.event === 'citation') onCitation(ev);
                else if (ev.event === 'done') onDone(ev);
                else if (ev.event === 'error') onError(ev);
              } catch (_) {}
            }
          }
        }
        onDone({});
      }).catch((err) => {
        if (err.name !== 'AbortError') onError(err.message);
      });
    },

    cancelTurn() {
      if (state.streamController) {
        state.streamController.abort();
        state.streamController = null;
      }
    },
  };

  // markdown, math, and mermaid
  function renderMarkdown(text) {
    if (!text) return '';
    let html = marked.parse(text, { breaks: true, gfm: true });
    return html;
  }

  function processMermaid(html) {
    const regex = /<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g;
    return html.replace(regex, (_, code) => {
      return `<div class="mermaid-wrap"><div class="mermaid">${code}</div></div>`;
    });
  }

  function renderMath(el) {
    if (window.MathJax && MathJax.typesetPromise) {
      MathJax.typesetPromise([el]).catch(() => {});
    }
  }

  function renderMermaid(el) {
    const blocks = el.querySelectorAll('.mermaid');
    if (blocks.length && window.mermaid) {
      blocks.forEach((block) => {
        if (block.dataset.rendered) return;
        try {
          mermaid.run({ nodes: [block] });
          block.dataset.rendered = '1';
        } catch (_) {}
      });
    }
  }

  function renderContent(el, text) {
    let html = renderMarkdown(text);
    html = processMermaid(html);
    el.innerHTML = html;
    renderMath(el);
    renderMermaid(el);
  }

  // mermaid init
  function initMermaid() {
    if (window.mermaid) {
      mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        themeVariables: {
          primaryColor: '#26233a',
          primaryTextColor: '#e0def4',
          primaryBorderColor: '#c4a7e7',
          lineColor: '#908caa',
          secondaryColor: '#1f1d2e',
          tertiaryColor: '#191724',
          fontFamily: "'Newsreader', Georgia, serif",
        },
      });
    }
  }

  // render sessions
  function renderSessions() {
    const el = dom.sessionsList;
    if (!state.sessions.length) {
      el.innerHTML = '<div style="padding:8px 12px;font-size:13px;color:var(--faint)">No sessions yet</div>';
      return;
    }
    el.innerHTML = state.sessions.map((s) => {
      const active = s.id === state.sessionId ? ' active' : '';
      const phase = s.phase || 'setup';
      const time = s.updated_at ? timeAgo(s.updated_at) : '';
      return `<div class="sidebar__item${active}" data-session-id="${s.id}">
        <span class="sidebar__item-badge" data-phase="${phase}">${phase}</span>
        <span class="sidebar__item-text">${esc(s.goal || 'Untitled')}</span>
        <span class="sidebar__item-meta">${time}</span>
      </div>`;
    }).join('');
  }

  // render files
  function renderFiles() {
    const el = dom.filesList;
    if (!state.files.length) {
      el.innerHTML = '<div style="padding:8px 12px;font-size:13px;color:var(--faint)">No files uploaded</div>';
      return;
    }
    el.innerHTML = state.files.map((f) => {
      const status = f.status || 'pending';
      return `<div class="sidebar__item" data-file-id="${f.id}">
        <span class="sidebar__item-dot" data-status="${status}"></span>
        <span class="sidebar__item-text">${esc(f.display_name || f.storage_name)}</span>
        <span class="sidebar__item-meta">${f.num_chunks || 0} chunks</span>
      </div>`;
    }).join('');
  }

  // render plan
  function renderPlan() {
    const el = dom.planNodes;
    if (!state.planNodes.length) {
      el.innerHTML = '<div style="padding:8px 12px;font-size:13px;color:var(--faint)">No plan generated</div>';
      return;
    }
    el.innerHTML = state.planNodes.map((n) => {
      const status = n.status || 'pending';
      const icon = status === 'done' ? '&#10003;' : status === 'current' ? '&#9654;' : status === 'skipped' ? '&#8594;' : '&#9675;';
      return `<div class="sidebar__item" data-node-id="${n.id}">
        <span style="font-size:12px;width:16px;text-align:center">${icon}</span>
        <span class="sidebar__item-text">${esc(n.title)}</span>
      </div>`;
    }).join('');
  }

  // render watch
  function renderWatch() {
    const el = dom.watchList;
    if (!state.watchDirs.length) {
      el.innerHTML = '<div style="padding:8px 12px;font-size:13px;color:var(--faint)">No folders watched</div>';
      return;
    }
    el.innerHTML = state.watchDirs.map((w) => {
      return `<div class="sidebar__item" data-watch-id="${w.id}">
        <span class="sidebar__item-dot" data-status="ready"></span>
        <span class="sidebar__item-text">${esc(w.path)}</span>
        <span class="sidebar__item-meta">${w.file_count || 0}</span>
      </div>`;
    }).join('');
  }

  // render messages
  function renderMessages() {
    const el = dom.messages;
    el.innerHTML = '';
    if (!state.messages.length) {
      el.appendChild(dom.emptyState);
      dom.emptyState.style.display = '';
      return;
    }
    dom.emptyState.style.display = 'none';
    state.messages.forEach((msg) => {
      el.appendChild(createMessageEl(msg));
    });
    scrollBottom();
  }

  function createMessageEl(msg) {
    const div = document.createElement('div');
    div.className = `msg msg--${msg.role}`;
    div.dataset.msgId = msg.id;

    const roleLabel = msg.role === 'user' ? 'You' : 'Sage';
    let content = msg.content || '';

    div.innerHTML = `
      <div class="msg__role">${roleLabel}</div>
      <div class="msg__bubble"></div>
    `;

    const bubble = div.querySelector('.msg__bubble');
    renderContent(bubble, content);

    if (msg.citations && msg.citations.length) {
      const cites = document.createElement('div');
      cites.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px;margin-top:8px';
      msg.citations.forEach((c) => {
        const chip = document.createElement('span');
        chip.className = 'cite';
        chip.textContent = c.label || c.chunk_id || 'src';
        chip.dataset.chunkId = c.chunk_id;
        chip.dataset.fileId = c.file_id;
        cites.appendChild(chip);
      });
      bubble.appendChild(cites);
    }

    return div;
  }

  function appendStreamingMessage() {
    dom.emptyState.style.display = 'none';
    const div = document.createElement('div');
    div.className = 'msg msg--assistant';
    div.dataset.msgId = 'streaming';
    div.innerHTML = `
      <div class="msg__role">Sage</div>
      <div class="msg__bubble streaming"></div>
    `;
    dom.messages.appendChild(div);
    scrollBottom();
    return div.querySelector('.msg__bubble');
  }

  function scrollBottom() {
    requestAnimationFrame(() => {
      dom.messages.scrollTop = dom.messages.scrollHeight;
    });
  }

  // viewer
  function openViewer(fileId, fileName) {
    state.viewerOpen = true;
    state.viewerFileId = fileId;
    state.viewerPage = 1;
    dom.viewer.style.display = '';
    dom.viewerTitle.textContent = fileName || 'Document';
    loadViewerContent();
  }

  function closeViewer() {
    state.viewerOpen = false;
    state.viewerFileId = null;
    dom.viewer.style.display = 'none';
  }

  async function loadViewerContent() {
    if (!state.viewerFileId) return;
    try {
      const data = await api.get(`/files/${state.viewerFileId}/excerpts`);
      const chunks = data.excerpts || data.chunks || [];
      state.viewerTotal = Math.max(1, chunks.length);
      dom.viewerPage.textContent = `${state.viewerPage} / ${state.viewerTotal}`;
      dom.viewerContent.innerHTML = chunks.map((c, i) => {
        return `<div class="chunk" data-chunk-index="${i}">${esc(c.text || c.content || '')}</div>`;
      }).join('');
    } catch (err) {
      dom.viewerContent.innerHTML = `<div style="color:var(--love)">Failed to load document: ${esc(err.message)}</div>`;
    }
  }

  // upload modal
  function openUploadModal() {
    state.pendingFiles = [];
    dom.uploadModal.style.display = '';
    dom.uploadList.innerHTML = '';
    dom.uploadSubmit.disabled = true;
    dom.fileInput.value = '';
  }

  function closeUploadModal() {
    dom.uploadModal.style.display = 'none';
    state.pendingFiles = [];
  }

  function addPendingFiles(files) {
    for (const f of files) {
      if (!state.pendingFiles.find((p) => p.name === f.name && p.size === f.size)) {
        state.pendingFiles.push(f);
      }
    }
    renderPendingFiles();
  }

  function renderPendingFiles() {
    dom.uploadList.innerHTML = state.pendingFiles.map((f, i) => {
      return `<div class="upload-item">
        <span class="upload-item__name">${esc(f.name)}</span>
        <span class="upload-item__size">${formatSize(f.size)}</span>
        <button class="upload-item__remove" data-idx="${i}" aria-label="Remove">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M4 4l6 6M10 4L4 10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
        </button>
      </div>`;
    }).join('');
    dom.uploadSubmit.disabled = state.pendingFiles.length === 0;
  }

  async function submitUpload() {
    if (!state.pendingFiles.length) return;
    dom.uploadSubmit.disabled = true;
    dom.uploadSubmit.textContent = 'Uploading...';

    for (const f of state.pendingFiles) {
      try {
        await api.upload(f);
      } catch (err) {
        console.error('Upload failed:', f.name, err);
      }
    }

    closeUploadModal();
    await loadFiles();
  }

  // data loaders
  async function loadSessions() {
    try {
      const data = await api.get('/sessions');
      state.sessions = data.sessions || data || [];
      renderSessions();
    } catch (err) {
      console.error('Failed to load sessions:', err);
    }
  }

  async function loadFiles() {
    try {
      const data = await api.get('/files');
      state.files = data.files || data || [];
      renderFiles();
    } catch (err) {
      console.error('Failed to load files:', err);
    }
  }

  async function loadSession(id) {
    try {
      const data = await api.get(`/sessions/${id}`);
      state.sessionId = id;
      state.messages = data.messages || [];
      state.planNodes = data.plan_nodes || [];
      dom.sessionTitle.textContent = data.goal || 'Untitled Session';
      updatePhase(data.phase || 'setup');
      renderMessages();
      renderPlan();
      renderSessions();
    } catch (err) {
      console.error('Failed to load session:', err);
    }
  }

  async function loadWatchDirs() {
    try {
      const data = await api.get('/watch');
      state.watchDirs = data.sources || data || [];
      renderWatch();
    } catch (err) {
      state.watchDirs = [];
      renderWatch();
    }
  }

  // actions
  async function createSession() {
    try {
      const data = await api.post('/sessions', { goal: '' });
      await loadSessions();
      await loadSession(data.id);
    } catch (err) {
      console.error('Failed to create session:', err);
    }
  }

  function sendMessage() {
    const text = dom.messageInput.value.trim();
    if (!text || state.streaming || !state.sessionId) return;

    dom.messageInput.value = '';
    autoResize(dom.messageInput);

    const userMsg = { id: crypto.randomUUID(), role: 'user', content: text };
    state.messages.push(userMsg);
    dom.messages.appendChild(createMessageEl(userMsg));
    scrollBottom();

    const bubble = appendStreamingMessage();
    let fullText = '';

    state.streaming = true;
    dom.sendBtn.style.display = 'none';
    dom.stopBtn.style.display = '';

    api.streamTurn(
      state.sessionId,
      text,
      (meta) => {},
      (delta) => {
        fullText += delta;
        renderContent(bubble, fullText);
        scrollBottom();
      },
      (cit) => {},
      (done) => {
        state.streaming = false;
        state.streamController = null;
        dom.sendBtn.style.display = '';
        dom.stopBtn.style.display = 'none';
        bubble.classList.remove('streaming');

        const assistantMsg = {
          id: done.message_id || crypto.randomUUID(),
          role: 'assistant',
          content: fullText,
          citations: done.citations || [],
        };
        state.messages.push(assistantMsg);

        const streamingEl = dom.messages.querySelector('[data-msg-id="streaming"]');
        if (streamingEl) streamingEl.remove();
        dom.messages.appendChild(createMessageEl(assistantMsg));
        scrollBottom();
      },
      (err) => {
        state.streaming = false;
        state.streamController = null;
        dom.sendBtn.style.display = '';
        dom.stopBtn.style.display = 'none';
        bubble.classList.remove('streaming');
        bubble.innerHTML = `<span style="color:var(--love)">Error: ${esc(typeof err === 'string' ? err : err.message || 'Generation failed')}</span>`;
      }
    );
  }

  function stopGeneration() {
    api.cancelTurn();
  }

  function updatePhase(phase) {
    dom.phaseBadge.textContent = phase;
    dom.phaseBadge.dataset.phase = phase;
  }

  // event handlers
  function initEvents() {
    dom.menuBtn.addEventListener('click', () => {
      state.sidebarOpen = !state.sidebarOpen;
      dom.sidebar.classList.toggle('collapsed', !state.sidebarOpen);
    });

    dom.newSessionBtn.addEventListener('click', createSession);

    dom.sessionsList.addEventListener('click', (e) => {
      const item = e.target.closest('.sidebar__item');
      if (item) loadSession(item.dataset.sessionId);
    });

    dom.filesList.addEventListener('click', (e) => {
      const item = e.target.closest('.sidebar__item');
      if (item) {
        const fid = item.dataset.fileId;
        const file = state.files.find((f) => f.id === fid);
        openViewer(fid, file ? file.display_name : 'Document');
      }
    });

    dom.sendBtn.addEventListener('click', sendMessage);
    dom.stopBtn.addEventListener('click', stopGeneration);

    dom.messageInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    dom.messageInput.addEventListener('input', () => autoResize(dom.messageInput));

    dom.attachBtn.addEventListener('click', openUploadModal);
    dom.uploadBtn.addEventListener('click', openUploadModal);

    dom.uploadBackdrop.addEventListener('click', closeUploadModal);
    dom.uploadClose.addEventListener('click', closeUploadModal);
    dom.uploadCancel.addEventListener('click', closeUploadModal);

    dom.uploadZone.addEventListener('click', () => dom.fileInput.click());
    dom.fileInput.addEventListener('change', (e) => addPendingFiles(e.target.files));

    dom.uploadZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dom.uploadZone.classList.add('dragover');
    });
    dom.uploadZone.addEventListener('dragleave', () => dom.uploadZone.classList.remove('dragover'));
    dom.uploadZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dom.uploadZone.classList.remove('dragover');
      addPendingFiles(e.dataTransfer.files);
    });

    dom.uploadSubmit.addEventListener('click', submitUpload);

    dom.uploadList.addEventListener('click', (e) => {
      const btn = e.target.closest('.upload-item__remove');
      if (btn) {
        state.pendingFiles.splice(parseInt(btn.dataset.idx), 1);
        renderPendingFiles();
      }
    });

    dom.viewerClose.addEventListener('click', closeViewer);
    const scrollToViewerPage = () => {
      const target = dom.viewerContent.querySelector(`[data-chunk-index="${state.viewerPage - 1}"]`);
      if (target) target.scrollIntoView({ block: 'start', behavior: 'smooth' });
    };
    dom.viewerPrev.addEventListener('click', () => {
      if (state.viewerPage > 1) {
        state.viewerPage--;
        dom.viewerPage.textContent = `${state.viewerPage} / ${state.viewerTotal}`;
        scrollToViewerPage();
      }
    });
    dom.viewerNext.addEventListener('click', () => {
      if (state.viewerPage < state.viewerTotal) {
        state.viewerPage++;
        dom.viewerPage.textContent = `${state.viewerPage} / ${state.viewerTotal}`;
        scrollToViewerPage();
      }
    });

    $$('.sidebar__header').forEach((btn) => {
      btn.addEventListener('click', () => {
        btn.closest('.sidebar__section').classList.toggle('open');
      });
    });

    dom.messages.addEventListener('click', (e) => {
      const cite = e.target.closest('.cite');
      if (cite) {
        const fid = cite.dataset.fileId;
        const file = state.files.find((f) => f.id === fid);
        openViewer(fid, file ? file.display_name : 'Source');
      }
    });

    dom.addWatchBtn.addEventListener('click', async () => {
      const path = prompt('Enter folder path to watch:');
      if (!path) return;
      try {
        await api.post('/watch', { path });
        await loadWatchDirs();
      } catch (err) {
        console.error('Failed to add watch dir:', err);
      }
    });
  }

  // helpers
  function esc(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function timeAgo(ts) {
    const diff = Date.now() - new Date(ts).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + 'h ago';
    const days = Math.floor(hrs / 24);
    return days + 'd ago';
  }

  function autoResize(ta) {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
  }

  // init
  async function init() {
    initMermaid();
    initEvents();

    await Promise.all([loadSessions(), loadFiles(), loadWatchDirs()]);

    if (state.sessions.length) {
      await loadSession(state.sessions[0].id);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
