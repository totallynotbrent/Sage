/* Binary check-question buttons for Sage workspace.
 *
 * The tutor ends teaching turns with a two-option check question
 * (yes/no, higher/lower, increase/decrease, true/false, A/B).
 * Detect the trailing choice marker in the assistant text and replace it
 * with tappable buttons; the tap sends the answer as a normal chat message
 * so the existing grading pipeline handles it unchanged.
 */

/* Each pattern: [regex for the trailing marker, display labels].
 * First match wins. All case-insensitive. Trailing markdown emphasis,
 * whitespace and punctuation after the marker is tolerated. */
const BINARY_AFTER = /(?:\s|\*|_|~)*[?.!]*(?:\s|\*)*$/;
const BINARY_PATTERNS = [
  { re: new RegExp("\\(?(yes\\s*[/\\\\|]\\s*no|yes\\s+or\\s+no)\\)?" + BINARY_AFTER.source, "i"), labels: ["Yes", "No"] },
  { re: new RegExp("\\((higher|lower)\\s*[/\\\\|]\\s*(higher|lower)\\)?" + BINARY_AFTER.source, "i"),
    labels: ["Higher", "Lower"] },
  { re: new RegExp("\\((higher|lower)\\s+or\\s+(higher|lower)\\)?" + BINARY_AFTER.source, "i"),
    labels: ["Higher", "Lower"] },
  { re: new RegExp("\\((increasing|decreasing)\\s*[/\\\\|]\\s*(increasing|decreasing)\\)?" + BINARY_AFTER.source, "i"),
    labels: ["Increasing", "Decreasing"] },
  { re: new RegExp("\\((increasing|decreasing)\\s+or\\s+(increasing|decreasing)\\)?" + BINARY_AFTER.source, "i"),
    labels: ["Increasing", "Decreasing"] },
  { re: new RegExp("\\((true|false)\\s*[/\\\\|]\\s*(true|false)\\)?" + BINARY_AFTER.source, "i"),
    labels: ["True", "False"] },
  { re: new RegExp("\\((true|false)\\s+or\\s+(true|false)\\)?" + BINARY_AFTER.source, "i"),
    labels: ["True", "False"] },
];

function detect_binary_question(text) {
  const trimmed = (text || "").trim();
  for (const p of BINARY_PATTERNS) {
    const m = trimmed.match(p.re);
    if (m) {
      return { tail: m[0], question: trimmed.slice(0, trimmed.length - m[0].length).trim(), labels: p.labels };
    }
  }
  return null;
}

function append_binary_buttons(messageEl, onAnswer) {
  if (!messageEl) return;
  const row = document.createElement("div");
  row.className = "binary-answer-row";
  const labels = messageEl.__binary_labels || ["Yes", "No"];
  for (const label of labels) {
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
