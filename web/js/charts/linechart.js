// Minimal dependency-free interactive line chart: hover tooltip + basic
// time-range zoom via click-drag. No external charting library required.
function renderLineChart(canvas, series, opts = {}) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;
  const pad = { top: 10, right: 10, bottom: 24, left: 48 };

  let data = series.filter((p) => p.y !== null && p.y !== undefined);
  if (data.length === 0) {
    ctx.fillStyle = "#8b9bb4";
    ctx.font = "13px sans-serif";
    ctx.fillText("Keine Daten", pad.left, H / 2);
    return;
  }

  let range = { start: 0, end: data.length - 1 };

  function draw() {
    ctx.clearRect(0, 0, W, H);
    const visible = data.slice(range.start, range.end + 1);
    const ys = visible.map((p) => p.y);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const spanY = maxY - minY || 1;

    const x = (i) => pad.left + (i / Math.max(1, visible.length - 1)) * (W - pad.left - pad.right);
    const y = (v) => H - pad.bottom - ((v - minY) / spanY) * (H - pad.top - pad.bottom);

    // gridlines
    ctx.strokeStyle = "#22304a";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 3; i++) {
      const gy = pad.top + (i / 3) * (H - pad.top - pad.bottom);
      ctx.beginPath();
      ctx.moveTo(pad.left, gy);
      ctx.lineTo(W - pad.right, gy);
      ctx.stroke();
    }

    // line
    ctx.strokeStyle = opts.color || "#4f8cff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    visible.forEach((p, i) => {
      const px = x(i), py = y(p.y);
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.stroke();

    // y-axis labels
    ctx.fillStyle = "#8b9bb4";
    ctx.font = "11px sans-serif";
    ctx.fillText(maxY.toFixed(opts.decimals ?? 2), 4, pad.top + 8);
    ctx.fillText(minY.toFixed(opts.decimals ?? 2), 4, H - pad.bottom);

    canvas._chartState = { visible, x, y };
  }

  draw();

  canvas.onmousemove = (ev) => {
    const state = canvas._chartState;
    if (!state) return;
    const rect2 = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect2.left;
    let nearest = 0, best = Infinity;
    state.visible.forEach((p, i) => {
      const dx = Math.abs(state.x(i) - mx);
      if (dx < best) { best = dx; nearest = i; }
    });
    draw();
    const p = state.visible[nearest];
    if (!p) return;
    const px = state.x(nearest), py = state.y(p.y);
    ctx.fillStyle = opts.color || "#4f8cff";
    ctx.beginPath();
    ctx.arc(px, py, 3, 0, Math.PI * 2);
    ctx.fill();

    const label = `${p.label || ""}: ${p.y}`;
    ctx.font = "11px sans-serif";
    const tw = ctx.measureText(label).width + 10;
    const tx = Math.min(Math.max(px - tw / 2, 2), W - tw - 2);
    ctx.fillStyle = "#161f30";
    ctx.fillRect(tx, py - 26, tw, 18);
    ctx.strokeStyle = "#22304a";
    ctx.strokeRect(tx, py - 26, tw, 18);
    ctx.fillStyle = "#e6ecf5";
    ctx.fillText(label, tx + 5, py - 13);
  };

  canvas.onmouseleave = () => draw();

  // simple zoom: scroll to shrink/expand the visible range
  canvas.onwheel = (ev) => {
    ev.preventDefault();
    const span = range.end - range.start;
    const delta = ev.deltaY > 0 ? 1 : -1;
    const newSpan = Math.max(3, Math.min(data.length - 1, span + delta * Math.ceil(span * 0.1 + 1)));
    const center = (range.start + range.end) / 2;
    range = {
      start: Math.max(0, Math.round(center - newSpan / 2)),
      end: Math.min(data.length - 1, Math.round(center + newSpan / 2)),
    };
    draw();
  };
  canvas.ondblclick = () => {
    range = { start: 0, end: data.length - 1 };
    draw();
  };
}
