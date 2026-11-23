// Deck runtime shared by every template: stage scaling, step reveals, keyboard/click navigation, deep links,
// plus data, state (toggles), live values and plots.
// An element with data-step="k" is visible once the slide's step counter reaches k. The counter starts at 1
// on entering a slide, so step 1 is "visible on entry" and step k needs k-1 clicks.
// Deep links: #<slideId> or #<slideId>.<step>
(() => {
const $ = s => document.querySelector(s);
const stage = $('#stage');
const fit = () => { stage.style.transform = `scale(${Math.min(innerWidth / 1600, innerHeight / 900)})`; };
addEventListener('resize', fit); fit();

/* ---------- data and state ---------- */
const json = id => { const e = document.getElementById(id); return e ? JSON.parse(e.textContent) : {}; };
const DATA = json('deck-data'), STATE = json('deck-state');
const sub = p => p.replace(/\$(\w+)/g, (m, v) => STATE[v] ?? m);
const get = p => DATA[sub(p)];

// Python format-spec subset for live values: "", ".2f", ",d", ",.1f", ".1%", ".3e", ".3g"
function pyfmt(v, spec) {
  if (typeof v !== 'number') return v == null ? '⚠' : String(v);
  const m = /^(,)?(?:\.(\d+))?([fde%g]|tex)?$/.exec(spec || '');
  if (!m) return String(v);
  const [, comma, p, t] = m, d = p == null ? undefined : +p;
  const group = s => comma ? s.replace(/^(-?\d+)/, x => (+x).toLocaleString('en-US')) : s;
  if (t === 'd') return group(String(Math.round(v)));
  if (t === 'f') return group(v.toFixed(d ?? 6));
  if (t === '%') return group((100 * v).toFixed(d ?? 6)) + '%';
  if (t === 'e' || t === 'tex') return v.toExponential(d ?? 6);
  return String(+v.toPrecision(d || 6));
}

/* ---------- helpers handed to plot kinds ---------- */
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const tipEl = $('#tip');
const RAMP = ['#f2f6fc', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
const ctx = {
  css, get, state: STATE, fmt: pyfmt,
  color: c => typeof c === 'string' && c.startsWith('--') ? css(c) : c,
  resolveVars: x => JSON.parse(JSON.stringify(x), (k, v) => typeof v === 'string' && v.startsWith('var(--') ? css(v.slice(4, -1)) : v),
  el(tag, attrs = {}, parent) {
    const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  },
  ramp(t) {
    t = Math.max(0, Math.min(1, t)); const x = t * (RAMP.length - 1), i = Math.floor(x), f = x - i;
    const a = RAMP[i], b = RAMP[Math.min(i + 1, RAMP.length - 1)], p = (h, k) => parseInt(h.slice(1 + 2 * k, 3 + 2 * k), 16);
    return `rgb(${[0, 1, 2].map(k => Math.round(p(a, k) + f * (p(b, k) - p(a, k)))).join(',')})`;
  },
  tip(ev, html) { tipEl.innerHTML = html; tipEl.style.display = 'block'; tipEl.style.left = ev.clientX + 14 + 'px'; tipEl.style.top = ev.clientY + 14 + 'px'; },
  untip() { tipEl.style.display = 'none'; },
  fail(host, msg) { host.innerHTML = `<p class="small plot-fail">${msg}</p>`; },
};

/* ---------- plots ---------- */
const Plots = window.DeckPlots || {};
const figs = [...document.querySelectorAll('figure.plot[data-spec]')];
const spec = f => f._spec || (f._spec = JSON.parse(f.dataset.spec));
const kind = f => Plots[spec(f).kind];
const optsOf = f => { const s = spec(f), o = {};
  for (const k in s.opts) o[k] = s.paths.includes(k) ? get(s.opts[k]) : sub(s.opts[k]);
  return o; };
function draw(f) {
  const K = kind(f), host = f.querySelector('.plot-host');
  if (!K) return ctx.fail(host, `no plot kind "${spec(f).kind}"`);
  if (!K.lazy) host.innerHTML = '';
  try { K.draw(host, spec(f).from ? get(spec(f).from) : null, optsOf(f), ctx, f); f._drawn = true; }
  catch (e) { ctx.fail(host, `${spec(f).kind}: ${e.message}`); console.error(e); }
}
const visible = f => f.closest('.slide') === slides[cur];
function bindValues() {
  document.querySelectorAll('[data-bind]').forEach(e => { e.textContent = pyfmt(get(e.dataset.bind), e.dataset.fmt); });
}
function setState(v, value) {
  STATE[v] = value;
  document.querySelectorAll(`.toggle[data-var="${v}"] button`).forEach(b => b.setAttribute('aria-pressed', b.dataset.value === value));
  figs.filter(f => f.dataset.spec.includes('$' + v)).forEach(f => {
    if (kind(f)?.lazy && !visible(f)) f._drawn = false; else draw(f);
  });
  bindValues(); render();
}
document.querySelectorAll('.toggle button').forEach(b => b.addEventListener('click', e => {
  e.stopPropagation(); setState(b.closest('.toggle').dataset.var, b.dataset.value);
}));

/* ---------- navigation ---------- */
const slides = [...document.querySelectorAll('.slide')];
const maxStep = sl => Math.max(1, ...[...sl.querySelectorAll('[data-step]')].map(e => +e.dataset.step));
let cur = 0, step = 1, shown = -1;

// embedded apps start when their slide first opens (base64 so their own <script> tags survive)
const loadEmbeds = root => root.querySelectorAll('.embed iframe[data-pending]').forEach(f => {
  const b64 = f.parentElement.querySelector('.embed-src').textContent.trim();
  f.srcdoc = new TextDecoder().decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
  f.removeAttribute('data-pending');
});
function enterLeave() {
  if (shown === cur) return;
  loadEmbeds(slides[cur]);
  if (shown >= 0) figs.filter(f => f.closest('.slide') === slides[shown]).forEach(f => kind(f)?.leave?.(f.querySelector('.plot-host'), optsOf(f), ctx));
  shown = cur;
  figs.filter(visible).forEach(f => {
    if (!f._drawn) draw(f);
    kind(f)?.enter?.(f.querySelector('.plot-host'), optsOf(f), ctx);
  });
}
function render() {
  slides.forEach((s, i) => s.classList.toggle('on', i === cur));
  enterLeave();
  slides[cur].querySelectorAll('[data-step]').forEach(e => e.classList.toggle('hide', +e.dataset.step > step));
  $('#counter').textContent = `${String(cur + 1).padStart(2, '0')} / ${slides.length} · ${slides[cur].dataset.title || slides[cur].id}`;
  const secs = JSON.parse(stage.dataset.sections || '[]'), sn = slides[cur].dataset.section;
  $('#sections').innerHTML = sn ? secs.map(x => x === sn ? `<b>${x}</b>` : x).join(' &nbsp;·&nbsp; ') : '';
  $('#progress').style.width = `${100 * (cur + 1) / slides.length}%`;
  try { history.replaceState(null, '', `#${slides[cur].id}${step > 1 ? '.' + step : ''}`); } catch (e) {}
}
function go(dir) {
  if (dir > 0) { if (step < maxStep(slides[cur])) step++; else if (cur < slides.length - 1) { cur++; step = 1; } }
  else { if (step > 1) step--; else if (cur > 0) { cur--; step = maxStep(slides[cur]); } }
  render();
}
const toggleFull = () => { try { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {}); } catch (e) {} };
addEventListener('keydown', e => {
  if ([' ', 'Enter'].includes(e.key) && e.target.closest('button')) return;
  if (['ArrowRight', 'PageDown', ' ', 'Enter'].includes(e.key)) { e.preventDefault(); go(1); }
  else if (['ArrowLeft', 'PageUp', 'Backspace'].includes(e.key)) { e.preventDefault(); go(-1); }
  else if (e.key === 'Home') { cur = 0; step = 1; render(); }
  else if (e.key === 'End') { cur = slides.length - 1; step = maxStep(slides[cur]); render(); }
  else if (e.key === 'f') toggleFull();
});
$('#nav-prev').onclick = () => go(-1);
$('#nav-next').onclick = () => go(1);
$('#nav-full').onclick = toggleFull;
// clicks advance the deck, except on controls and on plots you can drag
stage.addEventListener('click', e => { if (!e.target.closest('button, a, .toggle, .plot3d, .embed')) go(1); });

// ?state=wave:wave1,view:null sets toggles before anything is drawn (used by export, handy for links)
const Q = new URLSearchParams(location.search);
(Q.get('state') || '').split(',').filter(Boolean).forEach(kv => {
  const [k, v] = kv.split(/[:=]/);
  STATE[k] = v;
  document.querySelectorAll(`.toggle[data-var="${k}"] button`).forEach(b => b.setAttribute('aria-pressed', b.dataset.value === v));
});

// draw everything that does not need a visible box (their sub-steps must exist before the first render)
figs.forEach(f => { if (!kind(f)?.lazy) draw(f); });
bindValues();

if (Q.get('only')) exportMode(Q.get('only'));
else if (!slides.length) {                      // a file with only #Figure elements: show them as a sheet
  document.documentElement.classList.add('sheet');
  loadEmbeds(document);
  figs.filter(f => kind(f)?.lazy).forEach(draw);
} else {
  const m = decodeURIComponent(location.hash.slice(1)).match(/^(.+?)(?:\.(\d+))?$/);
  if (m) { const i = slides.findIndex(s => s.id === m[1]); if (i >= 0) { cur = i; step = Math.min(+m[2] || 1, maxStep(slides[i])); } }
  render();
}

/* ---------- export mode: ?only=<element ID>[&step=n][&state=..][&bg=paper|white|transparent][&pad=px][&print=1][&svg=1]
   Shows that one element at its real size and writes its size (and SVG) into the page for the exporter. ---------- */
async function exportMode(id) {
  const root = document.documentElement, meta = (k, v) => {
    const e = document.createElement('meta'); e.id = 'tt-' + k; e.content = v; document.head.appendChild(e); };
  root.classList.add('export');
  ctx.exporting = true;
  const bg = Q.get('bg') || 'paper';
  if (bg !== 'paper') root.style.setProperty('--export-bg', bg === 'transparent' ? 'transparent' : bg);
  const el = document.getElementById(id);
  if (!el) return meta('error', `no element with ID "${id}"`);
  let target;
  if (el.classList.contains('slide')) {           // a whole slide: the 1600x900 stage, unscaled, no controls
    cur = slides.indexOf(el); step = +Q.get('step') || maxStep(el);
    removeEventListener('resize', fit); render(); target = stage;
  } else {                                        // any other element: lift it out at the width it has in place
    const host = el.closest('.slide, .figure');
    const isFig = el.classList.contains('figure');
    if (host && !isFig) host.classList.add('on');
    if (isFig || host?.classList.contains('figure')) $('#figures').style.display = 'block';   // measurable
    const w = el.offsetWidth;
    const wrap = document.createElement('div'); wrap.id = 'only';
    if (host && !isFig) {                         // keep the slide's classes and CSS variables (roomy, --grid-gap, ...)
      wrap.className = host.className.replace(/\b(slide|figure|on|title-slide)\b/g, '').trim();
      const st = host.getAttribute('style'); if (st) wrap.setAttribute('style', st);
    }
    wrap.style.padding = (Q.get('pad') ?? (isFig ? 0 : 24)) + 'px';
    el.style.width = w + 'px'; el.style.margin = '0';
    wrap.appendChild(el); document.body.appendChild(wrap);
    $('#figures').style.display = ''; $('#viewport').style.display = 'none';
    if (host && !isFig) host.classList.remove('on');
    const s = +Q.get('step');
    el.querySelectorAll('[data-step]').forEach(e => e.classList.toggle('hide', s ? +e.dataset.step > s : false));
    el.classList.remove('hide');
    figs.filter(f => el.contains(f)).forEach(f => { if (kind(f)?.lazy || !f._drawn) draw(f); });
    target = wrap;
  }
  await document.fonts.ready;
  if (window.MathJax?.startup?.promise) await MathJax.startup.promise;
  await new Promise(r => setTimeout(r, 400));
  if (Q.get('print') && window.Plotly) {          // WebGL does not survive printing: swap 3D plots for an image
    for (const h of target.querySelectorAll('.plot3d')) {
      const url = await Plotly.toImage(h, { format: 'png', width: h.clientWidth, height: h.clientHeight, scale: 2 });
      h.innerHTML = `<img src="${url}" style="display:block;width:100%;height:100%">`;
    }
  }
  const r = target.getBoundingClientRect(), W = Math.ceil(r.width), H = Math.ceil(r.height);
  const page = document.createElement('style');
  page.textContent = `@page { size: ${W}px ${H}px; margin: 0 } html, body { width: ${W}px; height: ${H}px; margin: 0; overflow: hidden }
    * { -webkit-print-color-adjust: exact; print-color-adjust: exact }`;
  document.head.appendChild(page);
  if (Q.get('svg')) {                             // the element's one SVG plot, with fonts written into it
    const svgs = target.querySelectorAll('.plot-host svg');
    if (svgs.length !== 1) meta('error', `SVG export needs exactly one SVG plot in the element (found ${svgs.length})`);
    else {
      const src = svgs[0], out = src.cloneNode(true), a = src.querySelectorAll('text'), b = out.querySelectorAll('text');
      a.forEach((t, i) => { const cs = getComputedStyle(t);
        b[i].setAttribute('font-family', cs.fontFamily); b[i].setAttribute('font-weight', cs.fontWeight);
        if (!t.getAttribute('font-size')) b[i].setAttribute('font-size', cs.fontSize); });
      out.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      if (target.querySelector('.legend, figcaption')) meta('note', 'the SVG holds the plot only; its legend and caption are HTML and are left out');
      const ta = document.createElement('textarea'); ta.id = 'tt-svg'; ta.textContent = out.outerHTML; document.body.appendChild(ta);
      ta.style.display = 'none';
    }
  }
  meta('size', `${W} ${H}`);
}
})();
