"use strict";

(() => {
  const el = (id) => document.getElementById(id);

  const session_path = () =>
    "/api/sessions/" + encodeURIComponent(window.Sage.state.currentSessionId);

  const quiz_question_path = (question_id) =>
    session_path() + "/quiz/" + encodeURIComponent(question_id);

  function post(path, body) {
    return window.Sage.api(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  const answer_result = (resp) => (resp && resp.result) || resp || {};

  function render(full) {
    const area = el("quiz-area");
    area.replaceChildren();
    const quiz = full.quiz || [];
    const phase = full.session.phase;
    const pending = quiz.find((q) => q.status === "pending");
    if (pending) {
      render_question(area, pending);
      area.hidden = false;
      return;
    }
    if (phase === "probe") {
      const note = document.createElement("p");
      note.textContent = "Probe complete — create your learning plan.";
      area.appendChild(note);
      const hint = document.createElement("p");
      hint.className = "hint";
      hint.textContent = "Generate the plan from the study controls below, then approve it.";
      area.appendChild(hint);
      area.hidden = false;
      return;
    }
    if (phase === "remediate") {
      const last_check = [...quiz]
        .reverse()
        .find(
          (q) =>
            q.kind === "check" &&
            q.status === "answered" &&
            (q.outcome === "incorrect" || q.outcome === "idk")
        );
      if (last_check) {
        render_remediate(area, last_check);
        area.hidden = false;
        return;
      }
    }
    area.hidden = true;
  }

  function render_question(area, question) {
    const card = document.createElement("div");
    card.className = "quiz-card";

    const header = document.createElement("div");
    header.className = "quiz-header";
    const kind_chip = document.createElement("span");
    kind_chip.className = "status-chip";
    kind_chip.textContent = question.kind === "probe" ? "Probe" : "Check";
    header.appendChild(kind_chip);
    card.appendChild(header);

    const text = document.createElement("p");
    text.className = "quiz-question";
    text.textContent = question.question;
    card.appendChild(text);

    const options = document.createElement("div");
    options.className = "quiz-options";
    let selected_index = null;
    let idk_selected = false;
    question.options.forEach((option, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "option-button";
      if (index === question.options.length - 1) button.classList.add("idk");
      button.textContent = option;
      button.addEventListener("click", () => {
        if (button.disabled) return;
        options.querySelectorAll(".option-button").forEach((b) => b.classList.remove("selected"));
        button.classList.add("selected");
        selected_index = index;
        idk_selected = index === question.options.length - 1;
      });
      options.appendChild(button);
    });
    card.appendChild(options);

    const submit_row = document.createElement("div");
    submit_row.className = "quiz-submit";
    const submit = document.createElement("button");
    submit.type = "button";
    submit.className = "primary";
    submit.textContent = "Submit";
    submit.addEventListener("click", () =>
      submit_answer(question, selected_index, idk_selected, card, options, submit_row)
    );
    submit_row.appendChild(submit);
    card.appendChild(submit_row);

    area.appendChild(card);
  }

  async function submit_answer(question, selected_index, idk_selected, card, options, submit_row) {
    if (window.Study.is_busy()) return;
    if (selected_index === null) {
      window.Sage.toast("Select an answer first.");
      return;
    }
    window.Study.set_busy(true);
    try {
      const body = idk_selected ? { idk: true } : { choice_index: selected_index };
      const resp = await post(quiz_question_path(question.id) + "/answer", body);
      const result = answer_result(resp);
      render_answer_feedback(card, question, result.outcome || "incorrect", result, options, submit_row, selected_index);
    } catch (err) {
      window.Sage.toast(`Answer failed: ${err.message}`);
    } finally {
      window.Study.set_busy(false);
    }
  }

  function render_answer_feedback(card, question, outcome, result, options, submit_row, user_index) {
    submit_row.hidden = true;
    options.querySelectorAll(".option-button").forEach((b) => {
      b.disabled = true;
      b.classList.remove("selected");
    });
    const buttons = options.querySelectorAll(".option-button");
    const correct_button = buttons[question.correct_index];
    if (correct_button) correct_button.classList.add("correct");
    if (outcome === "incorrect" && user_index !== null && user_index !== question.correct_index) {
      const wrong_button = buttons[user_index];
      if (wrong_button) wrong_button.classList.add("wrong");
    }

    const feedback = document.createElement("div");
    feedback.className = "quiz-feedback";
    const chip = document.createElement("span");
    chip.className = `status-chip ${outcome}`;
    chip.textContent = outcome;
    feedback.appendChild(chip);
    const explanation = result.explanation || question.explanation || "";
    if (explanation) {
      const p = document.createElement("p");
      p.className = "quiz-explanation";
      p.textContent = explanation;
      feedback.appendChild(p);
    }
    card.appendChild(feedback);

    if (question.kind === "probe") {
      const next_row = document.createElement("div");
      next_row.className = "feedback-actions";
      const next = document.createElement("button");
      next.type = "button";
      next.className = "primary";
      next.textContent = result.probe_complete ? "Continue" : "Next question";
      next.addEventListener("click", () => window.Study.refresh());
      next_row.appendChild(next);
      card.appendChild(next_row);
    } else if (outcome === "correct") {
      const next_row = document.createElement("div");
      next_row.className = "feedback-actions";
      const cont = document.createElement("button");
      cont.type = "button";
      cont.className = "primary";
      cont.textContent = "Continue";
      cont.addEventListener("click", () => window.Study.refresh());
      next_row.appendChild(cont);
      card.appendChild(next_row);
    } else {
      card.appendChild(feedback_actions(question, card, outcome));
    }
  }

  function render_remediate(area, question) {
    const card = document.createElement("div");
    card.className = "quiz-card";

    const header = document.createElement("div");
    header.className = "quiz-header";
    const kind_chip = document.createElement("span");
    kind_chip.className = "status-chip";
    kind_chip.textContent = "Check";
    header.appendChild(kind_chip);
    const outcome_chip = document.createElement("span");
    outcome_chip.className = `status-chip ${question.outcome}`;
    outcome_chip.textContent = question.outcome;
    header.appendChild(outcome_chip);
    card.appendChild(header);

    const text = document.createElement("p");
    text.className = "quiz-question";
    text.textContent = question.question;
    card.appendChild(text);

    const options = document.createElement("div");
    options.className = "quiz-options";
    const user_index =
      question.user_choice !== undefined && question.user_choice !== null
        ? question.user_choice
        : question.chosen_index;
    question.options.forEach((option, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "option-button";
      if (index === question.options.length - 1) button.classList.add("idk");
      button.disabled = true;
      if (index === question.correct_index) button.classList.add("correct");
      if (user_index === index && user_index !== question.correct_index) button.classList.add("wrong");
      button.textContent = option;
      options.appendChild(button);
    });
    card.appendChild(options);

    if (question.explanation) {
      const feedback = document.createElement("div");
      feedback.className = "quiz-feedback";
      const p = document.createElement("p");
      p.className = "quiz-explanation";
      p.textContent = question.explanation;
      feedback.appendChild(p);
      card.appendChild(feedback);
    }

    card.appendChild(feedback_actions(question, card, question.outcome));
    area.appendChild(card);
  }

  function feedback_actions(question, card, outcome) {
    const row = document.createElement("div");
    row.className = "feedback-actions";

    const hint = document.createElement("button");
    hint.type = "button";
    hint.textContent = "Hint";
    hint.addEventListener("click", () => request_hint(question, card));
    row.appendChild(hint);

    const reveal = document.createElement("button");
    reveal.type = "button";
    reveal.textContent = "Reveal answer";
    reveal.addEventListener("click", () => reveal_answer(question, card));
    row.appendChild(reveal);

    if (outcome === "incorrect" || outcome === "idk") {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Retry";
      retry.addEventListener("click", () => retry_question(question, card));
      row.appendChild(retry);
    }

    const skip = document.createElement("button");
    skip.type = "button";
    skip.textContent = "Skip";
    skip.addEventListener("click", () =>
      window.Study.run("Skip question", quiz_question_path(question.id) + "/skip")
    );
    row.appendChild(skip);

    const cont = document.createElement("button");
    cont.type = "button";
    cont.className = "primary";
    cont.textContent = "Continue";
    cont.addEventListener("click", () =>
      window.Study.run("Continue", session_path() + "/continue")
    );
    row.appendChild(cont);

    return row;
  }

  async function request_hint(question, card) {
    if (window.Study.is_busy()) return;
    window.Study.set_busy(true);
    try {
      const resp = await post(quiz_question_path(question.id) + "/hint");
      const result = answer_result(resp);
      const hint_text = result.hint || "";
      let hint = card.querySelector(".quiz-hint");
      if (!hint) {
        hint = document.createElement("p");
        hint.className = "quiz-hint";
        card.appendChild(hint);
      }
      hint.textContent = hint_text || "No hint available.";
    } catch (err) {
      window.Sage.toast(`Hint failed: ${err.message}`);
    } finally {
      window.Study.set_busy(false);
    }
  }

  async function reveal_answer(question, card) {
    if (window.Study.is_busy()) return;
    window.Study.set_busy(true);
    try {
      const resp = await post(quiz_question_path(question.id) + "/reveal");
      const result = answer_result(resp);
      const buttons = card.querySelectorAll(".option-button");
      buttons.forEach((b) => {
        b.disabled = true;
      });
      const correct_button = buttons[question.correct_index];
      if (correct_button) correct_button.classList.add("correct");
      const explanation = question.explanation || result.explanation || "";
      let expl = card.querySelector(".quiz-explanation");
      if (!expl) {
        expl = document.createElement("p");
        expl.className = "quiz-explanation";
        card.appendChild(expl);
      }
      expl.textContent = explanation || "The correct answer is highlighted above.";
    } catch (err) {
      window.Sage.toast(`Reveal failed: ${err.message}`);
    } finally {
      window.Study.set_busy(false);
    }
  }

  function retry_question(question, card) {
    const area = el("quiz-area");
    area.replaceChildren();
    render_question(area, question);
  }

  window.StudyQuiz = { render };
})();
