/* Sage shared JS helpers — auto-extracted. Loaded before page scripts. */

// HTML escape (pages may define their own; this is the canonical one)
if (!window.esc) {
  window.esc = function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };
}

// Animations are force-enabled app-wide
window.SAGE_ANIMATIONS_ENABLED = true;

// Sidebar toggle used by every page's hamburger button.
// Pages with their own toggleSidebar keep theirs (this is a fallback).
if (!window.toggleSidebar) {
  window.toggleSidebar = function toggleSidebar() {
    const sb = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    const btn = document.getElementById('menu-btn');
    if (!sb) return;
    const mobile = window.matchMedia('(max-width:1023px)').matches;
    let open;
    if (mobile) {
      backdrop && backdrop.classList.toggle('open');
      open = !sb.classList.contains('open');
      sb.classList.toggle('open', open);
    } else {
      open = sb.classList.toggle('collapsed') === false;
    }
    btn && btn.setAttribute('aria-expanded', String(open));
  };
}

// Load the shared animation module on every page (opt-in via data-sage-animate on <body>)
(function () {
  const sc = document.createElement('script');
  sc.src = 'sage-animate.js';
  document.head.appendChild(sc);
})();
