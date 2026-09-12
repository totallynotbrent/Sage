/* Sage shared animation module. loaded by every page via sage.js or directly.
 *
 * Auto mode: add data-sage-animate to <body> and headings/list items/cards get
 * entrance animations with zero per-page code:
 *   - h1/h2/h3 text: word cascade (wordIn)
 *   - .sidebar__item, .artifact-card, .results > *, tr, li: staggered toast-in fade/slide
 * Manual API: SageAnimate.wrapWords(el), SageAnimate.toastIn(el), SageAnimate.stagger(rows)
 * Animations are always enabled (reduced-motion intentionally ignored per product decision).
 */
(function () {
  'use strict';
  if (window.SageAnimate) return;

  const WORD_STAGGER_MS = 32;
  const TOAST_STAGGER_MS = 45;

  function wrapWords(container) {
    if (!container || container.dataset && container.dataset.wordWrapped === '1') return;
    if (container.dataset) container.dataset.wordWrapped = '1';
    let word_idx = 0;
    function wrap_text_node(node) {
      const text = node.textContent;
      if (!text || !text.trim()) return;
      if (/[$\\(\\[]/.test(text)) return; // skip math
      const frag = document.createDocumentFragment();
      const parts = text.split(/(\s+)/);
      parts.forEach(part => {
        if (!part) return;
        if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(' ')); return; }
        const span = document.createElement('span');
        span.className = 'word';
        span.textContent = part;
        span.style.animationDelay = (word_idx++ * 32) + 'ms';
        frag.appendChild(span);
      });
      node.parentNode.replaceChild(frag, node);
    }
    function walk(node) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        const tag = node.tagName;
        if (tag === 'PRE' || tag === 'CODE' || tag === 'SCRIPT' || tag === 'STYLE' || tag === 'TEXTAREA') return;
        if (node.classList.contains('word')) return;
        Array.from(node.childNodes).forEach(walk);
      } else if (node.nodeType === Node.TEXT_NODE) {
        wrap_text_node(node);
      }
    }
    walk(container);
    return container;
  }

  function toastIn(el, delayMs) {
    if (!el) return;
    el.classList.add('toast-in');
    el.style.animationDelay = (delayMs || 0) + 'ms';
    return el;
  }

  function stagger(els, baseMs) {
    const list = Array.from(els || []);
    list.forEach((el, i) => toastIn(el, (baseMs || 0) + i * TOAST_STAGGER_MS));
    return list;
  }

  function autoAnimate() {
    const body = document.body;
    if (!body || !body.hasAttribute('data-sage-animate')) return;

    // Word-cascade the page heading(s)
    document.querySelectorAll('[data-sage-words]').forEach(el => {
      wrapWords(el);
      el.querySelectorAll('.word').forEach(w => w.style.animationDelay = ((+w.style.animationDelay.replace('ms','')||0)) + 'ms');
    });

    // Stagger common card/list containers
    document.querySelectorAll('[data-sage-stagger]').forEach(group => {
      const children = group.children;
      const step = parseInt(group.getAttribute('data-sage-stagger'), 10) || TOAST_STAGGER_MS;
      Array.from(children).forEach((child, i) => toastIn(child, i * step));
    });

    // Observe dynamic content: new children of [data-sage-stagger] animate too
    document.querySelectorAll('[data-sage-stagger]').forEach(group => {
      const step = parseInt(group.getAttribute('data-sage-stagger'), 10) || TOAST_STAGGER_MS;
      const count = group.children.length;
      new MutationObserver(muts => {
        muts.forEach(mu => {
          Array.from(mu.addedNodes).filter(n => n.nodeType === 1).forEach((n, i) => {
            toastIn(n, mu.addedNodes.length > 1 ? i * step : 0);
          });
        });
      }).observe(group, { childList: true });
    });
  }

  window.SageAnimate = { wrapWords, toastIn, stagger, autoAnimate };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoAnimate);
  } else {
    autoAnimate();
  }
})();
