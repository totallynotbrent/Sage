"use strict";

(() => {
  const el = (id) => document.getElementById(id);

  function close_modal() {
    el("preferences-modal").hidden = true;
  }

  async function open_modal() {
    el("preferences-modal").hidden = false;
    try {
      const prefs = await window.Sage.api("/api/preferences");
      el("pref-depth").value = prefs.depth || "standard";
      el("pref-pacing").value = prefs.pacing || "normal";
      el("pref-style").value = prefs.style || "examples-first";
      el("pref-notes").value = prefs.notes || "";
    } catch (err) {
      window.Sage.toast(`Could not load preferences: ${err.message}`);
    }
    render_mastery();
  }

  function render_mastery() {
    const list = el("mastery-list");
    list.replaceChildren();
    const full = window.Sage.state.currentSession;
    const mastery = (full && full.mastery) || [];
    if (!mastery.length) {
      const li = document.createElement("li");
      li.className = "hint";
      li.textContent = "No mastery data yet.";
      list.appendChild(li);
      return;
    }
    for (const topic of mastery) {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = topic.label || topic.topic || "General";
      const confidence = document.createElement("span");
      confidence.className = "muted";
      const pct = Math.round((topic.confidence || 0) * 1000) / 10;
      confidence.textContent = `${pct}%`;
      li.append(label, confidence);
      list.appendChild(li);
    }
  }

  async function save_preferences() {
    const body = {
      depth: el("pref-depth").value,
      pacing: el("pref-pacing").value,
      style: el("pref-style").value,
      notes: el("pref-notes").value,
    };
    try {
      await window.Sage.api("/api/preferences", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      window.Sage.toast("Preferences saved.");
    } catch (err) {
      window.Sage.toast(`Could not save preferences: ${err.message}`);
    }
  }

  async function clear_preferences() {
    if (!window.confirm("Clear all learner preferences?")) return;
    try {
      await window.Sage.api("/api/preferences", { method: "DELETE" });
      el("pref-depth").value = "standard";
      el("pref-pacing").value = "normal";
      el("pref-style").value = "examples-first";
      el("pref-notes").value = "";
      window.Sage.toast("Preferences cleared.");
    } catch (err) {
      window.Sage.toast(`Could not clear preferences: ${err.message}`);
    }
  }

  async function reset_mastery() {
    if (!window.confirm("Reset all mastery data? This cannot be undone.")) return;
    try {
      await window.Sage.api("/api/mastery", { method: "DELETE" });
      if (window.Sage.state.currentSessionId) {
        await window.Sage.openSession(window.Sage.state.currentSessionId);
      }
      render_mastery();
      window.Sage.toast("Mastery data reset.");
    } catch (err) {
      window.Sage.toast(`Could not reset mastery: ${err.message}`);
    }
  }

  async function delete_session() {
    if (window.Sage.state.streaming) {
      window.Sage.toast("Finish or stop the current reply first.");
      return;
    }
    const id = window.Sage.state.currentSessionId;
    if (!id) return;
    if (!window.confirm("Delete this session? This cannot be undone.")) return;
    try {
      await window.Sage.api(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
      window.Sage.state.currentSession = null;
      window.Sage.state.currentSessionId = null;
      el("new-session-panel").hidden = false;
      el("session-panel").hidden = true;
      close_modal();
      await window.Sage.loadSessions();
    } catch (err) {
      window.Sage.toast(`Could not delete session: ${err.message}`);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    el("preferences-btn").addEventListener("click", open_modal);
    el("preferences-close").addEventListener("click", close_modal);
    el("preferences-modal").addEventListener("click", (event) => {
      if (event.target === el("preferences-modal")) close_modal();
    });
    el("preferences-form").addEventListener("submit", (event) => {
      event.preventDefault();
      save_preferences();
    });
    el("preferences-clear").addEventListener("click", clear_preferences);
    el("reset-mastery-btn").addEventListener("click", reset_mastery);
    el("delete-session-btn").addEventListener("click", delete_session);
  });

  window.StudyData = { open_modal, close_modal };
})();
