// Runs the test page's script against a minimal DOM and a stub Leaflet (MapPlan.md 5.2). Usage: node page_harness.js index.html plan.json
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const plan = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join("\n");

const els = {};
const mk = () => {
  const cls = new Set();
  return { innerHTML: "", textContent: "", hidden: false, style: {}, value: "", checked: true, dataset: {}, handlers: {},
    classList: { toggle: (c, on) => (on === undefined ? (cls.has(c) ? cls.delete(c) : cls.add(c)) : on ? cls.add(c) : cls.delete(c)),
                 add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c) },
    addEventListener(t, f) { this.handlers[t] = f; }, scrollIntoView() { this.scrolled = true; } };
};
const rows = {};
const rowsFromHtml = () => {
  const out = [];
  for (const m of els.plan.innerHTML.matchAll(/data-step="(\d+)"/g)) {
    if (!rows[m[1]]) { rows[m[1]] = mk(); rows[m[1]].dataset.step = m[1]; }
    out.push(rows[m[1]]);
  }
  return out;
};
const docHandlers = {};
global.document = {
  addEventListener: (t, f) => { docHandlers[t] = f; },
  getElementById: (id) => (els[id] = els[id] || mk()),
  documentElement: {},
  querySelectorAll: (sel) => (sel.includes("li[data-step]") ? rowsFromHtml() : sel.includes("li.sel") ? Object.values(rows).filter((r) => r.classList.contains("sel")) : []),
  querySelector: (sel) => { const m = sel.match(/data-step="(\d+)"/); return m ? rows[m[1]] : { value: "home" }; },
};
global.getComputedStyle = () => ({ getPropertyValue: () => "" });
global.fetch = async () => ({ ok: true, json: async () => ({ backend: "x", model: "y", origins: [], interests: [], tile_url: "t/{z}", tile_attribution: "a" }) });
global.setInterval = () => 0; global.clearInterval = () => {};

// ---- stub Leaflet: records what is drawn ----
const made = [];
const group = (ls) => ({ layers: ls || [], addLayer(l) { this.layers.push(l); }, clearLayers() { this.layers = []; }, addTo() { return this; }, getBounds: () => bnds() });
const bnds = () => ({ pad() { return this; }, contains: () => false, getCenter: () => [0, 0] });
const layer = (type, extra) => {
  const l = { type, popup: null, handlers: {}, opened: 0, z: 0, opacity: 1, ...extra, bindPopup(p) { this.popup = p; return this; }, on(t, f) { this.handlers[t] = f; return this; },
    openPopup() { this.opened++; }, setZIndexOffset(z) { this.z = z; }, getElement: () => ({ firstChild: mk() }) };
  if (type !== "marker") l.setStyle = function (s) { this.style = { ...(this.style || {}), ...s }; };
  if (type === "polyline") { l.getLatLngs = function () { return this.coords; }; l.bringToFront = function () { this.front = true; }; }
  if (type === "marker") l.setOpacity = function (o) { this.opacity = o; };
  made.push(l); return l;
};
global.L = {
  map: () => ({ attributionControl: { addAttribution(t) { global.attribution = t; } }, removeLayer() {}, removeControl() {}, addLayer() {}, fitBounds() { this.fit = (this.fit || 0) + 1; }, setView() {}, getZoom: () => 12, getBounds: bnds,
                getBoundsZoom: () => 14, invalidateSize() {} }),
  tileLayer: (u) => ({ url: u, addTo() { return this; } }),
  featureGroup: group,
  polyline: (c, o) => layer("polyline", { coords: c, opts: o }),
  circleMarker: (c, o) => layer("circle", { coords: c, opts: o }),
  marker: (c, o) => layer("marker", { coords: c, icon: o.icon }),
  divIcon: (o) => ({ html: o.html }),
  latLngBounds: bnds,
  control: { layers: () => ({ addTo() { return this; } }) },
};

const api = new Function(code + "\n;return { render, select, byStep: () => byStep, views: () => views, clearSelection, allLayers: () => allLayers, casing: () => casing };")();
api.render(plan);
const byStep = api.byStep();
const isDotted = (l) => l.opts && l.opts.dashArray === "1 7";
const isDashed = (l) => l.opts && l.opts.dashArray === "6 6";
const lines = made.filter((l) => l.type === "polyline");
const r = {
  solid: lines.filter((l) => !l.opts.dashArray).length, dashed: lines.filter(isDashed).length, dotted: lines.filter(isDotted).length,
  stops: made.filter((l) => l.type === "circle").length,
  numbered: made.filter((l) => l.type === "marker" && /class="pin( food)?\s*"/.test(l.icon.html)).map((l) => l.icon.html.replace(/<[^>]+>/g, "")),
  origin: made.filter((l) => l.type === "marker" && l.icon.html.includes("pin origin")).length,
  clocks: made.filter((l) => l.type === "marker" && l.icon.html.includes("pin clock")).length,
  ideas: made.filter((l) => l.type === "marker" && l.icon.html.includes("pin idea")).length,
  food: made.filter((l) => l.type === "marker" && l.icon.html.includes("pin food")).length,
  popups: made.every((l) => typeof l.popup === "string" && l.popup.length > 0),
  rows: rowsFromHtml().map((r) => Number(r.dataset.step)),
  stepsWithFeatures: Object.keys(byStep).map(Number),
  legend: els.maplegend.innerHTML, warn: els.mapwarn.innerHTML, hidden: els.mapbox.hidden,
  views: Object.keys(api.views()), attribution: global.attribution,
  ridePopups: [...new Set(made.filter((l) => l.type === "polyline" && l.popup && /^<strong>(bus|rail|ferry)/.test(l.popup)).map((l) => l.popup))],
};
const stepOf = (kind) => Number(Object.keys(byStep).find((k) => byStep[k].some((x) => x.kind === kind)));
const vs = stepOf("visit");
const solidStep = Object.keys(byStep).find((k) => byStep[k].some((x) => x.kind === "ride" && x.layer.opts && !x.layer.opts.dashArray));
const rs = Number(solidStep !== undefined ? solidStep : stepOf("ride"));          // a ride that has a real path, if there is one
const dimmed = () => api.allLayers().filter((x) => (x.layer.style && x.layer.style.opacity !== undefined && x.layer.style.opacity < 0.5) || x.layer.opacity === 0.4).length;
// row -> map
rows[vs].handlers.click();
r.rowClick = { selected: rows[vs].classList.contains("sel"), popupOpened: byStep[vs].some((x) => x.layer.opened > 0), raised: byStep[vs].some((x) => x.layer.z === 1000) };
// map -> row, on a ride: casing under a stronger stroke, the rest dimmed
const rideLayers = byStep[rs].filter((x) => x.kind === "ride");
byStep[rs][0].layer.handlers.click();
r.mapClick = { selected: rows[rs].classList.contains("sel"), scrolled: !!rows[rs].scrolled, previousCleared: !rows[vs].classList.contains("sel") };
const solidRide = rideLayers.find((x) => x.layer.opts && !x.layer.opts.dashArray);
r.highlight = {
  casing: api.casing().layers.length > 0, casingWhite: api.casing().layers.every((c) => c.opts.color === "#ffffff"),
  casingWider: !solidRide || api.casing().layers.some((c) => c.opts.weight > solidRide.layer.style.weight),
  strongerStroke: !solidRide || solidRide.layer.style.weight === solidRide.base.weight + 2,
  onTop: !solidRide || solidRide.layer.front === true, dimmed: dimmed() > 0,
  otherDimmed: api.allLayers().filter((x) => !byStep[rs].some((y) => y.layer === x.layer)).every((x) => (x.layer.style && x.layer.style.opacity < 0.5) || x.layer.opacity === 0.4),
};
// clicking the same thing again, or Escape, clears it
byStep[rs][0].layer.handlers.click();
r.clearedByClick = { casing: api.casing().layers.length === 0, dimmed: dimmed(), row: !rows[rs].classList.contains("sel") };
byStep[rs][0].layer.handlers.click();
docHandlers.keydown({ key: "Escape" });
r.clearedByEscape = { casing: api.casing().layers.length === 0, dimmed: dimmed(), row: !rows[rs].classList.contains("sel") };
// keyboard on a row
rows[vs].handlers.keydown({ key: "Enter", preventDefault() {} });
r.keyboard = rows[vs].classList.contains("sel");
api.render({ ...plan, map: null });
r.hiddenWithoutMap = els.mapbox.hidden;
console.log(JSON.stringify(r));
