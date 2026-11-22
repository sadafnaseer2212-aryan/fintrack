/* FinTrack shared UI: chart theme, motion, toasts, sparklines (no build step needed). */
const FT = {
  em: '#34d399', em2: '#10b981', em3: '#6ee7b7', teal: '#2dd4bf', graphite: '#4b5a54', grey: '#6b7a73',
  silver: '#a3b1ab', red: '#f87171', amber: '#fbbf24', blue: '#60a5fa', ink: '#e9f1ed', muted: '#8c9c95',
  grid: 'rgba(255,255,255,.06)',
  cats: ['#34d399', '#10b981', '#2dd4bf', '#6ee7b7', '#059669', '#a7f3d0', '#64748b', '#94a3b8', '#475569'],
  reduced: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
};
// aliases used by page scripts
Object.assign(FT, {green: FT.em2, greenMid: FT.em, greenLight: 'rgba(110,231,183,.35)', grey: '#64748b', greyDark: '#94a3b8'});
FT.pkr = (v) => (v < 0 ? '-' : '') + 'Rs ' + Math.round(Math.abs(v || 0)).toLocaleString('en-US');

/* ---------- Plotly dark theme ---------- */
FT.layout = (extra = {}) => {
  const base = {
    font: {family: 'Inter, system-ui, sans-serif', size: 12, color: FT.muted},
    margin: {t: 10, r: 10, b: 36, l: 58},
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    hoverlabel: {bgcolor: 'rgba(10,16,14,.95)', bordercolor: 'rgba(52,211,153,.35)', font: {color: FT.ink, family: 'Inter', size: 12}},
    legend: {orientation: 'h', x: 0, y: 1.14, font: {size: 12, color: FT.ink}, bgcolor: 'rgba(0,0,0,0)'},
    xaxis: {showgrid: false, linecolor: 'rgba(255,255,255,.1)', tickfont: {size: 11}, fixedrange: true, zeroline: false},
    yaxis: {gridcolor: FT.grid, zeroline: false, tickprefix: 'Rs ', tickformat: '~s', tickfont: {size: 11}, fixedrange: true},
    bargap: 0.35, bargroupgap: 0.15, hovermode: 'closest',
  };
  for (const k of Object.keys(extra)) {
    base[k] = (typeof extra[k] === 'object' && !Array.isArray(extra[k]) && base[k]) ? Object.assign({}, base[k], extra[k]) : extra[k];
  }
  return base;
};
FT.config = {responsive: true, displayModeBar: false};
/** Load Plotly only when a page actually draws a 2D chart (keeps the 3D dashboard light). */
FT.loadPlotly = () => FT._plotly || (FT._plotly = new Promise((ok, fail) => {
  if (typeof Plotly !== 'undefined') return ok();
  const s = document.createElement('script'); s.src = document.documentElement.dataset.plotly; s.onload = ok; s.onerror = fail; document.head.appendChild(s);
}));

/** Plot and (unless reduced motion) animate bars/lines growing from zero. */
FT.plot = (id, traces, layout = {}, opts = {}) => {
  const el = document.getElementById(id);
  if (!el) return;
  if (typeof Plotly === 'undefined') {
    if (FT.loadPlotly) { FT.loadPlotly().then(() => FT.plot(id, traces, layout, opts), () => { el.textContent = 'Chart library failed to load.'; }); return; }
    el.textContent = 'Chart library failed to load.'; return;
  }
  el.innerHTML = '';
  el.classList.remove('chart-loading');
  const animate = opts.animate !== false && !FT.reduced;
  if (!animate) { Plotly.newPlot(el, traces, FT.layout(layout), FT.config); return el; }
  const isGrowable = (t) => (t.type === 'bar' || t.type === 'scatter' || !t.type) && t.orientation !== 'h' && Array.isArray(t.y) && t.y.some(v => typeof v === 'number') && !t.fill;
  const start = traces.map(t => isGrowable(t) ? Object.assign({}, t, {y: t.y.map(v => (v === null ? null : 0))}) : t);
  const lay = FT.layout(layout);
  if (!lay.yaxis.range && !layout.yaxis?.autorange) {
    const all = traces.flatMap(t => (Array.isArray(t.y) ? t.y : [])).filter(v => typeof v === 'number');
    if (all.length) { const mx = Math.max(...all), mn = Math.min(0, ...all); lay.yaxis = Object.assign({}, lay.yaxis, {range: [mn * 1.12, mx * 1.12 || 1]}); }
  }
  Plotly.newPlot(el, start, lay, FT.config).then(() => {
    Plotly.animate(el, {data: traces.map(t => ({y: t.y}))}, {transition: {duration: 900, easing: 'cubic-out'}, frame: {duration: 900, redraw: false}})
      .then(() => { if (opts.after) opts.after(el); Plotly.relayout(el, {'yaxis.autorange': true}); });
  });
  return el;
};
FT.empty = (id, text) => {
  const el = document.getElementById(id);
  if (el) { el.classList.remove('chart-loading'); el.innerHTML = `<div class="empty"><i class="bi bi-bar-chart"></i>${text}</div>`; }
};

/* ---------- count-up numbers: <span data-count="1234.5" data-prefix="Rs " data-decimals="0"> ---------- */
FT.countUp = (root = document) => {
  root.querySelectorAll('[data-count]').forEach(el => {
    const target = parseFloat(el.dataset.count || '0');
    const dec = parseInt(el.dataset.decimals || '0', 10);
    const prefix = el.dataset.prefix || '', suffix = el.dataset.suffix || '';
    const fmt = v => (v < 0 ? '-' : '') + prefix + Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: dec, maximumFractionDigits: dec}) + suffix;
    if (FT.reduced || typeof gsap === 'undefined') { el.textContent = fmt(target); return; }
    const o = {v: 0};
    gsap.to(o, {v: target, duration: 1.4, ease: 'power3.out', onUpdate: () => { el.textContent = fmt(o.v); }});
  });
};

/* ---------- sparklines: <svg class="spark" data-spark="[1,2,3]" data-color="#34d399"> ---------- */
FT.sparklines = (root = document) => {
  root.querySelectorAll('svg[data-spark]').forEach((svg, n) => {
    let vals; try { vals = JSON.parse(svg.dataset.spark); } catch (e) { return; }
    if (!vals.length || vals.every(v => !v)) return;
    const W = 160, H = 52, mx = Math.max(...vals), mn = Math.min(...vals), rng = (mx - mn) || 1;
    const pts = vals.map((v, i) => [i / Math.max(vals.length - 1, 1) * W, H - 6 - (v - mn) / rng * (H - 14)]);
    const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    const c = svg.dataset.color || FT.em, gid = 'sg' + n;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.innerHTML = `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c}" stop-opacity=".35"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></linearGradient></defs>
      <path d="${d} L${W} ${H} L0 ${H} Z" fill="url(#${gid})"/>
      <path class="sp-line" d="${d}" fill="none" stroke="${c}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
      <circle cx="${pts[pts.length - 1][0]}" cy="${pts[pts.length - 1][1]}" r="3" fill="${c}"/>`;
    const line = svg.querySelector('.sp-line');
    if (!FT.reduced && line.getTotalLength) {
      const L = line.getTotalLength(); line.style.strokeDasharray = L; line.style.strokeDashoffset = L;
      line.getBoundingClientRect(); line.style.transition = 'stroke-dashoffset 1.6s cubic-bezier(.22,1,.36,1)'; line.style.strokeDashoffset = 0;
    }
  });
};

/* ---------- staggered reveal of top-level content blocks ---------- */
FT.reveal = () => {
  if (FT.reduced) return;
  document.querySelectorAll('#main > *, #main > .row > [class*="col"], .reveal-group > *').forEach((el, i) => {
    if (el.classList.contains('toast-stack')) return;
    el.classList.add('reveal'); el.style.setProperty('--i', Math.min(i, 12));
  });
};

/* ---------- toasts ---------- */
FT.dismissToast = (t) => { t.classList.add('leaving'); setTimeout(() => t.remove(), 380); };
FT.toast = (msg, kind = 'success') => {
  const stack = document.querySelector('.toast-stack');
  const icons = {success: 'bi-check2-circle', danger: 'bi-exclamation-octagon', warning: 'bi-exclamation-triangle', info: 'bi-info-circle'};
  const t = document.createElement('div');
  t.className = `ft-toast ${kind}`; t.setAttribute('role', 'status');
  t.innerHTML = `<span class="t-ic"><i class="bi ${icons[kind] || icons.info}"></i></span><div></div><button class="t-close" aria-label="Dismiss"><i class="bi bi-x-lg"></i></button><span class="t-bar"></span>`;
  t.querySelector('div').textContent = msg;
  stack.appendChild(t); FT.armToast(t);
};
FT.armToast = (t) => {
  t.querySelector('.t-close')?.addEventListener('click', () => FT.dismissToast(t));
  const ms = t.classList.contains('danger') ? 9000 : 5000;
  const bar = t.querySelector('.t-bar'); if (bar) bar.style.animationDuration = ms + 'ms';
  setTimeout(() => t.isConnected && FT.dismissToast(t), ms);
};

/* ---------- WebGL capability ---------- */
FT.webgl = (() => {
  try { const c = document.createElement('canvas'); return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl'))); }
  catch (e) { return false; }
})();

/** 3D/2D view switch: container [data-view] with .view-3d / .view-2d children and buttons [data-set-view]. */
FT.viewSwitch = (container, on3d, on2d) => {
  const key = 'ft-view-' + container.id;
  let pref = null; try { pref = localStorage.getItem(key); } catch (e) {}
  const can3d = FT.webgl;
  // default: 3D, except reduced-motion users and wide 3D charts on phones (data-mobile-2d); a saved choice always wins
  const phone2d = container.hasAttribute('data-mobile-2d') && window.innerWidth < 576;
  let mode = !can3d ? '2d' : (pref || (FT.reduced || phone2d ? '2d' : '3d'));
  const btns = container.querySelectorAll('[data-set-view]');
  const apply = (m) => {
    mode = m;
    container.classList.toggle('show-2d', m === '2d');
    btns.forEach(b => { b.classList.toggle('active', b.dataset.setView === m); b.setAttribute('aria-pressed', b.dataset.setView === m); });
    (m === '3d' ? on3d : on2d)();
    // the 2D chart may have been drawn while hidden: let Plotly re-measure
    if (m === '2d' && typeof Plotly !== 'undefined') container.querySelectorAll('.js-plotly-plot').forEach(p => Plotly.Plots.resize(p));
  };
  btns.forEach(b => {
    if (b.dataset.setView === '3d' && !can3d) { b.disabled = true; b.title = 'WebGL is not available in this browser'; }
    // only an explicit user choice is remembered (an automatic fallback never overwrites it)
    b.addEventListener('click', () => { try { localStorage.setItem(key, b.dataset.setView); } catch (e) {} apply(b.dataset.setView); });
  });
  apply(mode);
};

/* ---------- boot ---------- */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.ft-toast').forEach(FT.armToast);
  FT.reveal();
  FT.countUp();
  FT.sparklines();
  // "/" focuses the search box
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      const s = document.getElementById('globalSearch'); if (s) { e.preventDefault(); s.focus(); }
    }
  });
});

// Confirmation for destructive forms: <form data-confirm="message">
document.addEventListener('submit', (e) => {
  const msg = e.target.getAttribute('data-confirm');
  if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
  const btn = e.target.querySelector('button[type=submit], button:not([type])');
  if (btn && !btn.dataset.noSpin && e.target.method === 'post') {
    btn.style.width = btn.offsetWidth + 'px';
    setTimeout(() => { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>'; }, 0);
  }
});
