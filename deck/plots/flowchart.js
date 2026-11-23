// flowchart: a JSON Canvas (Obsidian .canvas) drawn as SVG: text nodes as boxes, edges as arrows with labels.
// data: { nodes: [{id, x, y, width, height, text, color?}], edges: [{fromNode, toNode, fromSide?, toSide?, label?}] }
//   i.e. the .canvas file as it is, or a part of it picked by the data script. color: "1"–"6" (Obsidian presets) or #hex.
//   Text: **bold**, `code`, [[file|alias]] (shown as the alias, underlined), line breaks.
// opts: width (px; default: canvas units × scale), scale (0.7), font (px in canvas units, 22),
//   highlight (node id, several joined with +): a thick ring in --warn
DeckPlots.flowchart = {
  draw(host, C, o, ctx) {
    if (!C || !Array.isArray(C.nodes)) return ctx.fail(host, 'flowchart: data is not a canvas ({nodes, edges})');
    const pad = 70, N = Object.fromEntries(C.nodes.map(n => [n.id, n]));
    const x0 = Math.min(...C.nodes.map(n => n.x)) - pad, y0 = Math.min(...C.nodes.map(n => n.y)) - pad;
    const W = Math.max(...C.nodes.map(n => n.x + n.width)) - x0 + pad, H = Math.max(...C.nodes.map(n => n.y + n.height)) - y0 + pad;
    const scale = o.width ? +o.width / W : +(o.scale || 0.7), font = +(o.font || 22);
    const hl = new Set(String(o.highlight || '').split('+').filter(Boolean));
    // Obsidian's six presets, as a fill tint and a stroke
    const P = { 1: '#e5484d', 2: '#ef8a3c', 3: '#d9a400', 4: '#2f9e6b', 5: '#1f9bb8', 6: '#8e5bd0' };
    const ink = ctx.css('--ink') || '#15171d', ink2 = ctx.css('--ink-3') || '#868b97', card = ctx.css('--card') || '#fff';
    const warn = ctx.css('--warn') || '#eda100';
    const svg = ctx.el('svg', { width: W * scale, height: H * scale, viewBox: `${x0} ${y0} ${W} ${H}`, class: 'flowchart' }, host);
    const defs = ctx.el('defs', {}, svg), mk = ctx.el('marker', { id: `fc-arrow-${host.closest('figure')?.id || 'x'}`,
      viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
    ctx.el('path', { d: 'M0,0 L10,5 L0,10 z', fill: ink2 }, mk);

    const side = (n, s) => ({ top: [n.x + n.width / 2, n.y, 0, -1], bottom: [n.x + n.width / 2, n.y + n.height, 0, 1],
      left: [n.x, n.y + n.height / 2, -1, 0], right: [n.x + n.width, n.y + n.height / 2, 1, 0] })[s];
    const guess = (a, b) => {                       // sides when the canvas does not say: the facing pair
      const dx = b.x + b.width / 2 - (a.x + a.width / 2), dy = b.y + b.height / 2 - (a.y + a.height / 2);
      return Math.abs(dy) >= Math.abs(dx) ? (dy > 0 ? ['bottom', 'top'] : ['top', 'bottom']) : (dx > 0 ? ['right', 'left'] : ['left', 'right']);
    };
    const edges = ctx.el('g', {}, svg), boxes = ctx.el('g', {}, svg), labels = ctx.el('g', {}, svg);
    for (const e of C.edges || []) {
      const a = N[e.fromNode], b = N[e.toNode];
      if (!a || !b) continue;
      const [fs, ts] = e.fromSide && e.toSide ? [e.fromSide, e.toSide] : guess(a, b);
      const [x1, y1, nx1, ny1] = side(a, fs), [x2, y2, nx2, ny2] = side(b, ts);
      const k = Math.max(40, Math.min(160, Math.hypot(x2 - x1, y2 - y1) / 2.5));
      ctx.el('path', { d: `M${x1},${y1} C${x1 + nx1 * k},${y1 + ny1 * k} ${x2 + nx2 * k},${y2 + ny2 * k} ${x2},${y2}`,
        fill: 'none', stroke: ink2, 'stroke-width': 2.5, 'marker-end': `url(#${mk.id})` }, edges);
      if (e.label) {                                // at the curve's midpoint
        const mx = (x1 + x2) / 8 + 3 / 8 * (x1 + nx1 * k + x2 + nx2 * k), my = (y1 + y2) / 8 + 3 / 8 * (y1 + ny1 * k + y2 + ny2 * k);
        const t = ctx.el('text', { x: mx, y: my + font * 0.3, 'text-anchor': 'middle', 'font-size': font * 0.8,
          'font-family': 'var(--mono)', fill: ink2, stroke: card, 'stroke-width': 8, 'paint-order': 'stroke' }, labels);
        t.textContent = e.label;
      }
    }
    const html = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/\[\[(?:[^\]|]*\|)?([^\]]+)\]\]/g, '<u>$1</u>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\n/g, '<br>');
    for (const n of C.nodes) {
      const c = P[n.color] || (n.color?.startsWith('#') ? n.color : null), on = hl.has(n.id);
      const g = ctx.el('g', { 'data-node': n.id }, boxes);
      ctx.el('rect', { x: n.x, y: n.y, width: n.width, height: n.height, rx: 12,
        fill: c ? c + '1f' : card, stroke: on ? warn : (c || ink2), 'stroke-width': on ? 7 : 2 }, g);
      const fo = ctx.el('foreignObject', { x: n.x, y: n.y, width: n.width, height: n.height }, g);
      const d = document.createElement('div');
      d.className = 'fc-text';
      const list = /(^|\n)- /.test(n.text || '');     // a bulleted card (e.g. the tests) reads better left-aligned
      d.style.cssText = `font:400 ${font}px/1.25 var(--body);color:${ink};height:100%;display:flex;align-items:center;` +
        `justify-content:center;text-align:${list ? 'left' : 'center'};padding:4px 12px;box-sizing:border-box`;
      d.innerHTML = `<div>${html(n.text || n.file || n.id)}</div>`;
      fo.appendChild(d);
      g.addEventListener('mousemove', ev => ctx.tip(ev, `<code>${n.id}</code>`));
      g.addEventListener('mouseleave', () => ctx.untip());
    }
  },
};
