// heatmap: a matrix drawn as square cells (SVG), with a hover tooltip.
// data: number[][]   opts: width (px, default 400), gamma (1), vmax (number | 'max' | 'q98' style quantile),
//   rowlabels / collabels (string[]; tooltips, and drawn on the left with labels=yes), labelw (px, 100),
//   values (yes: print the numbers), digits (2), gap (px between cells, 2), blocks (grid line every n cells),
//   vlabel (tooltip word for the value, 'rate'), veil (step at which a "hidden" overlay appears), veiltext
DeckPlots.heatmap = {
  draw(host, M, o, ctx) {
    if (!Array.isArray(M) || !M.length) return ctx.fail(host, 'heatmap: data is not a matrix');
    const n = M.length, nc = M[0].length, flat = M.flat().filter(Number.isFinite);
    const lab = o.labels === 'yes' ? +(o.labelw || 100) : 0;
    const width = +(o.width || 400), cell = width / nc, height = cell * n, gap = o.gap != null ? +o.gap : 2;
    let vmax = o.vmax;
    if (vmax == null || vmax === 'max') vmax = Math.max(...flat);
    else if (/^q\d+$/.test(vmax)) { const s = [...flat].sort((a, b) => a - b); vmax = s[Math.floor(+vmax.slice(1) / 100 * (s.length - 1))]; }
    vmax = +vmax || 1;
    const gamma = +(o.gamma || 1), digits = +(o.digits || 2);
    const rl = o.rowlabels || [], cl = o.collabels || o.rowlabels || [];
    const box = document.createElement('div'); box.className = 'heat-box'; host.appendChild(box);
    const svg = ctx.el('svg', { width: width + lab, height, viewBox: `0 0 ${width + lab} ${height}` }, box);
    if (!gap) ctx.el('rect', { x: lab, y: 0, width, height, fill: ctx.css('--card') }, svg);
    M.forEach((row, i) => row.forEach((v, j) => {
      const t = Math.pow(Math.max(0, v) / vmax, gamma);
      ctx.el('rect', { x: lab + j * cell + gap / 2, y: i * cell + gap / 2, width: Math.max(cell - gap, .5), height: Math.max(cell - gap, .5),
        rx: gap ? Math.min(4, cell / 6) : 0, fill: ctx.ramp(t) }, svg);
      if (o.values === 'yes') ctx.el('text', { x: lab + (j + .5) * cell, y: (i + .5) * cell + 6, 'text-anchor': 'middle', 'font-size': 16,
        fill: t > .55 ? '#fff' : ctx.css('--ink'), 'pointer-events': 'none' }, svg).textContent = (+v).toFixed(digits);
    }));
    if (o.blocks) {
      const b = +o.blocks, line = (x1, y1, x2, y2) => ctx.el('line', { x1, y1, x2, y2, stroke: 'rgba(21,23,29,.18)', 'stroke-width': 1 }, svg);
      for (let k = b; k < n; k += b) line(lab, k * cell + .5, lab + width, k * cell + .5);
      if (nc === n) for (let k = b; k < nc; k += b) line(lab + k * cell + .5, 0, lab + k * cell + .5, height);
    }
    if (lab) rl.forEach((l, i) => ctx.el('text', { x: lab - 10, y: (i + .5) * cell + 6, 'text-anchor': 'end', 'font-size': 16, fill: ctx.css('--ink-2') }, svg).textContent = l);
    svg.addEventListener('mousemove', ev => {
      const r = svg.getBoundingClientRect(), sx = r.width / (width + lab), sy = r.height / height;
      const j = Math.floor(((ev.clientX - r.left) / sx - lab) / cell), i = Math.floor((ev.clientY - r.top) / sy / cell);
      if (i < 0 || j < 0 || i >= n || j >= nc) return ctx.untip();
      ctx.tip(ev, `${rl[i] ?? i + 1} → ${cl[j] ?? j + 1}<br>${o.vlabel || 'rate'} ${(+M[i][j]).toFixed(3)}`);
    });
    svg.addEventListener('mouseleave', ctx.untip);
    if (o.veil) { const v = document.createElement('div'); v.className = 'veil'; v.textContent = o.veiltext || 'hidden';
      v.dataset.step = o.veil; v.dataset.anim = 'fade'; box.appendChild(v); }
  },
};
