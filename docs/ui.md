# UI & Interaction

How the Sage web UI behaves: streaming, animations, and the two-answer
check-question buttons.

## Streaming chat

Assistant turns stream token-by-token over SSE (`meta → delta* → citation* →
done`). The UI renders deltas into a streaming bubble whose child blocks fade
in (`streamLineIn`, 320 ms). A status pill above the composer shows live tool
activity ("Assessing your current knowledge of …") pulled from `tool_call`
event `status` fields.

### Leaked tool-call guard

Some models occasionally emit a tool invocation as inline text
(`<call:name status="..."/>`) instead of through the proper `tool_calls`
mechanism. `static/js/leaked-call-guard.js` intercepts streamed deltas:

- complete `<call:...>` tags are stripped before rendering;
- a trailing partial tag is held back until the stream closes;
- the call's `status` attribute becomes the composer status line, so the user
  still sees what Sage is doing.

The system prompt also instructs the model to invoke tools only through the
tool-calls mechanism, which makes the leak rare; the guard is the safety net.

## Animations

Motion follows the opendesign spec (see bread repo `opendesign/`): message
cascade on load, artifact reveal with staggered option delays, quiz-question
typewriter effect (`artType` steps(30) + blinking cursor) followed by option
reveals at 1100/1300/1500 ms, correct-answer color sweep, and diagram line
drawing.

`prefers-reduced-motion: reduce` does **not** disable animations — it caps
them at 200 ms animations / 120 ms transitions so the UI stays lively for
users who have Windows "Show animations" turned off (this was a real bug:
the original spec's kill-switch made everything look static on such
machines).

## Two-answer check questions

Teaching turns end with exactly one scaffolded check question that has exactly
two possible answers. The system prompt requires the question to end with a
literal parenthesized marker:

| Marker | Buttons rendered |
| ------ | ---------------- |
| `(yes/no)` | Yes / No |
| `(higher/lower)` | Higher / Lower |
| `(increasing/decreasing)` | Increasing / Decreasing |
| `(true/false)` | True / False |

`static/js/binary-check.js` detects the trailing marker in the finished
assistant text, strips it from the displayed bubble, and renders two buttons.
Tapping one sends the label ("yes", "higher", …) as a normal chat turn, so the
existing grading pipeline handles it unchanged — no special endpoint. The
buttons animate in (`binaryRowIn`), lock after answering, and dim the
untaken option.

This keeps interaction button-driven instead of requiring typed free-text
answers for binary checks.

## Artifacts placement

- **Workspace** (`sage-workspace.html`): probe quizzes and generated artifacts
  render in the right-hand artifacts rail (`diagramBody`), which auto-opens
  when a new artifact arrives. The chat column stays prose-only.
- **Home** (`index.html`): pure chat + session creation. The manual
  Diagram/Quiz/Todo buttons were removed — the agent generates artifacts
  itself via tools.
