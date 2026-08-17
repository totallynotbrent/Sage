"use strict";

(() => {
  const el = (id) => document.getElementById(id);

  let study_busy = false;

  const session_path = () =>
    "/api/sessions/" + encodeURIComponent(window.Sage.state.currentSessionId);

  const check_due = (session) =>
    session.phase === "teach" && (session.nodes_since_check || 0) >= 2;

  function set_busy(busy) {
    study_busy = busy;
    const send = el("send-btn");
    if (send) send.disabled = busy || window.Sage.state.streaming;
    document
      .querySelectorAll("#plan-panel button, #quiz-area button, #study-controls button")
      .forEach((button) => {
        button.disabled = busy;
      });
  }

  async function refresh() {
    if (window.Sage.state.currentSessionId) {
      await window.Sage.openSession(window.Sage.state.currentSessionId);
    }
  }

  async function run(label, path, body) {
    if (window.Sage.state.streaming) {
      window.Sage.toast("Finish or stop the current reply first.");
      return;
    }
    set_busy(true);
    try {
      await window.Sage.api(path, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      await refresh();
    } catch (err) {
      window.Sage.toast(`${label}: ${err.message}`);
    } finally {
      set_busy(false);
    }
  }

  function control_button(label, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.study_action = action;
    button.textContent = label;
    button.className = "primary";
    return button;
  }

  function render_study_controls(full) {
    const section = el("study-controls");
    section.replaceChildren();
    const session = full.session;
    const phase = session.phase;
    const nodes = full.plan || [];

    const title = document.createElement("h2");
    title.className = "panel-title";
    title.textContent = "Study controls";
    section.appendChild(title);

    const row = document.createElement("div");
    row.className = "study-controls";
    section.appendChild(row);

    if (phase === "setup") {
      row.appendChild(control_button("Start probe", "probe"));
    }
    if (!nodes.length) {
      row.appendChild(control_button("Generate learning plan", "plan"));
    }
    if (phase === "plan" && nodes.length) {
      row.appendChild(control_button("Approve plan", "plan/approve"));
    }
    if (phase === "teach") {
      row.appendChild(control_button("Next step", "advance"));
      if (check_due(session)) {
        row.appendChild(control_button("Check understanding", "check"));
      }
    }
    if (phase === "complete") {
      const note = document.createElement("p");
      note.className = "hint";
      note.textContent = "Session complete. Start a new session or delete this one from Preferences.";
      row.appendChild(note);
    }
    section.hidden = row.childElementCount === 0;
  }

  async function render_study_area(full) {
    const badge = el("phase-badge");
    badge.textContent = full.session.phase;
    badge.className = `badge phase-${full.session.phase}`;
    await window.StudyPlan.render(full);
    window.StudyQuiz.render(full);
    render_study_controls(full);
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-study-action]");
      if (!button) return;
      run(button.textContent.trim(), session_path() + "/" + button.dataset.study_action);
    });
  });

  window.Study = {
    render_study_area,
    run,
    refresh,
    set_busy,
    is_busy: () => study_busy,
  };
})();
