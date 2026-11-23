// requires: https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js
// scatter3d: a rotatable Plotly 3D scene. Drawn when its slide opens (Plotly needs a visible box).
// data: { layout: {...Plotly layout},
//         modes: { <name>: { traces: [...Plotly traces], notes?: html, legend?: [{label, color}] } } }
//   Any string "var(--x)" in traces/layout is replaced by that CSS variable's value.
// opts: mode (which entry of modes; usually $state), spin (yes: slow turn while the slide is open, stops on drag),
//   aside (#id of an element that receives the mode's notes and legend), height (px, 680)
DeckPlots.scatter3d = {
  lazy: true,
  draw(host, D, o, ctx, fig) {
    if (!window.Plotly) return ctx.fail(host, 'The 3D viewer needs plotly.js, which did not load. Check the network connection.');
    const m = D && D.modes && D.modes[o.mode || Object.keys(D.modes)[0]];
    if (!m) return ctx.fail(host, `scatter3d: no mode "${o.mode}"`);
    if (!host._plot) { host.style.height = (o.height || 680) + 'px'; host.classList.add('plot3d'); }
    Plotly.react(host, ctx.resolveVars(m.traces), ctx.resolveVars(D.layout), { displayModeBar: false, responsive: false });
    if (!host._plot) { host._plot = true; host.on('plotly_relayouting', () => this.stop(host)); }
    const aside = o.aside && document.getElementById(o.aside);
    if (aside) aside.innerHTML = (m.notes ? `<p class="small">${m.notes}</p>` : '') +
      (m.legend ? `<div class="legend">${m.legend.map(l => `<span><i style="background:${ctx.color(l.color)}"></i>${l.label}</span>`).join('')}</div>` : '');
  },
  enter(host, o, ctx) {
    if (ctx?.exporting || o.spin !== 'yes' || host._spin || matchMedia('(prefers-reduced-motion: reduce)').matches || !window.Plotly || !host._plot) return;
    let a = 0.7;
    host._spin = setInterval(() => { a += 0.006; Plotly.relayout(host, { 'scene.camera.eye': { x: 2.1 * Math.cos(a), y: 2.1 * Math.sin(a), z: .8 } }); }, 50);
  },
  leave(host) { this.stop(host); },
  stop(host) { clearInterval(host._spin); host._spin = null; },
};
