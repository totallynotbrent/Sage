// Client-side guard: strips leaked text-form tool calls (e.g. <call:run_probe/>,
// [call:name], bare "call:name/") from rendered assistant messages. The server
// also strips these; this is the last line of defense for anything that slips
// through before sanitize_html/renderMd see the text.
(function () {
  'use strict';

  const LEAKED_CALL_RE = /<call:\\w+\\b[^>]*>?|\\[?call:\\w+\\b\\/?\\]?(?:\\s*\\([^)]*\\))?/g;
  const LEAKED_CALL_OPEN_RE = /<call:[^<]*$|\\[?call:\\w+$/;

  function strip_leaked_calls(text, opts) {
    opts = opts || {};
    let out = text.replace(LEAKED_CALL_RE, '');
    if (!opts.streaming) {
      // Final render: also drop any dangling open tag remnant.
      out = out.replace(LEAKED_CALL_OPEN_RE, '');
    }
    return out;
  }

  function leaked_call_status(text) {
    if (/<call:/.test(text)) return 'Preparing your assessment\u2026';
    return '';
  }

  window.strip_leaked_calls = strip_leaked_calls;
  window.LEAKED_CALL_RE = LEAKED_CALL_RE;
  window.LEAKED_CALL_OPEN_RE = LEAKED_CALL_OPEN_RE;
  window.leaked_call_status = leaked_call_status;
})();
