// lines: x-y line series on shared axes (e.g. loss vs iteration), optional log axes, hover readout.
// data: { x: {label, log?, min?, max?}, y: {label, log?, min?, max?, fmt?},
//         series: [{id, legend, color, x: number[], y: number[], dash?, width?}],   order = legend order = reveal order
//         refs?:  [{y, label, color?}] }                                             horizontal reference lines
// opts: width (px, 640), height (px, 400), reveal (series: one series per click after the plot's step), legend (yes/no)
DeckPlots.lines = {
  draw(host, D, o, ctx, fig) {
    if (!D || !D.series) return ctx.fail(host, 'lines: data needs x, y and series');
    const W = +(o.width || 640), H = +(o.height || 400), L = 78, R = 18, T = 14, B = 58;
    const col = s => ctx.color(s.color);
    const all = k => D.series.flatMap(s => s[k]).concat(k === 'y' ? (D.refs || []).map(r => r.y) : []).filter(v => Number.isFinite(v) && (!D[k].log || v > 0));
    const span = k => {
      const a = D[k], v = all(k);
      let lo = a.min ?? Math.min(...v), hi = a.max ?? Math.max(...v);
      if (a.log) { lo = 10 ** Math.floor(Math.log10(lo) + 1e-9); hi = 10 ** Math.ceil(Math.log10(hi) - 1e-9); }
      else if (a.min == null || a.max == null) { const p = (hi - lo) * .06 || 1; lo = a.min ?? lo - p; hi = a.max ?? hi + p; }
      return [lo, hi];
    };
    const sc = (k, a, b) => { const [lo, hi] = span(k), f = D[k].log ? Math.log10 : (v => v);
      return v => a + (f(v) - f(lo)) / (f(hi) - f(lo)) * (b - a); };
    const X = sc('x', L, W - R), Y = sc('y', H - B, T);
    const ticks = k => { const [lo, hi] = span(k);
      if (D[k].log) { const t = []; for (let e = Math.round(Math.log10(lo)); e <= Math.round(Math.log10(hi)); e++) t.push(10 ** e); return t; }
      const st = 10 ** Math.floor(Math.log10((hi - lo) / 4)), m = [1, 2, 2.5, 5, 10].find(m => (hi - lo) / (m * st) <= 6) * st;
      const t = []; for (let v = Math.ceil(lo / m) * m; v <= hi + 1e-12; v += m) t.push(+v.toPrecision(12)); return t; };
    const lab = (k, v) => D[k].log ? (v >= 1e3 ? `10${String(Math.round(Math.log10(v))).replace(/\d/g, d => '⁰¹²³⁴⁵⁶⁷⁸⁹'[d])}` : String(v))
      : D[k].fmt ? ctx.fmt(v, D[k].fmt) : String(v);
    if (o.legend !== 'no') {
      const lg = document.createElement('div'); lg.className = 'legend'; host.appendChild(lg);
      lg.innerHTML = D.series.map(s => `<span><i style="background:${col(s)}"></i>${s.legend}</span>`).join('');
    }
    const svg = ctx.el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, host);
    const txt = (x, y, s, a = {}) => { ctx.el('text', { x, y, 'font-size': 15, fill: ctx.css('--ink-3'), ...a }, svg).textContent = s; };
    ticks('x').forEach(v => { ctx.el('line', { x1: X(v), x2: X(v), y1: T, y2: H - B, stroke: ctx.css('--rule') }, svg);
      txt(X(v), H - B + 22, lab('x', v), { 'text-anchor': 'middle' }); });
    ticks('y').forEach(v => { ctx.el('line', { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: ctx.css('--rule') }, svg);
      txt(L - 8, Y(v) + 5, lab('y', v), { 'text-anchor': 'end' }); });
    if (D.x.label) txt((L + W - R) / 2, H - 8, D.x.label, { 'text-anchor': 'middle' });
    if (D.y.label) txt(16, (T + H - B) / 2, D.y.label, { 'text-anchor': 'middle', transform: `rotate(-90 16 ${(T + H - B) / 2})` });
    (D.refs || []).forEach(r => {
      ctx.el('line', { x1: L, x2: W - R, y1: Y(r.y), y2: Y(r.y), stroke: ctx.color(r.color || '--ink-3'), 'stroke-dasharray': '6 5', 'stroke-width': 1.5 }, svg);
      if (r.label) txt(W - R - 4, Y(r.y) - 6, r.label, { 'text-anchor': 'end' });
    });
    const base = +(fig.dataset.step || 1);
    D.series.forEach((s, k) => {
      const g = ctx.el('g', o.reveal === 'series' ? { 'data-step': base + k + 1, 'data-anim': 'fade' } : {}, svg);
      const pts = s.x.map((x, i) => [x, s.y[i]]).filter(([x, y]) => Number.isFinite(y) && (!D.x.log || x > 0) && (!D.y.log || y > 0));
      ctx.el('path', { d: 'M' + pts.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join('L'), fill: 'none', stroke: col(s),
        'stroke-width': s.width || 2.5, 'stroke-dasharray': s.dash || 'none', 'stroke-linejoin': 'round' }, g);
      const [xe, ye] = pts[pts.length - 1];
      ctx.el('circle', { cx: X(xe), cy: Y(ye), r: 5, fill: col(s) }, g);
      s._pts = pts;
    });
    // hover: nearest point of every series at the cursor's x
    const hit = ctx.el('rect', { x: L, y: T, width: W - L - R, height: H - B - T, fill: 'transparent' }, svg);
    const fy = v => D.y.fmt ? ctx.fmt(v, D.y.fmt) : v.toPrecision(3);
    hit.addEventListener('mousemove', ev => {
      const bx = svg.getBoundingClientRect(), px = (ev.clientX - bx.left) * W / bx.width;
      const rows = D.series.map(s => { const p = s._pts.reduce((b, q) => Math.abs(X(q[0]) - px) < Math.abs(X(b[0]) - px) ? q : b);
        return `<span style="color:${col(s)}">●</span> ${s.legend}: ${fy(p[1])} <span style="opacity:.6">(${p[0].toLocaleString()})</span>`; });
      ctx.tip(ev, rows.join('<br>'));
    });
    hit.addEventListener('mouseleave', ctx.untip);
  },
};
