/* Binary check-question buttons for Sage workspace.
 *
 * The tutor ends teaching turns with a yes/no (or A/B) check question.
 * Detect the trailing question in the assistant text and replace it with
 * two tappable buttons; the tap sends the answer as a normal chat message
 * so the existing pipeline handles grading.
 */

/* Matches a trailing "(yes/no)", "yes or no?", "(correct/incorrect)" style
 * binary choice, case-insensitive. Returns match info or null. */
const BINARY_TAIL_RE = /\(?((?:yes|no)\s*[/\\]\s*(?:yes|no)|(?:yes|no)\s+or\s+(?:yes|no))\)?\s*[?.!]*\s*$/i;

function detect_binary_question(text) {
  const trimmed = (text || "").trim();
  const m = trimmed.match(BINARY_TAIL_RE);
  if (!m) return null;
  return { tail: m[0], question: trimmed.slice(0, trimmed.length - m[0].length).trim() };
}

function append_binary_buttons(messageEl, onAnswer) {
  if (!messageEl) return;
  const row = document.createElement("div");
  row.className = "binary-answer-row";
  for (const label of ["Yes", "No"]) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "binary-answer-btn";
    btn.textContent = label;
    btn.addEventListener("click", () => {
      if (row.dataset.answered === "1") return;
      row.dataset.answered = "1";
      btn.classList.add("selected");
      row.querySelectorAll(".binary-answer-btn").forEach(b => {
        if (b !== btn) b.disabled = true;
      });
      onAnswer(label.toLowerCase());
    });
    row.appendChild(btn);
  }
  messageEl.appendChild(row);
}
