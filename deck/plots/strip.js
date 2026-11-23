// strip: rows of points on one shared numeric axis, with optional clouds of small dots under each row.
// data: { axis: {min, max, step, label},
//         series: [{id, legend, color, opacity?}]            order = legend order = reveal order,
//         rows:   [{id, label,
//                   points: [{series, value, tag, tip, fixed?}],     fixed: label never moved to avoid others
//                   clouds: [{series, values, offset, jitter, r, opacity, tip}]}] }
// opts: width (px, 1408), height (px, 350), reveal (series: one series per click after the plot's step),
//   highlight (a row id, usually $state: that row gets a band), legend (yes/no)
DeckPlots.strip = {
  draw(host, D, o, ctx, fig) {
    if (!D || !D.rows) return ctx.fail(host, 'strip: data needs axis, series and rows');
    const W = +(o.width || 1408), H = +(o.height || 350), L = 150, R = 30, T = 58, B = 44, A = D.axis;
    const X = v => L + (v - A.min) / (A.max - A.min) * (W - L - R), band = (H - T - B) / D.rows.length;
    const col = s => ctx.color(s.color), ser = Object.fromEntries(D.series.map(s => [s.id, s]));
    if (o.legend !== 'no') {
      const lg = document.createElement('div'); lg.className = 'legend'; host.appendChild(lg);
      lg.innerHTML = D.series.map(s => `<span><i style="background:${col(s)};opacity:${s.opacity ?? 1}"></i>${s.legend}</span>`).join('');
    }
    const svg = ctx.el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, host);
    D.rows.forEach((r, k) => {
      if (o.highlight != null) ctx.el('rect', { x: L - 140, y: T + band * k + 2, width: W - L + 130 - R, height: band - 4, rx: 6,
        fill: '#eef2f9', 'fill-opacity': r.id === o.highlight ? 1 : 0 }, svg);
    });
    for (let v = A.min; v <= A.max + 1e-9; v += A.step) {
      ctx.el('line', { x1: X(v), x2: X(v), y1: T, y2: H - B, stroke: ctx.css('--rule'), 'stroke-width': 1 }, svg);
      ctx.el('text', { x: X(v), y: H - B + 24, 'text-anchor': 'middle', 'font-size': 16, fill: ctx.css('--ink-3') }, svg).textContent = v.toFixed(2);
    }
    if (A.label) ctx.el('text', { x: W - R, y: H - 2, 'text-anchor': 'end', 'font-size': 16, fill: ctx.css('--ink-3') }, svg).textContent = A.label;
    // one group per series, in series order; with reveal=series each group is one more click
    const base = +(fig.dataset.step || 1), gs = {};
    D.series.forEach((s, k) => {
      gs[s.id] = ctx.el('g', o.reveal === 'series' ? { 'data-step': base + k + 1, 'data-anim': 'fade' } : {}, svg);
    });
    let seed = 7; const jit = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280 - .5; };
    const hover = (node, html) => { node.addEventListener('mousemove', ev => ctx.tip(ev, html)); node.addEventListener('mouseleave', ctx.untip); };
    D.rows.forEach((r, k) => {
      const yc = T + band * (k + .5);
      ctx.el('text', { x: L - 24, y: yc + 7, 'text-anchor': 'end', 'font-size': 20, fill: ctx.css('--ink'), 'font-weight': 500 }, svg).textContent = r.label;
      (r.clouds || []).forEach(c => c.values.forEach(v => {
        const s = ser[c.series];
        const dot = ctx.el('circle', { cx: X(v), cy: yc + c.offset + jit() * c.jitter, r: c.r, fill: col(s), 'fill-opacity': c.opacity }, gs[c.series]);
        hover(dot, `${c.tip} · ${v.toFixed(3)}`);
      }));
      let lastR = -1e9, lvl = 0;
      [...r.points].sort((a, b) => a.value - b.value).forEach(p => {
        const g = gs[p.series], s = ser[p.series], cx = X(p.value), cy = yc - 4;
        ctx.el('line', { x1: cx, x2: cx, y1: cy - 15, y2: cy + 15, stroke: col(s), 'stroke-width': 3, 'stroke-linecap': 'round' }, g);
        hover(ctx.el('circle', { cx, cy, r: 8, fill: col(s), stroke: ctx.css('--paper'), 'stroke-width': 2 }, g), p.tip);
        const hw = p.tag.length * 4.6;
        let tx = cx, anchor = 'middle';
        if (!p.fixed) {                     // place labels left to right; nudge right, else stack upwards
          if (cx - hw >= lastR + 4) lvl = 0;
          else if (lastR + 6 <= cx) { lvl = 0; tx = lastR + 6; anchor = 'start'; }
          else lvl++;
          lastR = anchor === 'start' ? tx + 2 * hw : cx + hw;
        }
        ctx.el('text', { x: tx, y: cy - 22 - (p.fixed ? 0 : lvl * 16), 'text-anchor': anchor, 'font-size': 15, fill: ctx.css('--ink-2') }, g).textContent = p.tag;
      });
    });
  },
};
