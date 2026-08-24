/* Leaked tool-call guard + smooth status animations for Sage workspace.
 *
 * Some models emit tool invocations as inline text (<call:name .../>) in
 * the delta stream instead of through the proper tool_calls channel.
 * Left as-is, the user sees raw markup and no quiz cards. These helpers
 * strip leaked calls out of the visible text and expose a small event
 * hook so the composer can show what is actually happening.
 */

/* Matches <call:name attr="value" ... /> possibly spanning buffered
 * fragments. Kept permissive: any tag starting with <call: that closes. */
const LEAKED_CALL_RE = /<call:\s*[a-zA-Z_][\w-]*\b[^>]*\/?>(?:\s*<\/call:\s*[a-zA-Z_][\w-]*>)?/g;

/* Partial trailing "<call:..." fragment while the stream is mid-tag. */
const LEAKED_CALL_OPEN_RE = /<call:[^>]*$/;

function strip_leaked_calls(text, { streaming = false } = {}) {
  let cleaned = text.replace(LEAKED_CALL_RE, "");
  if (streaming) cleaned = cleaned.replace(LEAKED_CALL_OPEN_RE, "");
  return cleaned;
}

/* Extract a human-readable status from a leaked run_probe-style call:
 * <call:run_probe status="Assessing your knowledge..."/> -> "Assessing..." */
function leaked_call_status(text) {
  const m = text.match(/<call:\s*[a-zA-Z_][\w-]*\s+status="([^"]{0,120})"/);
  return m ? m[1] : null;
}
