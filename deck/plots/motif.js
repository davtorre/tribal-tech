// motif: a small decorative matrix of tiles. data: (number | null)[][]: a number is the tile's opacity (0–1),
// null is an "unknown" tile (dashed, with a ? unless q=no).  opts: size (px, 34), gap (px, 4), radius (px, 4),
// color (CSS colour or --var, default --accent), q (yes/no)
DeckPlots.motif = {
  draw(host, M, o, ctx) {
    if (!Array.isArray(M) || !M.length) return ctx.fail(host, 'motif: data is not a matrix');
    const size = +(o.size || 34), g = document.createElement('div');
    g.className = 'motif';
    g.style.cssText = `grid-template-columns:repeat(${M[0].length},${size}px);gap:${o.gap ?? 4}px`;
    const color = ctx.color(o.color || '--accent'), q = (o.q || (o.size ? 'no' : 'yes')) === 'yes';
    M.forEach(row => row.forEach(v => {
      const i = document.createElement('i');
      i.style.cssText = `width:${size}px;height:${size}px;border-radius:${o.radius ?? (o.size ? 3 : 4)}px`;
      if (v == null) { i.className = 'q'; if (q) i.textContent = '?'; }
      else { i.style.background = color; i.style.opacity = v; }
      g.appendChild(i);
    }));
    host.appendChild(g);
  },
};
