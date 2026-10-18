/* stage.js: the scroll intro and the cube build. Plain JavaScript, no libraries.
 *
 * 1. Intro: market-noise fragments start bunched in the middle and spread out
 *    as the user scrolls; the command bar fades in near the end of the track.
 * 2. Working: the polled #job fragment (swapped in by htmx) reports the real
 *    pipeline stage. Each completed stage pulls more fragments into the cube.
 *    Rejected fragments are struck out and fall away.
 * 3. Done: the top of the cube lifts off and the brief rises into view.
 *
 * Only opacity, position and transform are changed, through the CSSOM, so the
 * Content-Security-Policy can keep forbidding inline styles.
 */
(function () {
  'use strict';

  var doc = document.documentElement;
  doc.classList.remove('no-js');

  var stage = document.getElementById('stage');
  if (!stage) return;

  var intro = document.getElementById('intro');
  var bar = document.getElementById('bar');
  var cue = document.getElementById('cue');
  var ghost = document.getElementById('ghost');
  var headline = document.getElementById('headline');
  var subline = document.getElementById('subline');
  var result = document.getElementById('result');
  var status = document.getElementById('status');
  var statusLine = document.getElementById('status-line');
  var statusMeta = document.getElementById('status-meta');
  var segs = Array.prototype.slice.call(document.querySelectorAll('#segs .seg'));
  var retry = document.getElementById('retry');
  var briefView = document.getElementById('brief-view');
  var themeInput = document.getElementById('theme');
  var riskInput = document.getElementById('risk');
  var riskOut = document.getElementById('risk-out');
  var chips = Array.prototype.slice.call(document.querySelectorAll('.chip[data-sector]'));

  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- Geometry: an isometric 4x4x4 cube in a 1040 x 560 stage ----------
  var SW = 1040, SH = 560, CX = 520, CY = 300, S = 40, N = 4, K = 34.641;
  function P(x, y, z) { return [CX + (x - y) * K, CY + (x + y - 4) * 20 - (z - 2) * S]; }
  function clamp(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function seg(p, a, b) { return clamp((p - a) / (b - a)); }
  function ease(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function rng(seed) {
    var s = seed >>> 0;
    return function () {
      s = (s + 0x6d2b79f5) | 0;
      var t = Math.imul(s ^ (s >>> 15), 1 | s);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function pct(v, total) { return (v / total * 100).toFixed(3) + '%'; }

  var LABELS = ['rate cut odds ▲', 'CPI hot', 'sentiment +0.41', 'volume 2.3×', 'guidance cut', 'earnings beat',
    'supply shock', 'ETF inflows', 'short interest ▲', 'yield 4.1%', 'copper ▲ 1.2%', 'risk-off', 'FDA date',
    'chatter ▲', 'funding rate', 'whale wallets', 'revenue miss', 'upgrade', 'downgrade', 'oil ▼ 0.8%',
    'gold bid', 'layoffs', 'buyback', 'hashrate ▲'];

  var cells = [];
  var i, j, k, a, b, d;
  for (k = 0; k < N; k++) for (i = 0; i < N; i++) {
    a = P(i, 4, k); d = P(i, 4, k + 1);
    cells.push({ face: 'c-left', left: a[0], top: d[1], w: 0.866 * S, h: 1.5 * S, cx: a[0] + 0.433 * S, cy: d[1] + 0.75 * S, z: k, lift: false });
  }
  for (k = 0; k < N; k++) for (j = 0; j < N; j++) {
    b = P(4, j + 1, k); d = P(4, j, k + 1);
    cells.push({ face: 'c-right', left: b[0], top: d[1], w: 0.866 * S, h: 1.5 * S, cx: b[0] + 0.433 * S, cy: d[1] + 0.75 * S, z: k, lift: false });
  }
  for (i = 0; i < N; i++) for (j = 0; j < N; j++) {
    var l = P(i, j + 1, 4), t = P(i, j, 4);
    cells.push({ face: 'c-top', left: l[0], top: t[1], w: 1.732 * S, h: S, cx: l[0] + 0.866 * S, cy: t[1] + 0.5 * S, z: 4, lift: true });
  }

  var r = rng(7);
  cells.map(function (c, idx) { return { idx: idx, key: c.z + r() * 0.9 }; })
    .sort(function (x, y) { return x.key - y.key; })
    .forEach(function (o, rank) { cells[o.idx].a = 0.04 + 0.76 * rank / (cells.length - 1); });

  var frags = [];
  for (var n = 0; n < cells.length + 20; n++) {
    var bx, by;
    do { bx = 40 + r() * (SW - 80); by = 24 + r() * (SH - 48); }
    while (bx > 360 && bx < 680 && by > 120 && by < 480 && r() < 0.8);
    frags.push({ label: LABELS[n % LABELS.length], bx: bx, by: by, ph: r() * 6.28,
      cell: n < cells.length ? n : -1, a: n < cells.length ? cells[n].a : 0.1 + r() * 0.7 });
  }

  // Build the DOM once.
  cells.forEach(function (c) {
    var el = document.createElement('div');
    el.className = 'cell ' + c.face;
    el.style.left = pct(c.left, SW); el.style.top = pct(c.top, SH);
    el.style.width = pct(c.w, SW); el.style.height = pct(c.h, SH);
    stage.appendChild(el); c.el = el;
  });
  frags.forEach(function (f) {
    var el = document.createElement('div');
    el.className = 'frag';
    el.textContent = f.label;
    stage.appendChild(el); f.el = el;
  });

  // ---------- State ----------
  var mode = 'intro';      // intro | working | opening | done | failed
  var u = 0, uS = 0;       // scroll progress through the intro, raw and smoothed
  var p = 0, pTarget = 0;  // build progress, 0..1
  var openT = 0, clock = 0, last = 0;

  // ---------- Scroll ----------
  function readScroll() {
    if (mode !== 'intro') return;
    var range = intro.offsetHeight - window.innerHeight;
    u = range > 0 ? clamp(-intro.getBoundingClientRect().top / range) : 1;
  }
  window.addEventListener('scroll', readScroll, { passive: true });
  window.addEventListener('resize', readScroll);
  readScroll();

  // ---------- Form helpers ----------
  chips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      themeInput.value = chip.getAttribute('data-sector');
      chips.forEach(function (c) { c.setAttribute('aria-pressed', c === chip ? 'true' : 'false'); });
    });
  });
  themeInput.addEventListener('input', function () {
    chips.forEach(function (c) { c.setAttribute('aria-pressed', c.getAttribute('data-sector') === themeInput.value ? 'true' : 'false'); });
  });
  function riskWord(v) { return v <= 33 ? 'Cautious' : v <= 66 ? 'Balanced' : 'Bold'; }
  function showRisk() {
    var v = Number(riskInput.value);
    riskOut.innerHTML = '';
    var num = document.createElement('span');
    num.className = 'mono'; num.textContent = String(v);
    riskOut.appendChild(num);
    riskOut.appendChild(document.createTextNode(' · ' + riskWord(v)));
    riskInput.setAttribute('aria-valuetext', v + ' out of 100, ' + riskWord(v));
  }
  riskInput.addEventListener('input', showRisk);
  showRisk();

  // ---------- Job updates from htmx ----------
  function setText(el, text) { if (el.textContent !== text) el.textContent = text; }

  function startWorking() {
    mode = 'working';
    u = uS = 1;
    intro.classList.add('done');
    window.scrollTo(0, 0);
    doc.classList.add('locked');
    bar.hidden = true;
    cue.hidden = true;
    status.hidden = false;
    setText(headline, 'Sorting signal from noise.');
    setText(subline, 'Every fragment is weighed. Most become part of the strategy, the rest are struck out.');
  }

  function onJob(job) {
    var st = job.getAttribute('data-status');
    var step = Number(job.getAttribute('data-step')) || 0;
    var total = Number(job.getAttribute('data-total')) || 7;
    if (mode === 'intro') startWorking();
    if (mode !== 'working') return;

    var label = job.getAttribute('data-stage-label') || 'Starting';
    statusMeta.textContent = job.getAttribute('data-theme') + ' · risk ' + job.getAttribute('data-risk') +
      (st === 'RUNNING' && step ? ' · stage ' + step + ' of ' + total : '');
    segs.forEach(function (s, idx) { s.classList.toggle('on', st === 'SUCCEEDED' || idx < step); });

    if (st === 'QUEUED') { setText(statusLine, 'Waiting for an agent…'); pTarget = 0.02; }
    else if (st === 'RUNNING') { setText(statusLine, label + '…'); pTarget = Math.max(pTarget, step / total); }
    else if (st === 'SUCCEEDED') { setText(statusLine, 'Checking hard limits…'); pTarget = 1; }
    else if (st === 'FAILED') {
      mode = 'failed';
      var err = job.querySelector('.job-error');
      setText(statusLine, err ? err.textContent : 'This strategy could not be generated.');
      statusLine.classList.add('failed');
      setText(headline, 'No strategy this time.');
      setText(subline, 'Nothing was kept. You can try again with another sector.');
      retry.hidden = false;
      doc.classList.remove('locked');
    }
  }

  function scan() {
    var job = result.querySelector('#job');
    if (job) onJob(job);
  }
  new MutationObserver(scan).observe(result, { childList: true, subtree: true, attributes: true });

  function openCube() {
    mode = 'opening';
    openT = 0;
    setText(headline, 'Strategy assembled.');
    setText(subline, 'Checked against every hard limit.');
    status.hidden = true;
    window.setTimeout(showBrief, reduced ? 300 : 2300);
  }

  function showBrief() {
    var tpl = document.getElementById('brief-tpl');
    if (!tpl) return;
    briefView.innerHTML = '';
    briefView.appendChild(tpl.content.cloneNode(true));
    intro.hidden = true;
    briefView.hidden = false;
    doc.classList.remove('locked');
    window.scrollTo(0, 0);
    mode = 'done';
    var copy = briefView.querySelector('[data-copy]');
    var text = briefView.querySelector('[data-brief]');
    if (copy && text && navigator.clipboard) {
      copy.addEventListener('click', function () {
        navigator.clipboard.writeText(text.textContent).then(function () { copy.textContent = 'Copied'; });
      });
    }
  }

  // ---------- Frame loop ----------
  function frame(now) {
    var dt = last ? Math.min(now - last, 50) : 16;
    last = now;
    if (!reduced) clock += dt;

    if (mode === 'intro') {
      uS = reduced ? u : uS + (u - uS) * Math.min(1, dt * 0.012);
      var intoNoise = uS < 0.5;
      setText(headline, intoNoise ? 'The market is noise.' : 'Harvest an agentic trading strategy.');
      setText(subline, intoNoise ? 'Thousands of signals, every minute. Most of them rumor.' : 'Pick a sector. Our agents keep the signal and drop the rest.');
      var reveal = ease(seg(uS, 0.6, 0.95));
      bar.style.opacity = reveal.toFixed(3);
      bar.style.transform = 'translateY(' + (24 * (1 - reveal)).toFixed(1) + 'px)';
      bar.classList.toggle('live', uS > 0.8);
      cue.style.opacity = (1 - seg(uS, 0, 0.25)).toFixed(3);
    }
    if (mode === 'working') {
      p = reduced ? pTarget : p + (pTarget - p) * Math.min(1, dt * 0.0035);
      if (pTarget >= 1 && p > 0.995) { p = 1; openCube(); }
    }
    if (mode === 'opening') openT = Math.min(1, openT + (reduced ? 1 : dt / 1800));
    if (mode === 'failed') p = Math.max(0, p - dt * 0.0006);

    draw();
    if (mode !== 'done') window.requestAnimationFrame(frame);
  }

  function draw() {
    var spread = mode === 'intro' ? 0.14 + 0.86 * ease(seg(uS, 0, 0.85)) : 1;
    var lit = [];
    var t = clock;
    frags.forEach(function (f) {
      var ox = CX + (f.bx - CX) * spread, oy = CY + (f.by - CY) * spread * 0.92;
      var wx = ox + (4 + 6 * spread) * Math.sin(t * 0.0005 + f.ph);
      var wy = oy + (3 + 4 * spread) * Math.cos(t * 0.0004 + f.ph * 1.3);
      var x = wx, y = wy, op = 0.85, out = false;
      if (f.cell >= 0) {
        var q = ease(seg(p, f.a, f.a + 0.14));
        var c = cells[f.cell];
        x = wx + (c.cx - wx) * q; y = wy + (c.cy - wy) * q;
        op = 0.85 * (1 - seg(q, 0.55, 0.95));
        lit[f.cell] = seg(q, 0.6, 1);
      } else {
        var rq = seg(p, f.a, f.a + 0.1);
        out = rq > 0;
        y = wy + 24 * rq; op = 0.85 * (1 - rq);
      }
      f.el.style.left = pct(x, SW); f.el.style.top = pct(y, SH);
      f.el.style.opacity = op.toFixed(3);
      if (f.out !== out) { f.el.classList.toggle('out', out); f.out = out; }
    });
    var o = ease(openT);
    cells.forEach(function (c, idx) {
      var op = lit[idx] || 0, dy = 0;
      if (c.lift && (mode === 'opening' || mode === 'done')) {
        var lq = seg(o, (idx % 16) * 0.025, (idx % 16) * 0.025 + 0.6);
        dy = -260 * ease(lq); op = op * (1 - lq);
      }
      c.el.style.opacity = op.toFixed(3);
      c.el.style.transform = 'translateY(' + dy.toFixed(1) + '%) scale(0.88)';
    });
    ghost.style.opacity = mode === 'working' ? '0.9' : mode === 'opening' ? (1 - o).toFixed(2) : '0';
  }

  window.requestAnimationFrame(frame);
})();
