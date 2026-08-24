/* Binary check-question buttons for Sage workspace.
 *
 * The tutor ends teaching turns with a two-option check question
 * (yes/no, higher/lower, increase/decrease, true/false, A/B).
 * Detect the trailing choice marker in the assistant text and replace it
 * with tappable buttons; the tap sends the answer as a normal chat message
 * so the existing grading pipeline handles it unchanged.
 */

/* Each pattern: [regex for the trailing marker, display labels].
 * First match wins. All case-insensitive. */
const BINARY_PATTERNS = [
  { re: /\(?(yes\s*[/\\|]\s*no|yes\s+or\s+no)\)?\s*[?.!]*$/i, labels: ["Yes", "No"] },
  { re: /\((higher|lower)\s*[/\\|]\s*(higher|lower)\)?\s*[?.!]*$/i,
    labels: ["Higher", "Lower"] },
  { re: /\((higher|lower)\s+or\s+(higher|lower)\)?\s*[?.!]*$/i,
    labels: ["Higher", "Lower"] },
  { re: /\((increasing|decreasing)\s*[/\\|]\s*(increasing|decreasing)\)?\s*[?.!]*$/i,
    labels: ["Increasing", "Decreasing"] },
  { re: /\((increasing|decreasing)\s+or\s+(increasing|decreasing)\)?\s*[?.!]*$/i,
    labels: ["Increasing", "Decreasing"] },
  { re: /\((true|false)\s*[/\\|]\s*(true|false)\)?\s*[?.!]*$/i,
    labels: ["True", "False"] },
  { re: /\((true|false)\s+or\s+(true|false)\)?\s*[?.!]*$/i,
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
