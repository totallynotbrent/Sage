/*
 * dither.js — ordered/error-diffusion dithering with image-palette extraction
 * Spike for the Sage home re-think. New in this revision:
 *   - extract a color palette from the image (k-means), drive # of colors
 *   - dither modes: bayer (2/4/8 matrix), floyd-steinberg, none
 *   - multi-tone rendering (N levels from the palette), not just 1-bit
 * Image source is swappable (file / URL / procedural placeholder) because the
 * hero image isn't decided yet. Darkness + edge-fade to rose-pine are handled
 * by the consuming variant (CSS overlays), not the canvas.
 */
(function (global) {
  'use strict';

  // ---- Bayer ordered threshold matrix (recursive, checkered base) ----
  const _bayerCache = {};
  function bayer(n) {
    if (_bayerCache[n]) return _bayerCache[n];
    let m = [[0]];
    const offset = [[0, 2], [3, 1]];
    for (let s = 1; s < n; s *= 2) {
      const t = [];
      for (let y = 0; y < 2 * s; y++) {
        t[y] = [];
        for (let x = 0; x < 2 * s; x++) {
          const r = Math.floor(y / s), c = Math.floor(x / s);
          t[y][x] = 4 * m[y % s][x % s] + offset[r][c];
        }
      }
      m = t;
    }
    const N = m.length, M = [];
    for (let y = 0; y < N; y++) {
      M[y] = [];
      for (let x = 0; x < N; x++) M[y][x] = (m[y][x] + 0.5) / (N * N);
    }
    _bayerCache[n] = M;
    return M;
  }

  function load_image(src) {
    return new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error('could not load image: ' + String(src).slice(0, 48)));
      if (!/^data:/i.test(src)) im.crossOrigin = 'anonymous';
      im.src = src;
    });
  }

  const rgb = c => `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;
  const PALETTES = {
    monochrome: { dark: [12, 12, 12], light: [238, 238, 238] },
    rosepine: { dark: [25, 23, 36], light: [224, 222, 244] },
    invert: { dark: [238, 238, 238], light: [12, 12, 12] },
    blueprint: { dark: [4, 26, 40], light: [200, 226, 236] },
  };

  const luma = c => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  const sortLuma = cs => cs.slice().sort((a, b) => luma(a) - luma(b));
  const lerp = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const clamp = (v, a, b) => v < a ? a : (v > b ? b : v);
  function quantizeLevels(n, darkC, lightC) {
    n = Math.max(2, Math.round(Number(n)) || 2);
    const out = [];
    for (let i = 0; i < n; i++) out.push(lerp(darkC, lightC, i / (n - 1)));
    return out;
  }
  // Error-diffusion kernels, ported from the ditherit.com algorithm set
  // (weights: Floyd-Steinberg 7/5/3/16, Stucki 42-base, Burkes 32-base, Atkinson 1/8).
  const DIFFUSION_KERNELS = {
    'floyd-steinberg': [
      { dx: 1, dy: 0, w: 7 / 16 }, { dx: -1, dy: 1, w: 3 / 16 },
      { dx: 0, dy: 1, w: 5 / 16 }, { dx: 1, dy: 1, w: 1 / 16 },
    ],
    atkinson: [
      { dx: 1, dy: 0, w: 1 / 8 }, { dx: 2, dy: 0, w: 1 / 8 },
      { dx: -1, dy: 1, w: 1 / 8 }, { dx: 0, dy: 1, w: 1 / 8 },
      { dx: 1, dy: 1, w: 1 / 8 }, { dx: 0, dy: 2, w: 1 / 8 },
    ],
    stucki: [
      { dx: 1, dy: 0, w: 8 / 42 }, { dx: 2, dy: 0, w: 4 / 42 },
      { dx: -2, dy: 1, w: 2 / 42 }, { dx: -1, dy: 1, w: 4 / 42 },
      { dx: 0, dy: 1, w: 8 / 42 }, { dx: 1, dy: 1, w: 4 / 42 },
      { dx: 2, dy: 1, w: 2 / 42 },
      { dx: -2, dy: 2, w: 1 / 42 }, { dx: -1, dy: 2, w: 2 / 42 },
      { dx: 0, dy: 2, w: 4 / 42 }, { dx: 1, dy: 2, w: 2 / 42 },
      { dx: 2, dy: 2, w: 1 / 42 },
    ],
    burkes: [
      { dx: 1, dy: 0, w: 8 / 32 }, { dx: 2, dy: 0, w: 4 / 32 },
      { dx: -2, dy: 1, w: 2 / 32 }, { dx: -1, dy: 1, w: 4 / 32 },
      { dx: 0, dy: 1, w: 8 / 32 }, { dx: 1, dy: 1, w: 4 / 32 },
      { dx: 2, dy: 1, w: 2 / 32 },
    ],
  };

  // k-means palette extraction over a 64x64 downsample.
  function extractPalette(img, k) {
    const w = 64, h = 64;
    const cv = document.createElement('canvas');
    cv.width = w; cv.height = h;
    const c = cv.getContext('2d', { willReadFrequently: true });
    c.drawImage(img, 0, 0, w, h);
    const d = c.getImageData(0, 0, w, h).data;
    const pts = [];
    for (let i = 0; i < d.length; i += 4) pts.push([d[i], d[i + 1], d[i + 2]]);
    // Greedy farthest-point seeding: guarantees initial means are spread across
    // distinct colors (even-spaced sampling could land two seeds in one cluster
    // and collapse it). k <= pts.length by construction of the downsample.
    const means = [pts[0].slice()];
    const dist2 = new Array(pts.length).fill(Infinity);
    while (means.length < k) {
      const last = means[means.length - 1];
      let best = -1, bestD = -1;
      for (let i = 0; i < pts.length; i++) {
        const dx = pts[i][0] - last[0], dy = pts[i][1] - last[1], dz = pts[i][2] - last[2];
        const d = dx * dx + dy * dy + dz * dz;
        if (d < dist2[i]) dist2[i] = d;
        if (dist2[i] > bestD) { bestD = dist2[i]; best = i; }
      }
      if (best < 0) break;
      means.push(pts[best].slice());
    }
    for (let it = 0; it < 10; it++) {
      const sums = Array.from({ length: k }, () => [0, 0, 0]);
      const cnt = new Array(k).fill(0);
      for (const p of pts) {
        let bi = 0, bd = Infinity;
        for (let m = 0; m < k; m++) {
          const dx = p[0] - means[m][0], dy = p[1] - means[m][1], dz = p[2] - means[m][2];
          const dd = dx * dx + dy * dy + dz * dz;
          if (dd < bd) { bd = dd; bi = m; }
        }
        sums[bi][0] += p[0]; sums[bi][1] += p[1]; sums[bi][2] += p[2]; cnt[bi]++;
      }
      for (let m = 0; m < k; m++) if (cnt[m]) means[m] = [sums[m][0] / cnt[m], sums[m][1] / cnt[m], sums[m][2] / cnt[m]];
    }
    return sortLuma(means.map(m => m.map(Math.round)));
  }

  function build_palette(img, opt) {
    let pal;
    if (opt.palette === 'extract') pal = extractAuto(img, clamp(opt.colors, 2, 50));
    else {
      const p = PALETTES[opt.palette] || PALETTES.rosepine;
      pal = quantizeLevels(clamp(opt.colors, 2, 50), p.dark, p.light);
    }
    return applySaturation(pal, opt.saturation == null ? 1 : clamp(opt.saturation, 0, 2));
  }

  // Saturation dial: 0 = fully desaturate to grays, 1 = as extracted, >1 boost.
  const saturateColor = (c, s) => {
    if (s === 1) return c;
    const g = luma(c);
    return [g + (c[0] - g) * s, g + (c[1] - g) * s, g + (c[2] - g) * s].map(v => Math.round(Math.max(0, Math.min(255, v))));
  };
  function applySaturation(colors, s) { return s === 1 ? colors : colors.map(c => saturateColor(c, s)); }

  // Auto color extraction matching ditherit.com: which vendors leeoniya's
  // RgbQuant.js (its own hybrid quantizer, not k-means). We use RgbQuant when
  // it's loaded (window.RgbQuant) and fall back to our k-means extractPalette.
  function extractAuto(img, k) {
    const rq = (typeof window !== 'undefined' && typeof window.RgbQuant === 'function')
      ? window.RgbQuant : null;
    if (rq) {
      const w = 256, h = 256;
      const cv = document.createElement('canvas');
      cv.width = w; cv.height = h;
      const c = cv.getContext('2d', { willReadFrequently: true });
      c.drawImage(img, 0, 0, w, h);
      const d = c.getImageData(0, 0, w, h).data;
      try {
        const quant = new rq({ colors: clamp(k, 2, 50) });
        quant.reduce(d, w);
        const raw = quant.palette(true);
        const out = [];
        for (const p of raw) if (p && p.length >= 3) out.push([p[0] | 0, p[1] | 0, p[2] | 0]);
        const sorted = sortLuma(out);
        if (sorted.length >= 2) return sorted;
      } catch (e) { /* fall back to k-means below */ }
    }
    return extractPalette(img, k);
  }

  // Core pass: render a dithered N-level image of `img` filling `dest`.
  // `palette_c` = N sorted colors (darkest -> lightest).
  async function dither_into(dest, img, opt) {
    opt = Object.assign({
      cell: 6, matrix: 8, mode: 'diffusion', kernel: 'floyd-steinberg',
      serpentine: false, level: 0.5, spread: 1, invert: false,
    }, opt || {});
    const pal = opt.palette_c && opt.palette_c.length ? opt.palette_c : null;
    const colors = pal || quantizeLevels(2, PALETTES.rosepine.dark, PALETTES.rosepine.light);
    const N = colors.length;
    const workW = Math.max(8, Math.round(dest.width / opt.cell));
    const workH = Math.max(8, Math.round(dest.height / opt.cell));

    const oc = document.createElement('canvas');
    oc.width = workW; oc.height = workH;
    const octx = oc.getContext('2d', { willReadFrequently: true });
    octx.drawImage(img, 0, 0, workW, workH);
    const px = octx.getImageData(0, 0, workW, workH).data;

    // Convert to a per-cell value in [0, N-1] (0..1 luma scaled), with exposure offset.
    const W = workW, H = workH;
    const val = new Float32Array(W * H);
    const expo = (opt.level - 0.5) * 0.6;
    for (let i = 0; i < W * H; i++) {
      const j = i * 4;
      let g = (0.2126 * px[j] + 0.7152 * px[j + 1] + 0.0722 * px[j + 2]) / 255;
      g = clamp(g + expo, 0, 1);
      val[i] = g * (N - 1);
    }

    const out = octx.createImageData(W, H);
    const colorAt = (idx) => colors[clamp(idx, 0, N - 1)];

    if (opt.mode === 'bayer') {
      const T = bayer(opt.matrix), n = T.length;
      for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
          const v = val[y * W + x];
          const base = Math.floor(v), frac = v - base;
          const t = clamp((T[y % n][x % n] - 0.5) * 2 * opt.spread + 0.5, 0, 1);
          let idx = base + (frac >= t ? 1 : 0);
          if (opt.invert) idx = N - 1 - idx;
          const i = (y * W + x) * 4, c = colorAt(idx);
          out.data[i] = c[0]; out.data[i + 1] = c[1]; out.data[i + 2] = c[2]; out.data[i + 3] = 255;
        }
      }
    } else if (opt.mode === 'diffusion') {
      const kernel = DIFFUSION_KERNELS[opt.kernel] || DIFFUSION_KERNELS['floyd-steinberg'];
      const serp = !!opt.serpentine;
      for (let y = 0; y < H; y++) {
        const leftToRight = !serp || (y % 2 === 0);
        for (let x0 = 0; x0 < W; x0++) {
          const x = leftToRight ? x0 : (W - 1 - x0);
          const i = y * W + x;
          const old = val[i];
          let idx = Math.round(old);
          if (opt.invert) idx = N - 1 - idx;
          const err = old - Math.round(old);
          for (const k of kernel) {
            const kx = leftToRight ? x + k.dx : x - k.dx;
            const ky = y + k.dy;
            if (kx >= 0 && kx < W && ky >= 0 && ky < H) val[ky * W + kx] += err * k.w;
          }
          const c = colorAt(idx);
          const j = i * 4;
          out.data[j] = c[0]; out.data[j + 1] = c[1]; out.data[j + 2] = c[2]; out.data[j + 3] = 255;
        }
      }
    } else { // none — plain quantization
      for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
          let idx = Math.round(val[y * W + x]);
          if (opt.invert) idx = N - 1 - idx;
          const i = (y * W + x) * 4, c = colorAt(idx);
          out.data[i] = c[0]; out.data[i + 1] = c[1]; out.data[i + 2] = c[2]; out.data[i + 3] = 255;
        }
      }
    }
    octx.putImageData(out, 0, 0);

    const ctx = dest.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = rgb(colors[0]);
    ctx.fillRect(0, 0, dest.width, dest.height);
    ctx.drawImage(oc, 0, 0, W, H, 0, 0, dest.width, dest.height);
    return dest;
  }

  async function renderBase(dest, src, opt) {
    opt = Object.assign({ width: 1200, palette: 'extract', colors: 4, saturation: 1, noise: { kind: 'grain', amount: 0.3 } }, opt || {});
    const img = await load_image(src);
    dest.width = opt.width;
    dest.height = Math.max(1, Math.round(opt.width * img.height / img.width));
    opt.palette_c = build_palette(img, opt);
    opt.palette_c = opt.palette_c.filter(Boolean);
    await dither_into(dest, img, opt);
    apply_noise(dest, opt.noise);
    return dest;
  }

  async function renderCover(dest, src, opt) {
    opt = Object.assign({
      width: document.documentElement.clientWidth || innerWidth,
      height: document.documentElement.clientHeight || innerHeight,
      cell: 8, palette: 'extract', colors: 4, saturation: 1, anchor: 0.5,
      mode: 'diffusion', kernel: 'floyd-steinberg', serpentine: false,
      matrix: 8, noise: { kind: 'grain', amount: 0.3 },
    }, opt || {});
    const img = await load_image(src);
    dest.width = opt.width; dest.height = opt.height;
    const iw = img.width, ih = img.height, arW = dest.width / dest.height;
    const anchor = opt.anchor == null ? 0.5 : Math.max(0, Math.min(1, opt.anchor));
    let sw = iw, sh = ih, sx = 0, sy = 0;
    const srcAr = iw / ih;
    if (srcAr > arW) { sw = ih * arW; sx = (iw - sw) * anchor; }
    else { sh = iw / arW; sy = (ih - sh) * anchor; }
    const tmp = document.createElement('canvas');
    tmp.width = Math.max(8, Math.round(sw)); tmp.height = Math.max(8, Math.round(sh));
    tmp.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, tmp.width, tmp.height);
    opt.palette_c = build_palette(img, opt);
    opt.palette_c = opt.palette_c.filter(Boolean);
    await dither_into(dest, tmp, opt);
    apply_noise(dest, opt.noise);
    return dest;
  }

  function apply_noise(dest, opt) {
    opt = Object.assign({ kind: 'grain', amount: 0.30, cell: 3, blend: 'overlay' }, opt || {});
    const ctx = dest.getContext('2d');
    if (opt.kind === 'none' || !opt.amount) return;
    // 'subtract' etches the noise into the image (difference blend) instead of
    // laying it on top (source-over). Pure black/white noise difference will
    // only ever darken/invert toward the ink — never lighten past the base.
    const comp = opt.blend === 'subtract' ? 'difference' : 'source-over';
    if (opt.kind === 'grain') {
      const svg =
        '<svg xmlns="http://www.w3.org/2000/svg" width="140" height="140">' +
        '<filter id="n"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" stitchTiles="stitch"/>' +
        '<feColorMatrix type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 18 -8"/></filter>' +
        '<rect width="140" height="140" filter="url(#n)"/></svg>';
      const url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
      const pattern = ctx.createPattern((() => { const i = new Image(); i.src = url; return i; })(), 'repeat');
      ctx.globalAlpha = Math.min(1, opt.amount);
      ctx.fillStyle = pattern;
      ctx.globalCompositeOperation = comp;
      ctx.fillRect(0, 0, dest.width, dest.height);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'source-over';
    } else {
      ctx.globalAlpha = Math.min(1, opt.amount);
      ctx.globalCompositeOperation = comp;
      const c = Math.max(1, opt.cell);
      for (let y = 0; y < dest.height; y += c) {
        for (let x = 0; x < dest.width; x += c) {
          if (Math.random() < 0.5) {
            ctx.fillStyle = Math.random() < 0.5 ? '#0a0a0c' : '#eeeee6';
            ctx.fillRect(x, y, c, c);
          }
        }
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'source-over';
    }
  }

  async function paint(canvas, src, opts) {
    await renderBase(canvas, src, opts);
    apply_noise(canvas, opts.noise || {});
    return canvas;
  }

  function placeholder(width, height) {
    const cv = document.createElement('canvas');
    cv.width = width || 900; cv.height = height || 1200;
    const ctx = cv.getContext('2d');
    const b = cv.width / cv.height;
    let off = b > 1 ? 0 : (1 - b) * cv.height;
    void off;
    const g = ctx.createLinearGradient(0, 0, cv.width, cv.height);
    g.addColorStop(0, '#514a6e'); g.addColorStop(0.5, '#1f1d2e'); g.addColorStop(1, '#0c0a14');
    ctx.fillStyle = g; ctx.fillRect(0, 0, cv.width, cv.height);
    const r1 = ctx.createRadialGradient(cv.width * 0.72, cv.height * 0.26, 10, cv.width * 0.72, cv.height * 0.26, cv.height * 0.44);
    r1.addColorStop(0, 'rgba(236,238,244,0.5)'); r1.addColorStop(1, 'rgba(236,238,244,0)');
    ctx.fillStyle = r1; ctx.fillRect(0, 0, cv.width, cv.height);
    const r2 = ctx.createRadialGradient(cv.width * 0.18, cv.height * 0.82, 8, cv.width * 0.18, cv.height * 0.82, cv.height * 0.5);
    r2.addColorStop(0, 'rgba(156,207,216,0.55)'); r2.addColorStop(1, 'rgba(156,207,216,0)');
    ctx.fillStyle = r2; ctx.fillRect(0, 0, cv.width, cv.height);
    const g2 = ctx.createLinearGradient(0, cv.height * 0.4, cv.width, cv.height * 0.64);
    g2.addColorStop(0, 'rgba(235,111,146,0.55)'); g2.addColorStop(1, 'rgba(235,111,146,0.02)');
    ctx.fillStyle = g2; ctx.fillRect(0, 0, cv.width, cv.height);
    return cv.toDataURL('image/png');
  }

  function nested(obj, path) { return path.split('.').reduce((o, k) => (o == null ? o : o[k]), obj); }
  function set(obj, path, val) {
    const parts = path.split('.');
    let o = obj;
    for (let i = 0; i < parts.length - 1; i++) o = o[parts[i]] = o[parts[i]] || {};
    o[parts[parts.length - 1]] = val;
  }

  // Shared control bar. `palette` select adds 'extract' (image-derived).
  function controlBar(container, target, boxEl, opts) {
    const api = Object.assign({}, opts);
    function build() {
      if (!boxEl) boxEl = target.parentElement;
      const wrap = document.createElement('div');
      wrap.className = 'dt-controls';
      const rows = [
        ['cell', 'Cell size', 'range', 2, 20, 1],
        ['mode', 'Dither mode', 'select', ['diffusion', 'bayer', 'none'], null, null],
        ['kernel', 'Diffusion', 'select', ['floyd-steinberg', 'atkinson', 'stucki', 'burkes'], null, null],
        ['serpentine', 'Serpentine', 'checkbox', null, null, null],
        ['matrix', 'Bayer matrix', 'select', [2, 4, 8, 16], null, null],
        ['colors', 'Colors', 'range', 2, 10, 1],
        ['palette', 'Palette', 'select', ['extract', 'monochrome', 'rosepine', 'invert', 'blueprint'], null, null],
        ['invert', 'Invert', 'checkbox', null, null, null],
        ['level', 'Exposure', 'range', 0, 1, 0.01],
        ['saturation', 'Saturation', 'range', 0, 2, 0.01],
        ['spread', 'Spread', 'range', 0.2, 2.2, 0.05],
        ['noise.kind', 'Noise', 'select', ['none', 'grain', 'static'], null, null],
        ['noise.blend', 'Noise blend', 'select', ['overlay', 'subtract'], null, null],
        ['noise.amount', 'Noise amount', 'range', 0, 1, 0.01],
        ['image', 'Image', 'file', null, null, null],
      ];
      rows.forEach(([key, label, type, a, b, step]) => {
        const rowEl = document.createElement('label');
        rowEl.className = 'dt-row';
        const lab = document.createElement('span'); lab.textContent = label;
        rowEl.appendChild(lab);
        if (key === 'colors') {
          // Dual control: slider for quick + a number box for typing any count.
          const dup = document.createElement('div');
          dup.style.cssText = 'display:flex;gap:8px;align-items:center';
          const range = document.createElement('input');
          range.type = 'range'; range.min = 2; range.max = 24; range.step = 1;
          range.style.flex = '1';
          const num = document.createElement('input');
          num.type = 'number'; num.min = 2; num.max = 40; num.step = 1;
          num.style.cssText = 'width:64px;background:var(--overlay,#26233a);color:inherit;border:1px solid var(--border,#403d52);border-radius:6px;padding:4px 6px;font:inherit';
          const init = Math.max(2, Math.round(Number(nested(api, 'colors')) || 4));
          range.value = Math.min(24, init); num.value = init;
          const sync = () => {
            const v = Math.max(2, Math.min(40, Math.round(parseFloat(num.value) || 2)));
            num.value = v; range.value = Math.min(24, v);
            set(api, 'colors', v); refresh();
          };
          range.addEventListener('input', () => { num.value = range.value; set(api, 'colors', Math.round(parseFloat(range.value))); refresh(); });
          num.addEventListener('input', sync);
          dup.append(range, num);
          rowEl.appendChild(dup);
          wrap.appendChild(rowEl);
          return;
        }
        let ctl;
        const mkval = () => {
          const s = document.createElement('span');
          s.className = 'dt-val';
          s.style.cssText = 'font:500 11px/1.4 ui-monospace,"JetBrains Mono",monospace;color:var(--faint,#6e6a86);letter-spacing:.02em';
          return s;
        };
        if (type === 'range') {
          ctl = document.createElement('input');
          ctl.type = 'range'; ctl.min = a; ctl.max = b; ctl.step = step;
          const cur = nested(api, key);
          ctl.value = cur == null ? a : cur;
          const val = mkval(); val.textContent = ctl.value;
          ctl.addEventListener('input', () => { set(api, key, parseFloat(ctl.value)); val.textContent = ctl.value; refresh(); });
          rowEl.appendChild(ctl); rowEl.appendChild(val);
        } else if (type === 'select') {
          ctl = document.createElement('select');
          a.forEach(v => { const o = document.createElement('option'); o.value = v; o.textContent = v; ctl.appendChild(o); });
          ctl.value = nested(api, key) || a[0];
          const val = mkval(); val.textContent = ctl.value;
          ctl.addEventListener('input', () => { set(api, key, ctl.value); val.textContent = ctl.value; refresh(); });
          rowEl.appendChild(ctl); rowEl.appendChild(val);
        } else if (type === 'checkbox') {
          ctl = document.createElement('input'); ctl.type = 'checkbox';
          const cur = !!nested(api, key); ctl.checked = cur;
          const val = mkval(); val.textContent = cur ? 'on' : 'off';
          ctl.addEventListener('input', () => { set(api, key, ctl.checked); val.textContent = ctl.checked ? 'on' : 'off'; refresh(); });
          rowEl.appendChild(ctl); rowEl.appendChild(val);
        } else if (type === 'file') {
          ctl = document.createElement('input'); ctl.type = 'file'; ctl.accept = 'image/*';
          ctl.addEventListener('change', () => {
            if (!ctl.files || !ctl.files[0]) return;
            const r = new FileReader();
            r.onload = () => { api.src = r.result; refresh(); };
            r.readAsDataURL(ctl.files[0]);
          });
          rowEl.appendChild(ctl);
        }
        ctl.className = 'dt-ctl';
        wrap.appendChild(rowEl);
      });
      container.innerHTML = '';
      container.appendChild(wrap);
    }
    async function refresh() {
      const src = api.src || placeholder();
      const fn = api.cover ? renderCover : paint;
      await fn(target, src, api).catch(err => { console.error(err); });
      if (boxEl) boxEl.style.opacity = '1';
      if (typeof api.onPaint === 'function') api.onPaint();
    }
    function bindUrl() {
      const inp = document.getElementById('dt-imgurl');
      if (inp) inp.addEventListener('input', () => { if (inp.value.trim()) { api.src = inp.value.trim(); refresh(); } });
    }
    build(); bindUrl(); refresh();
    return { refresh, api };
  }

  global.dither = {
    renderBase, renderCover, apply_noise, paint, placeholder, bayer, PALETTES,
    extractPalette, extractAuto, applySaturation, quantizeLevels, DIFFUSION_KERNELS, controlBar,
    dither_into, build_palette, load_image,
  };
})(window);