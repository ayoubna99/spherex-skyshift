// SkyShift frontend. Talks to the backend API described in docs/api.md.
// Two parts: the reveal (static showcase data, no backend) and the explorer (static snapshot or live API).
const params = new URLSearchParams(location.search);
const API = (params.get("api") || window.SKYSHIFT_API || "http://localhost:8000").replace(/\/$/, "");
const LIVE = params.get("live") === "1";
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

const BANDS = { 1: "0.75–1.09 µm", 2: "1.10–1.62 µm", 3: "1.63–2.41 µm", 4: "2.42–3.82 µm", 5: "3.83–4.41 µm", 6: "4.42–5.00 µm" };
const MODE_HINT = {
  color: "One image per sky pass, about every six months. False colour: 4.7 µm → red, 2 µm → green, 0.9 µm → blue. Cool objects look red, hot stars blue-white.",
  stacks: "One image per sky pass, stacked for clarity, all on the same brightness scale. The target sits at the centre marks.",
  diffs: "Later pass minus earlier pass. Amber = brighter now, blue = brighter before. A moving star shows as a blue–amber pair.",
  frames: "Every single exposure in time order. Pick one and ask which satellites crossed it.",
};
const $ = (id) => document.getElementById(id);
const esc = (t) => String(t).replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
const fmtDate = (s) => (s ? s.slice(0, 10) : "");
const COLD = "#6cc0ff", HOT = "#ffb35c", MUTED = "#8b96ad";

// ---------------------------------------------------------------- data access
// Positions of the ready-made views. Coordinates typed within 3 arcminutes of one of them (for example
// the challenge's own coordinates for WISE 0855) open the ready-made view at once instead of a new job.
const READY = { "barnard": [269.446214, 4.769042], "eps-indi-b": [331.096863, -56.801278], "wise-0855": [133.767072, -7.242742],
                "rw-aur": [76.956375, 30.401194], "r-dra": [248.1675, 66.7551] };
function nearReady(ra, dec) {
  for (const [k, [r0, d0]] of Object.entries(READY)) {
    const dra = ((ra - r0 + 540) % 360 - 180) * Math.cos(d0 * Math.PI / 180), dd = dec - d0;
    if (Math.hypot(dra, dd) < 0.05) return k;
  }
  return null;
}
function staticPath(path) {
  if (path === "/api/targets") return "static/targets.json";
  if (path === "/api/showcase") return "static/showcase.json";
  const m = path.match(/^\/api\/frames\?target=([\w-]+)$/);
  if (m) return `static/frames_${m[1]}.json`;
  const c = path.match(/^\/api\/frames\?ra=([-\d.]+)&dec=([-\d.]+)&size_deg=([\d.]+)$/);
  if (c && +c[3] <= 0.15) { const k = nearReady(+c[1], +c[2]); if (k) return `static/frames_${k}.json`; }
  return null;
}
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
async function fetchJSON(url, base) {
  // Long jobs answer 202 {pending} and run on the server; ask again until the result is ready.
  // The shared host sometimes answers rapid repeat requests with an HTML "One moment, please" page
  // instead of JSON; treat that like "still working" and back off, rather than failing.
  let r, d, tries = 0;
  const t0 = Date.now();
  for (;;) {
    try { r = await fetch(url); }
    catch (e) { if (base !== "static" && tries++ < 6) { await sleep(8000); continue; } throw e; }
    const isJSON = (r.headers.get("content-type") || "").includes("json");
    if (r.status === 202 || (base !== "static" && r.ok && !isJSON)) {
      const p = isJSON ? await r.json().catch(() => ({})) : {};
      const waited = Math.round((Date.now() - t0) / 1000);
      if (typeof S !== "undefined" && S.onPending) S.onPending(Math.max(p.since_s || 0, waited));
      if (waited > 900) throw new Error("the server is still working on this position; try again in a few minutes");
      tries++;
      await sleep(isJSON ? 8000 : 15000);
      continue;
    }
    break;
  }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  d = await r.json();
  (Array.isArray(d) ? d : [d]).forEach((x) => { if (x && typeof x === "object") x._base = base; });
  return d;
}
async function api(path) {
  const sp = staticPath(path);
  if (sp && !LIVE) { try { return await fetchJSON(sp, "static"); } catch (e) { /* fall through */ } }
  try { return await fetchJSON(API + path, API); }
  catch (e) { if (sp && LIVE) return fetchJSON(sp, "static"); throw e; }
}
const offline = (e) => e instanceof TypeError;
const imgURL = (base, png) => `${base || API}/${png}`;

// ================================================================ THE REVEAL
const R = { scene: 0, timer: null, playing: !REDUCED, streak: null, specAnim: null, geoAnim: null };
const SCENE_MS = [7000, 11000, 11000, 12000, 12000];

function bnu(l, T) { return Math.pow(l, -3) / (Math.exp(14387.77 / (l * T)) - 1); }

// ---- thermal cross-section: brightness + temperature + distance -> emitting area (emissivity assumed)
// Streak brightness is the time-averaged, across-trail-integrated surface brightness (MJy/sr per pixel).
// A point source of flux density F crossing at angular rate w deposits F * (px/w) per pixel column over an
// exposure of length T, so F = B * px * w * T. Then F = eps * Planck(T_sat) * A / d^2.
const EPS = 0.9;                     // assumed emissivity of spacecraft surfaces (paint, MLI, oxidised metal)
const PX_RAD = 6.15 / 206265;
function planckMJysr(um, T) {        // Planck B_nu in MJy/sr
  const h = 6.62607e-34, c = 2.99792e8, k = 1.380649e-23, nu = c / (um * 1e-6);
  return 2 * h * nu ** 3 / c ** 2 / (Math.exp(h * nu / (k * T)) - 1) / 1e-26 / 1e6;
}
// SPHEREx fits a straight line to the charge ramp over the exposure. A satellite deposits its charge in one
// instant t0, so the fitted slope is 6*t0*(T-t0)/T^2 times the slope a steady source of the same total charge
// would give (1.5x at mid-exposure, ->0 at the ends). The two-camera ratio is immune; absolute flux is not.
function surFactor(c) {
  const sat = c.satellite, T = c.exposure_s || 115;
  if (!sat || !sat.time_utc || !c.date) return 1;
  const t0 = (Date.parse(sat.time_utc + "Z") - Date.parse(c.date + "Z")) / 1000;
  if (!(t0 > 0 && t0 < T)) return 1;
  return Math.max(0.2, 6 * t0 * (T - t0) / (T * T));
}
function emittingArea(c) {
  const sat = c.satellite;
  if (!sat || !c.temp_k || !c.flux_long || !sat.crossing_s || !sat.distance_km) return null;
  const w = (c.length_deg * Math.PI / 180) / sat.crossing_s, T = c.exposure_s || 115;
  const F = c.flux_long / surFactor(c) * PX_RAD * w * T;              // MJy, corrected for when it crossed
  const area = (Tk) => F / (EPS * planckMJysr(c.wavelength_long_um, Tk)) * (sat.distance_km * 1e3) ** 2;
  return { m2: area(c.temp_k), lo: area(c.temp_range_k[1]), hi: area(c.temp_range_k[0]), fluxJy: F * 1e6 };
}
// Published dimensions of the showcase objects, for the comparison (sources in the Method section)
const KNOWN_SIZE = {
  "16910": "An H-1 second stage is a 10.3 m × 2.5 m cylinder: about 26 m² side-on, 5 m² end-on.",
  "19649": "A Tselina-2 satellite is a 4.5 m × 1.4 m body with a 22.7 m solar-panel span: tens of m² depending on the angle.",
};
const fmtArea = (a) => `${Math.round(a.m2)} m²`;
const fmtAreaRange = (a) => `${Math.round(a.lo)}–${Math.round(a.hi)} m² over the temperature range; about 1.8× more if the surface is a poor emitter`;

async function initReveal() {
  // scene navigation
  const nav = $("sceneNav");
  ["Exposure", "Two cameras", "Temperature", "Name", "Size"].forEach((t, i) => {
    const b = document.createElement("button"); b.textContent = t; b.setAttribute("role", "tab");
    b.onclick = () => { gotoScene(i); pauseReveal(); };
    nav.appendChild(b);
  });
  $("sceneNext").onclick = () => { gotoScene(R.scene + 1); pauseReveal(); };
  $("sceneBack").onclick = () => { gotoScene(R.scene - 1); pauseReveal(); };
  $("scenePlay").onclick = () => (R.playing ? pauseReveal() : resumeReveal());
  // comparison slider
  const setCmp = (v) => { $("cmpOver").style.clipPath = `inset(0 ${100 - v}% 0 0)`; $("cmpHandle").style.left = v + "%"; };
  $("cmpRange").oninput = (e) => { setCmp(+e.target.value); pauseReveal(); };
  setCmp(50);
  // showcase data for the spectrum and geometry
  try {
    const sc = await api("/api/showcase");
    R.showcase = sc;
    R.streak = sc[0].streaks[0];
  } catch (e) { /* the reveal still works, the spectrum stays empty */ }
  if (R.streak) {
    $("specT").textContent = `${R.streak.temp_k} K`;
    $("bigTsub").textContent = `${R.streak.temp_range_k[0]}–${R.streak.temp_range_k[1]} K · about ${R.streak.temp_k - 273} °C`;
    const a = emittingArea(R.streak);
    if (a) {
      $("bigA").textContent = fmtArea(a);
      $("bigAsub").textContent = fmtAreaRange(a);
      $("sizeKnown").textContent = KNOWN_SIZE[R.streak.satellite.norad] || "";
      $("sizeFlux").textContent = `${Math.round(a.fluxJy).toLocaleString()} Jy`;
    }
    try {
      const b = R.showcase[1]?.streaks[0], ab = b && emittingArea(b);
      if (ab) $("secondArea").innerHTML = `<b>${fmtArea(ab)}</b> thermal cross-section (${Math.round(ab.lo)}–${Math.round(ab.hi)})`;
    } catch (e) { /* optional */ }
  }
  gotoScene(0);
  // only auto-play while the reveal is on screen
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((es) => es.forEach((en) => {
      if (en.isIntersecting && R.playing && !R.timer) schedule();
      if (!en.isIntersecting) clearTimeout(R.timer), (R.timer = null);
    }), { threshold: 0.35 }).observe($("reveal"));
  } else if (R.playing) schedule();
}
function schedule() {
  clearTimeout(R.timer);
  R.timer = setTimeout(() => { R.timer = null; gotoScene((R.scene + 1) % 5); if (R.playing) schedule(); }, SCENE_MS[R.scene]);
}
function pauseReveal() { R.playing = false; clearTimeout(R.timer); R.timer = null; $("scenePlay").textContent = "Auto-play"; $("reveal").classList.remove("auto"); }
function resumeReveal() { R.playing = true; $("scenePlay").textContent = "Pause"; $("reveal").classList.add("auto"); schedule(); }
function gotoScene(i) {
  R.scene = (i + 5) % 5;
  document.querySelectorAll("#stageBox .scene, .scene-copy .copy").forEach((el) => el.classList.toggle("on", +el.dataset.scene === R.scene));
  [...$("sceneNav").children].forEach((b, k) => { b.classList.toggle("on", k === R.scene); b.setAttribute("aria-selected", k === R.scene); });
  $("reveal").style.setProperty("--scene-ms", SCENE_MS[R.scene] + "ms");
  $("reveal").classList.toggle("auto", R.playing);
  // restart the bar animation
  const nav = $("sceneNav"); nav.classList.remove("tick"); void nav.offsetWidth; nav.classList.add("tick");
  $("sceneNext").textContent = R.scene === 4 ? "Start again" : "Next";
  if (R.scene === 2) animateSpectrum();
  if (R.scene === 3) animateGeometry();
  if (R.scene === 4) drawSize($("sizeCanvas"), R.streak);
  if (R.playing && !R.timer) schedule();
}

// ---- spectrum, drawn progressively
function animateSpectrum() {
  const cv = $("specCanvas"), c = R.streak;
  if (!c) return;
  cancelAnimationFrame(R.specAnim);
  const t0 = performance.now(), dur = REDUCED ? 0 : 3200;
  const tick = (now) => {
    const p = dur ? Math.min(1, (now - t0) / dur) : 1;
    drawSpectrum(cv, c, p, true);
    const T = Math.round(c.temp_k * Math.min(1, Math.max(0, (p - 0.55) / 0.45)));
    $("bigT").textContent = p > 0.55 ? `${T} K` : "…";
    if (p < 1) R.specAnim = requestAnimationFrame(tick);
  };
  R.specAnim = requestAnimationFrame(tick);
}
function drawSpectrum(cv, c, progress = 1, big = false) {
  const g = cv.getContext("2d"), W = cv.width, H = cv.height;
  const padL = big ? 70 : 46, padB = big ? 54 : 30, padT = big ? 40 : 28, padR = 24;
  g.clearRect(0, 0, W, H);
  const pts = c.spectrum.filter((p) => p.value > 0);
  if (!pts.length) return;
  const xs = (l) => padL + (l - 1.5) / (5.1 - 1.5) * (W - padL - padR);
  const vals = pts.map((p) => p.value);
  const lo = Math.log10(Math.min(...vals) / 3), hi = Math.log10(Math.max(...vals) * 2.2);
  const ys = (v) => H - padB - (Math.log10(v) - lo) / (hi - lo) * (H - padB - padT);
  const fs = big ? 20 : 16;
  g.font = `${fs}px "IBM Plex Sans", system-ui`; g.fillStyle = MUTED;
  // axes
  g.strokeStyle = "#1e2638"; g.lineWidth = 1;
  [2, 3, 4, 5].forEach((l) => { g.beginPath(); g.moveTo(xs(l), padT); g.lineTo(xs(l), H - padB); g.stroke(); g.fillText(l + " µm", xs(l) - fs, H - padB + fs + 6); });
  g.save(); g.translate(16, H / 2); g.rotate(-Math.PI / 2); g.fillText("brightness (log)", -60, 0); g.restore();
  // reflected-sunlight-only prediction, scaled to the short camera
  const sun = (l) => bnu(l, 5772);
  g.strokeStyle = "#4b5872"; g.setLineDash([7, 7]); g.lineWidth = 2; g.beginPath();
  for (let l = 1.5; l <= 5.1; l += 0.05) { const v = c.flux_short * sun(l) / sun(c.wavelength_short_um); const y = ys(v); l === 1.5 ? g.moveTo(xs(l), y) : g.lineTo(xs(l), y); }
  g.stroke(); g.setLineDash([]);
  // points appear left to right
  const sorted = [...pts].sort((a, b) => a.um - b.um);
  const nShow = Math.ceil(sorted.length * Math.min(1, progress / 0.6));
  sorted.slice(0, nShow).forEach((p) => {
    g.fillStyle = p.camera === "short" ? COLD : HOT;
    g.beginPath(); g.arc(xs(p.um), ys(p.value), big ? 6 : 4, 0, 7); g.fill();
    if (p.err) { g.strokeStyle = g.fillStyle; g.globalAlpha = .5; g.beginPath(); g.moveTo(xs(p.um), ys(Math.max(p.value - p.err, p.value / 4))); g.lineTo(xs(p.um), ys(p.value + p.err)); g.stroke(); g.globalAlpha = 1; }
  });
  // blackbody fit draws after the points
  if (c.temp_k && progress > 0.6) {
    const q = Math.min(1, (progress - 0.6) / 0.4);
    g.strokeStyle = HOT; g.lineWidth = big ? 3 : 2; g.beginPath();
    const l1 = 3.6, l2 = 3.6 + (5.1 - 3.6) * q;
    for (let l = l1; l <= l2; l += 0.02) { const v = c.flux_long * bnu(l, c.temp_k) / bnu(c.wavelength_long_um, c.temp_k); const y = ys(v); l === l1 ? g.moveTo(xs(l), y) : g.lineTo(xs(l), y); }
    g.stroke();
  }
  // legend
  g.fillStyle = COLD; g.fillRect(W - (big ? 330 : 300), padT - 10, 12, 12); g.fillStyle = MUTED; g.fillText("short camera", W - (big ? 310 : 282), padT + 1);
  g.fillStyle = HOT; g.fillRect(W - (big ? 180 : 160), padT - 10, 12, 12); g.fillStyle = MUTED; g.fillText("long camera", W - (big ? 160 : 142), padT + 1);
  g.fillStyle = MUTED; g.fillText("- - sunlight only", xs(1.6), padT + 1);
}

// ---- geometry: Earth, SPHEREx, the object, the line of sight — to scale
function animateGeometry() {
  cancelAnimationFrame(R.geoAnim);
  const t0 = performance.now(), dur = REDUCED ? 0 : 2600;
  const tick = (now) => {
    const p = dur ? Math.min(1, (now - t0) / dur) : 1;
    drawGeometry($("geoCanvas"), R.streak?.satellite || { altitude_km: 1508, distance_km: 987, name: "H-1 R/B(MABES)" }, p);
    if (p < 1) R.geoAnim = requestAnimationFrame(tick);
  };
  R.geoAnim = requestAnimationFrame(tick);
}
function drawGeometry(cv, sat, p = 1) {
  const g = cv.getContext("2d"), W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  const RE = 6371, hS = 650, hO = sat.altitude_km, d = sat.distance_km;
  const r1 = RE + hS, r2 = RE + hO;
  // angle between the two radius vectors from the triangle Earth–SPHEREx–object
  const cosT = (r1 * r1 + r2 * r2 - d * d) / (2 * r1 * r2);
  const theta = Math.acos(Math.max(-1, Math.min(1, cosT)));
  // zoom on the region above the limb; 1 km = k px
  const k = (Math.min(W, H) * 0.78) / (hO + 1700);
  const cx = W * 0.5, cy = H * 0.6 + RE * k;         // Earth centre, far below the canvas
  const pos = (r, a) => [cx + r * Math.sin(a) * k, cy - r * Math.cos(a) * k];
  const aS = -theta * 0.55, aO = theta * 0.45;
  const [sx, sy] = pos(r1, aS), [ox, oy] = pos(r2, aO);
  // Earth
  g.fillStyle = "#101a2c"; g.beginPath(); g.arc(cx, cy, RE * k, 0, 7); g.fill();
  g.strokeStyle = "#2a3a5c"; g.lineWidth = 2; g.stroke();
  g.font = `18px "IBM Plex Sans", system-ui`; g.fillStyle = MUTED;
  g.fillText("Earth", cx - 24, cy - RE * k + 34);
  // orbit rings
  [[r1, "SPHEREx orbit · about 650 km"], [r2, `${sat.name.replace(/\(.*\)/, "").trim()} · ${hO.toLocaleString()} km`]].forEach(([r, lab], i) => {
    g.strokeStyle = i ? HOT : COLD; g.globalAlpha = .35; g.setLineDash([4, 8]); g.lineWidth = 1.5;
    g.beginPath(); g.arc(cx, cy, r * k, -Math.PI / 2 - 0.5, -Math.PI / 2 + 0.5); g.stroke();
    g.setLineDash([]); g.globalAlpha = 1;
    g.fillStyle = i ? HOT : COLD; g.fillText(lab, 24, cy - r * k - 10);
  });
  // line of sight grows, then the object appears
  const q = Math.min(1, p / 0.7);
  g.strokeStyle = "#e8edf6"; g.lineWidth = 2; g.setLineDash([2, 6]); g.beginPath();
  g.moveTo(sx, sy); g.lineTo(sx + (ox - sx) * q, sy + (oy - sy) * q); g.stroke(); g.setLineDash([]);
  // SPHEREx
  g.fillStyle = COLD; g.beginPath(); g.arc(sx, sy, 7, 0, 7); g.fill();
  g.fillStyle = "#e8edf6"; g.font = `600 18px "IBM Plex Sans", system-ui`; g.fillText("SPHEREx", sx - 90, sy + 8);
  g.font = `16px "IBM Plex Sans", system-ui`; g.fillStyle = MUTED; g.fillText("looking away from Earth", sx - 90, sy + 30);
  if (p > 0.7) {
    const s = Math.min(1, (p - 0.7) / 0.3);
    g.fillStyle = HOT; g.globalAlpha = s; g.beginPath(); g.arc(ox, oy, 8, 0, 7); g.fill();
    g.fillStyle = "#e8edf6"; g.font = `600 18px "IBM Plex Sans", system-ui`; g.fillText(sat.name, ox + 16, oy + 6);
    g.font = `16px "IBM Plex Sans", system-ui`; g.fillStyle = MUTED; g.fillText(`${d.toLocaleString()} km away, crossing the field`, ox + 16, oy + 28);
    g.globalAlpha = 1;
  }
  // distance label on the line
  g.fillStyle = "#e8edf6"; g.font = `600 20px "IBM Plex Sans", system-ui`;
  g.fillText(`${Math.round(d * q).toLocaleString()} km`, (sx + ox) / 2 - 120, (sy + oy) / 2 + 8);
}

// ---- size: the measured thermal cross-section next to the object's published outline, same scale
function drawSize(cv, c) {
  const g = cv.getContext("2d"), W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  const a = c && emittingArea(c);
  if (!a) return;
  const real = c.satellite.norad === "16910" ? { w: 10.32, h: 2.49, label: "H-1 second stage, 10.3 × 2.5 m (published)" }
             : c.satellite.norad === "19649" ? { w: 22.7, h: 1.4, label: "Tselina-2 with panels, 22.7 m span (published)" } : null;
  const side = Math.sqrt(a.m2), sideLo = Math.sqrt(a.lo), sideHi = Math.sqrt(a.hi);
  const k = (W * 0.42) / Math.max(sideHi, real ? real.w : 0);     // px per metre
  g.font = `18px "IBM Plex Sans", system-ui`; g.fillStyle = MUTED;
  // measured: a square of the fitted area, with the range as fainter squares
  const cx = W * 0.3, cy = H * 0.5;
  [[sideHi, .18], [side, .55], [sideLo, .9]].forEach(([s, al]) => {
    g.fillStyle = HOT; g.globalAlpha = al; g.fillRect(cx - s * k / 2, cy - s * k / 2, s * k, s * k);
  });
  g.globalAlpha = 1; g.fillStyle = "#e8edf6"; g.font = `600 20px "IBM Plex Sans", system-ui`;
  g.fillText(`${Math.round(a.m2)} m² measured`, cx - 90, cy + sideHi * k / 2 + 34);
  g.font = `16px "IBM Plex Sans", system-ui`; g.fillStyle = MUTED;
  g.fillText(`${Math.round(a.lo)}–${Math.round(a.hi)} m² over the temperature range`, cx - 150, cy + sideHi * k / 2 + 58);
  if (real) {
    const rx = W * 0.72, ry = H * 0.5;
    g.strokeStyle = COLD; g.lineWidth = 3; g.strokeRect(rx - real.w * k / 2, ry - real.h * k / 2, real.w * k, real.h * k);
    g.fillStyle = "#e8edf6"; g.font = `600 20px "IBM Plex Sans", system-ui`;
    g.fillText(`${Math.round(real.w * real.h)} m² side-on`, rx - 70, ry + Math.max(real.h * k / 2, 20) + 34);
    g.font = `16px "IBM Plex Sans", system-ui`; g.fillStyle = MUTED;
    g.fillText(real.label, rx - 170, ry + Math.max(real.h * k / 2, 20) + 58);
  }
  // scale bar: 5 m
  g.strokeStyle = "#e8edf6"; g.lineWidth = 2; g.beginPath(); g.moveTo(30, H - 40); g.lineTo(30 + 5 * k, H - 40); g.stroke();
  g.fillStyle = MUTED; g.font = `16px "IBM Plex Sans", system-ui`; g.fillText("5 m", 30, H - 50);
}

// ================================================================ THE CENSUS (static/census.json)
async function initCensus() {
  let d;
  try { d = await fetchJSON("static/census.json", "static"); } catch (e) { return; }
  if (!d || !d.n_measured) return;
  $("census").hidden = false;
  const n = (k) => d[k] ?? 0;
  $("censusNumbers").innerHTML = [
    [n("n_attempted"), "catalogued trails sampled"], [n("n_on_long_pair"), "on a long-wave camera pair"],
    [n("n_measured"), "detected in the long camera"], [n("n_ratio_limits"), "seen in heat only (no reflected light detected)"], [n("n_with_temp"), "with a constrained temperature"],
    [d.ratio_median ? d.ratio_median.toFixed(0) + "×" : "—", "median long/short (stars: " + (d.stars_median ? d.stars_median.toFixed(2) : "—") + "×)"],
    [d.temp_median ? Math.round(d.temp_median) + " K" : "—", d.temp_p16_p84 ? `median temperature (16–84%: ${Math.round(d.temp_p16_p84[0])}–${Math.round(d.temp_p16_p84[1])} K)` : "median temperature"],
  ].map(([v, l]) => `<span><b>${v}</b>${l}</span>`).join("");
  drawHist($("ratioHist"), d.rows.filter((r) => r.ratio > 0 && !r.ratio_is_limit).map((r) => Math.log10(r.ratio)), {
    xmin: -1, xmax: 3.25, bins: 17, color: HOT, xlabel: "long / short brightness", ticks: [[-1, "0.1×"], [0, "1×"], [1, "10×"], [2, "100×"], [3, "1000×"]],
    bands: [{ lo: Math.log10(0.2), hi: Math.log10(0.3), color: COLD, label: "stars" }], lines: [{ x: Math.log10(0.27), label: "sunlight only" }],
    extra: d.rows.filter((r) => r.ratio > 0 && r.ratio_is_limit).map((r) => Math.log10(r.ratio)), extraLabel: "lower limits (no short-camera detection)",
  });
  drawHist($("tempHist"), d.rows.filter((r) => r.temp_k).map((r) => r.temp_k), {
    xmin: 150, xmax: 500, bins: 14, color: HOT, xlabel: "temperature (K)", ticks: [[200, "200"], [280, "280"], [350, "350"], [450, "450"]],
    bands: [{ lo: 277, hi: 283, color: "#8b96ad", label: "280 K model" }], lines: [],
  });
  const tb = $("censusTable");
  tb.innerHTML = `<tr><th>Exposure</th><th>Date</th><th>Cameras</th><th>Length</th><th>Long/short</th><th>Stars</th><th>T (K)</th></tr>` +
    d.rows.map((r) => `<tr><td>${esc(r.obsid)}</td><td>${r.date}</td><td>D${r.pair[0]}+D${r.pair[1]}</td><td>${r.length_deg}°</td><td>${r.ratio ? (r.ratio_is_limit ? "> " : "") + r.ratio.toFixed(1) + "×" : "—"}</td><td>${r.ratio_stars ? r.ratio_stars.toFixed(2) + "×" : "—"}</td><td>${r.temp_k ? `${r.temp_k} (${r.temp_range_k[0]}–${r.temp_range_k[1]})` : "—"}</td></tr>`).join("");
  $("censusNote").textContent = `Sample: a seeded random draw from the ${d.n_catalogued.toLocaleString()} trails in the catalogue, in DR1 reprocessing. Selection effects: our detector needs the pipeline's STREAK flag and a long enough line, so faint trails drop out, and short-wave-only pairs (D1/D4) are skipped. Ratios are robust to when the satellite crossed the exposure; absolute fluxes are not. Trails measured on D2/D5 use 1.4 and 4.1 µm instead of 2.0 and 4.7 µm, which lowers the ratio for the same temperature.`;
}
function drawHist(cv, xs, o) {
  const g = cv.getContext("2d"), W = cv.width, H = cv.height, L = 60, R = 20, T = 30, B = 70;
  g.clearRect(0, 0, W, H);
  const X = (x) => L + (x - o.xmin) / (o.xmax - o.xmin) * (W - L - R);
  const counts = new Array(o.bins).fill(0);
  xs.forEach((x) => { const i = Math.floor((x - o.xmin) / (o.xmax - o.xmin) * o.bins); if (i >= 0 && i < o.bins) counts[i]++; });
  const ec2 = new Array(o.bins).fill(0);
  (o.extra || []).forEach((x) => { const i = Math.floor((x - o.xmin) / (o.xmax - o.xmin) * o.bins); if (i >= 0 && i < o.bins) ec2[i]++; });
  const ymax = Math.max(1, ...counts.map((c, i) => c + ec2[i])), Y = (c) => H - B - c / ymax * (H - T - B);
  g.font = `16px "IBM Plex Sans", system-ui`;
  o.bands.forEach((b) => { g.fillStyle = b.color; g.globalAlpha = .25; g.fillRect(X(b.lo), T, X(b.hi) - X(b.lo), H - T - B); g.globalAlpha = 1; g.fillStyle = b.color; g.fillText(b.label, X(b.hi) + 6, T + 40); });
  o.lines.forEach((l) => { g.strokeStyle = "#4b5872"; g.setLineDash([6, 6]); g.beginPath(); g.moveTo(X(l.x), T); g.lineTo(X(l.x), H - B); g.stroke(); g.setLineDash([]); g.fillStyle = MUTED; g.fillText(l.label, X(l.x) + 6, H - B - 8); });
  const bw = (W - L - R) / o.bins;
  counts.forEach((c, i) => { if (!c) return; g.fillStyle = o.color; g.fillRect(L + i * bw + 1, Y(c), bw - 2, H - B - Y(c)); });
  if (o.extra && o.extra.length) {               // lower limits: hollow bars stacked on top, with arrows
    const ec = new Array(o.bins).fill(0);
    o.extra.forEach((x) => { const i = Math.floor((x - o.xmin) / (o.xmax - o.xmin) * o.bins); if (i >= 0 && i < o.bins) ec[i]++; });
    ec.forEach((c, i) => { if (!c) return; const y0 = Y(counts[i]), y1 = Y(counts[i] + c); g.strokeStyle = o.color; g.lineWidth = 2; g.strokeRect(L + i * bw + 2, y1, bw - 4, y0 - y1); g.fillStyle = o.color; g.fillText("→", L + i * bw + bw / 2 - 6, y1 - 6); });
    g.fillStyle = MUTED; g.fillText("hollow → " + o.extraLabel, W - R - 420, T + 40);
  }
  g.strokeStyle = "#2a3448"; g.beginPath(); g.moveTo(L, H - B); g.lineTo(W - R, H - B); g.moveTo(L, T); g.lineTo(L, H - B); g.stroke();
  g.fillStyle = MUTED;
  o.ticks.forEach(([x, t]) => { g.fillText(t, X(x) - 10, H - B + 22); });
  g.fillText(o.xlabel, W / 2 - 60, H - 14);
  for (let c = 0; c <= ymax; c += Math.max(1, Math.ceil(ymax / 4))) g.fillText(c, L - 28, Y(c) + 5);
  g.fillText(`N = ${xs.length + (o.extra ? o.extra.length : 0)}`, W - R - 80, T + 16);
}

// ================================================================ THE EXPLORER
const S = { data: null, mode: "color", det: 1, list: [], i: 0, timer: null, view: null, aladin: null, front: "A" };
function status(msg, busy = false) { $("status").textContent = msg || ""; $("status").classList.toggle("busy", busy); }

async function initTargets() {
  const box = $("targets");
  try {
    const ts = await api("/api/targets");
    ts.forEach((t) => {
      const b = document.createElement("button"); b.className = "target";
      b.innerHTML = `<b>${esc(t.name)}</b><span>${esc(t.note)}</span>`;
      b.onclick = () => { loadView({ target: t.key }); };
      box.appendChild(b);
    });
  } catch (e) { status("Targets could not be loaded: " + e.message); }
}
function syncURL() {
  const p = new URLSearchParams();
  if (S.view?.target) p.set("target", S.view.target);
  else if (S.view) { p.set("ra", S.view.ra); p.set("dec", S.view.dec); p.set("fov", S.view.fov); }
  p.set("mode", S.mode); p.set("det", S.det);
  if (params.get("api")) p.set("api", params.get("api"));
  if (LIVE) p.set("live", "1");
  history.replaceState(null, "", "?" + p.toString() + "#explore");
}

// ---- sky map
// Base layers, both served by NASA/IPAC IRSA (no dependency on CDS Strasbourg):
//  - AllWISE colour HiPS (mirror of the CDS product)
//  - SPHEREx DR1 all-sky data cube: 102 frames, one per band/channel; frames 50 and 51 (dichroic edges) are blank.
const HIPS_WISE = "https://irsa.ipac.caltech.edu/data/hips/CDS/AllWISE/RGB-W4-W2-W1";
const HIPS_SPHEREX = "https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube";
const SPHEREX_UM = [0.753,0.771,0.790,0.809,0.828,0.848,0.868,0.889,0.910,0.932,0.955,0.978,1.001,1.025,1.050,1.075,1.102,1.113,1.139,1.167,1.195,1.224,1.254,1.284,1.315,1.346,1.378,1.412,1.446,1.481,1.517,1.554,1.592,1.631,1.656,1.695,1.735,1.775,1.818,1.861,1.905,1.949,1.995,2.041,2.089,2.139,2.189,2.240,2.292,2.345,2.399,2.445,2.506,2.575,2.646,2.719,2.794,2.871,2.951,3.033,3.118,3.204,3.293,3.383,3.476,3.570,3.667,3.768,3.833,3.867,3.901,3.935,3.970,4.005,4.040,4.076,4.112,4.148,4.184,4.220,4.257,4.294,4.331,4.368,4.405,4.435,4.467,4.500,4.533,4.566,4.600,4.635,4.669,4.704,4.739,4.773,4.808,4.843,4.879,4.915,4.951,4.988];
function setBase(which) {
  if (!S.aladin) { S.pendingBase = which; return; }
  [...$("baseSeg").children].forEach((b) => b.classList.toggle("on", b.dataset.base === which));
  $("bandSlider").hidden = which !== "spherex";
  if (which === "spherex") {
    if (!S.cube) S.cube = A.imageHiPS(HIPS_SPHEREX, { name: "SPHEREx DR1 all-sky cube", imgFormat: "png", maxOrder: 5 });
    S.aladin.setBaseImageLayer(S.cube);
    // the slice can only be set once the layer has loaded its properties; the default middle slice (50) is blank
    [100, 1500, 4000].forEach((ms) => setTimeout(() => $("bandSlider").hidden || setBand(+$("bandRange").value), ms));
    $("mapNote").textContent = "SPHEREx's own all-sky map at one wavelength. Coarser than the cutouts above (about 13″ per pixel) but it is the whole sky, in every band.";
  } else {
    stopSweep();
    if (!S.wise) S.wise = A.imageHiPS(HIPS_WISE, { name: "AllWISE colour" });
    S.aladin.setBaseImageLayer(S.wise);
    $("mapNote").textContent = "Drag the map to explore; click “Load SPHEREx images here” to see that spot over time. Box = the field above; red line = a measured streak.";
  }
}
function setBand(n) {
  if (n === 50 || n === 51) n = n === 50 ? 49 : 52;          // blank dichroic frames
  $("bandRange").value = n; $("bandLabel").textContent = SPHEREX_UM[n - 1].toFixed(2) + " µm";
  try { S.cube && S.cube.setSliceNumber(n); } catch (e) { /* older Aladin Lite without cube support */ }
}
function stopSweep() { clearInterval(S.sweep); S.sweep = null; $("bandPlay").textContent = "Sweep"; }
$("baseSeg").onclick = (e) => { if (e.target.dataset.base) setBase(e.target.dataset.base); };
$("bandRange").oninput = (e) => { stopSweep(); clearTimeout(S.bandT); $("bandLabel").textContent = SPHEREX_UM[+e.target.value - 1].toFixed(2) + " µm"; S.bandT = setTimeout(() => setBand(+e.target.value), 250); };
$("bandPlay").onclick = () => {
  if (S.sweep) return stopSweep();
  // Each frame is a fresh set of tiles from IRSA; at a wide field that is hundreds of requests per step and the
  // server starts resetting connections. Keep the sweep gentle, and refuse it when the field is very wide.
  if (S.aladin && S.aladin.getFov()[0] > 8) { status("Zoom in to under 8° before sweeping the wavelengths — each step reloads the whole view from NASA."); return; }
  $("bandPlay").textContent = "Stop";
  S.sweep = setInterval(() => { let n = +$("bandRange").value + 3; if (n > 102) n = 1; setBand(n); }, 1200);
};
function initAladin() {
  if (!window.A || S.aladinStarted) return;
  S.aladinStarted = true;
  A.init.then(() => {
    S.aladin = A.aladin("#aladin", { survey: HIPS_WISE, fov: 0.5, log: false, showReticle: true, showLayersControl: false, showGotoControl: false, showFullscreenControl: false, showFrame: false, showCooGrid: false });
    S.overlay = A.graphicOverlay({ color: HOT, lineWidth: 2 });
    S.aladin.addOverlay(S.overlay);
    if (S.pendingBase) setBase(S.pendingBase);
    if (S.pendingMap) S.pendingMap();
    // Drag the map, put the centre crosshair on something, click: load SPHEREx images of that spot.
    S.aladin.on("positionChanged", () => { clearTimeout(S.mapT); S.mapT = setTimeout(updateMapLoad, 150); });
  }).catch(() => { $("aladin").hidden = true; $("bandSlider").hidden = true; $("baseSeg").hidden = true; });
}
window.addEventListener("aladin-ready", initAladin);
function mapCentre() {
  const [ra, dec] = S.aladin.getRaDec();
  return [+(((ra % 360) + 360) % 360).toFixed(5), +dec.toFixed(5)];
}
function updateMapLoad() {
  if (!S.aladin) return;
  const [ra, dec] = mapCentre();
  const cur = S.data ? S.data.target : (S.view && S.view.ra != null ? S.view : null);
  let far = true;
  if (cur) {
    const dra = ((ra - cur.ra + 540) % 360 - 180) * Math.cos(dec * Math.PI / 180);
    far = Math.hypot(dra, dec - cur.dec) > 0.02;                 // more than about 1 arcminute away
  }
  $("mapLoadBtn").hidden = !far;
  $("mapLoadPos").textContent = `RA ${ra.toFixed(3)}°, Dec ${dec.toFixed(3)}°` + (nearReady(ra, dec) ? " · ready-made view" : "");
}
$("mapLoadBtn").onclick = () => {
  const [ra, dec] = mapCentre();
  $("mapLoadBtn").hidden = true;
  loadView({ ra, dec, fov: +$("fov").value });
};
function mapGoto(ra, dec, fovDeg) {
  if (!S.aladin) { S.pendingMap = () => mapGoto(ra, dec, fovDeg); return; }
  S.aladin.gotoRaDec(ra, dec); S.aladin.setFoV(Math.max(fovDeg * 3, 0.3));
  S.overlay.removeAll();
  const h = fovDeg / 2, c = Math.cos(dec * Math.PI / 180);
  S.overlay.add(A.polyline([[ra - h / c, dec - h], [ra + h / c, dec - h], [ra + h / c, dec + h], [ra - h / c, dec + h], [ra - h / c, dec - h]]));
}
function mapStreak(line) {
  if (!S.aladin) { S.pendingMap = () => mapStreak(line); return; }
  S.overlay.add(A.polyline(line, { color: "#ff5f5f", lineWidth: 3 }));
  const ra = (line[0][0] + line[1][0]) / 2, dec = (line[0][1] + line[1][1]) / 2;
  S.aladin.gotoRaDec(ra, dec); S.aladin.setFoV(4.5);
}

// ---- loading a view
async function loadView(v) {
  stop();
  S.view = v; S.data = null; $("cards").innerHTML = "";
  const q = v.target ? `target=${v.target}` : `ra=${v.ra}&dec=${v.dec}&size_deg=${v.fov}`;
  $("title").textContent = "Loading…"; $("empty").hidden = false; $("empty").textContent = "Fetching SPHEREx images from NASA…";
  status("A new position takes 1–3 minutes the first time; after that it is instant.", true);
  S.onPending = (t) => { $("empty").textContent = `Fetching every SPHEREx exposure of this spot from NASA… ${t} s. A new position takes a few minutes on our small server; the five targets above are instant.`; };
  document.querySelector(".explore-grid").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
  try {
    const d = await api("/api/frames?" + q);
    S.data = d;
    if (S.mode === "color" && !(d.color || []).length) S.mode = "stacks";
    $("title").textContent = `${d.target.name || "Custom position"} — ${d.n_epochs} sky passes, ${d.frames.length} exposures`;
    $("ra").value = d.target.ra; $("dec").value = d.target.dec;
    status(`RA ${d.target.ra.toFixed(4)}°, Dec ${d.target.dec.toFixed(4)}°, field ${(d.target.size_deg * 60).toFixed(1)}′` + (d.note ? ` — ${d.note}` : ""));
    mapGoto(d.target.ra, d.target.dec, d.target.size_deg);
    buildDetButtons(); rebuildList(); show(0); syncURL();
    if ($("gaiaToggle").checked) $("gaiaToggle").onchange({ target: $("gaiaToggle") });
    if (!REDUCED) play();
  } catch (e) {
    const irsa = /IRSA|archive|502|503|Proxy Error/i.test(e.message || "");
    const msg = offline(e) ? "The live server is not reachable right now (it may be waking up). Try again in a minute, or pick one of the ready-made targets."
      : irsa ? "NASA's SPHEREx archive (IRSA, at Caltech) is not answering right now, so a new patch of sky can't be fetched. This is on NASA's side; try again in a few minutes. The five targets above are stored on this site and always work."
      : "Something went wrong: " + e.message;
    $("title").textContent = "Could not load"; status(""); $("empty").textContent = msg;
  }
}
function buildDetButtons() {
  const seg = $("detSeg"); seg.innerHTML = "";
  for (let d = 1; d <= 6; d++) {
    const b = document.createElement("button");
    b.textContent = "D" + d; b.title = BANDS[d];
    b.disabled = !S.data.stacks.some((s) => s.detector === d);
    b.onclick = () => { S.det = d; rebuildList(); show(0); syncURL(); };
    seg.appendChild(b);
  }
  if (!S.data.stacks.some((s) => s.detector === S.det)) S.det = S.data.stacks[0]?.detector || 1;
}
function rebuildList() {
  if (!S.data) return;
  [...$("detSeg").children].forEach((b, k) => b.classList.toggle("on", k + 1 === S.det));
  [...$("modeSeg").children].forEach((b) => b.classList.toggle("on", b.dataset.mode === S.mode));
  $("modeHint").textContent = MODE_HINT[S.mode];
  $("exposureTools").hidden = S.mode !== "frames";
  $("detSeg").classList.toggle("off", S.mode === "color");
  if (S.mode === "color") S.list = S.data.color || [];
  else if (S.mode === "stacks") S.list = S.data.stacks.filter((s) => s.detector === S.det);
  else if (S.mode === "diffs") S.list = S.data.diffs.filter((s) => s.detector === S.det);
  else S.list = S.data.frames.filter((s) => s.detector === S.det).sort((a, b) => a.mjd - b.mjd);
  buildTimeline();
}
function tlLabel(item) {
  if (S.mode === "color" || S.mode === "stacks") return item.date;
  if (S.mode === "diffs") {
    const a = S.data.stacks.find((s) => s.epoch === item.from_epoch && s.detector === item.detector);
    const b = S.data.stacks.find((s) => s.epoch === item.to_epoch && s.detector === item.detector);
    return `${a?.date || "?"} → ${b?.date || "?"}`;
  }
  return fmtDate(item.date);
}
function buildTimeline() {
  const tl = $("timeline"); tl.innerHTML = "";
  if (S.list.length > 40) { tl.hidden = true; return; }
  tl.hidden = false;
  S.list.forEach((it, k) => {
    const b = document.createElement("button"); b.className = "tick" + (it.streak || it.flags?.streak ? " streak" : "");
    b.innerHTML = `<i></i><span>${esc(tlLabel(it))}</span>`;
    b.title = it.streak || it.flags?.streak ? "A satellite streak was flagged in this image" : "";
    b.onclick = () => { stop(); show(k); };
    tl.appendChild(b);
  });
}
function label(item) {
  if (S.mode === "color") return `Pass ${item.epoch + 1}, ${item.date}: colour from D${item.detectors.join(" + D")}, ${item.n_frames} exposure(s)${item.streak ? " — streak flagged" : ""}`;
  if (S.mode === "stacks") return `Pass ${item.epoch + 1}, ${item.date}: ${item.band}, ${item.n_frames} exposure(s)${item.streak ? " — streak flagged" : ""}`;
  if (S.mode === "diffs") return `${tlLabel(item)}, ${BANDS[item.detector]}`;
  return `${fmtDate(item.date)}, ${item.wavelength_um ? item.wavelength_um.toFixed(2) + " µm" : item.band}, ${item.obs_id}${item.flags?.streak ? " — streak flagged" : ""}`;
}
function show(i) {
  if (!S.list.length) { $("frameA").removeAttribute("src"); $("frameB").removeAttribute("src"); $("empty").hidden = false; $("empty").textContent = "No images for this detector."; $("frameInfo").textContent = ""; return; }
  S.i = (i + S.list.length) % S.list.length;
  const it = S.list[S.i];
  // cross-fade between two stacked images
  const next = S.front === "A" ? "B" : "A";
  const img = $("frame" + next);
  img.onload = () => { $("frame" + next).classList.add("on"); $("frame" + S.front).classList.remove("on"); S.front = next; };
  img.src = imgURL(S.data._base, it.png);
  $("empty").hidden = true;
  $("frameInfo").textContent = `${S.i + 1} / ${S.list.length} — ${label(it)}`;
  [...$("timeline").children].forEach((b, k) => b.classList.toggle("on", k === S.i));
}
function play() {
  if (S.timer) return stop();
  $("playBtn").textContent = "Pause";
  S.timer = setInterval(() => show(S.i + 1), +$("speed").value);
}
function stop() { clearInterval(S.timer); S.timer = null; $("playBtn").textContent = "Play"; }

// ---- satellites in a chosen exposure
async function findSatellites() {
  const it = S.list[S.i];
  if (!it || !it.url) return;
  $("findSatBtn").disabled = true;
  status("Analysing the full exposure in both cameras…", true);
  S.onPending = (t) => status(`Analysing the full exposure in both cameras… ${t} s (about 2 minutes the first time)`, true);
  try {
    const r = await api(`/api/streaks?url=${encodeURIComponent(it.url)}&det=${it.detector}`);
    renderCards([r], `Exposure ${it.obs_id}, ${fmtDate(it.date)}`);
    status(`${r.streaks.length} satellite(s) measured in this exposure.`);
    $("cards").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "nearest" });
  } catch (e) { status(offline(e) ? "The analysis server is not reachable right now. Try again in a minute." : "Satellite search failed: " + e.message); }
  $("findSatBtn").disabled = false;
}
function renderCards(results, heading) {
  const box = $("cards"); box.innerHTML = `<h3>${esc(heading)}</h3>`;
  let n = 0;
  results.forEach((res) => res.streaks.forEach((c) => { n++; box.appendChild(card(c, res._base)); }));
  if (!n) box.innerHTML += `<p class="small">No satellite streak with enough signal in this exposure. Try one marked as flagged on the timeline.</p>`;
}
function card(c, base) {
  const el = document.createElement("article"); el.className = "card";
  const T = c.temp_k ? `${c.temp_k} K` : "—";
  const range = c.temp_range_k ? `${c.temp_range_k[0]}–${c.temp_range_k[1]} K, about ${c.temp_k - 273} °C` : "temperature needs the 3.8–5 µm cameras";
  const r = (x) => (x == null ? "—" : x >= 10 ? x.toFixed(0) + "×" : x.toFixed(2) + "×");
  const sat = c.satellite;
  const who = sat
    ? `<p class="satline"><b>${esc(sat.name)}</b> <a href="https://www.n2yo.com/satellite/?s=${encodeURIComponent(sat.norad)}" target="_blank" rel="noopener">NORAD ${esc(sat.norad)}</a><br>
       <span class="small">${sat.distance_km.toLocaleString()} km from SPHEREx, orbiting ${sat.altitude_km.toLocaleString()} km up. Crossed at ${sat.time_utc.slice(11)} UTC in ${sat.crossing_s} s; track on the streak to ${sat.offset_deg}°.</span></p>`
    : `<p class="small">${c.catalog_checked ? "Not in the public satellite catalogue, or its orbit is too uncertain." : "Not named yet: the server has no orbit file for this date."}</p>`;
  el.innerHTML = `
    <p class="temp"><span>${T}</span><small>${range}</small></p>
    ${who}
    <p class="small">${fmtDate(c.date)}, cameras D${c.detectors[0]} + D${c.detectors[1]}, streak ${c.length_deg.toFixed(1)}° long.</p>
    ${c.png_full ? `<img class="full" src="${imgURL(base, c.png_full)}" alt="Whole exposure with the streak">` : ""}
    <div class="row">
      <figure><img src="${imgURL(base, c.png_short)}" alt=""><figcaption>reflected light, ${c.wavelength_short_um.toFixed(1)} µm</figcaption></figure>
      <figure><img src="${imgURL(base, c.png_long)}" alt=""><figcaption>heat, ${c.wavelength_long_um.toFixed(1)} µm</figcaption></figure>
    </div>
    <canvas width="640" height="340"></canvas>
    <table>
      <tr><td>Long camera over short, this satellite</td><td><b>${r(c.ratio_long_short)}</b></td></tr>
      <tr><td>Same ratio, ${c.n_stars} stars in these images</td><td>${r(c.ratio_stars)}</td></tr>
      <tr><td>Sunlight alone would give</td><td>${r(c.ratio_reflected_only)}</td></tr>
    </table>
    ${(() => { const a = emittingArea(c); return a ? `<p class="area"><b>${fmtArea(a)}</b> thermal cross-section <span class="small">(${fmtAreaRange(a)}). ${KNOWN_SIZE[sat.norad] || ""}</span></p>` : ""; })()}
    ${sat ? `<canvas class="geo" width="640" height="360"></canvas>` : ""}
    <button class="btn ghost small">Show on the sky map</button>`;
  el.querySelector("button").onclick = () => mapStreak(c.line_sky);
  setTimeout(() => { drawSpectrum(el.querySelector("canvas"), c); if (sat) drawGeometry(el.querySelector("canvas.geo"), sat); }, 0);
  return el;
}

// ---- "above your city": the zenith right now, as RA/Dec
const CITIES = [
  ["Algiers", 36.75, 3.06], ["Boston", 42.36, -71.06], ["Berlin", 52.52, 13.41], ["Paris", 48.86, 2.35], ["London", 51.51, -0.13],
  ["Cairo", 30.04, 31.24], ["Lagos", 6.52, 3.38], ["Nairobi", -1.29, 36.82], ["Tokyo", 35.68, 139.69], ["Delhi", 28.61, 77.21],
  ["Sydney", -33.87, 151.21], ["São Paulo", -23.55, -46.63], ["Mexico City", 19.43, -99.13], ["Los Angeles", 34.05, -118.24], ["Pasadena (JPL)", 34.15, -118.14],
];
function zenithNow(lat, lon) {
  // Greenwich mean sidereal time (Meeus), then local sidereal time = RA of the zenith; Dec = latitude
  const jd = Date.now() / 86400000 + 2440587.5, T = (jd - 2451545) / 36525;
  let gmst = 280.46061837 + 360.98564736629 * (jd - 2451545) + 0.000387933 * T * T - T * T * T / 38710000;
  const ra = (((gmst + lon) % 360) + 360) % 360;
  return { ra: +ra.toFixed(4), dec: +lat.toFixed(4) };
}
function loadOverhead(lat, lon, place) {
  const z = zenithNow(lat, lon);
  status(`Straight above ${place} right now: RA ${z.ra}°, Dec ${z.dec}°.`);
  loadView({ ra: z.ra, dec: z.dec, fov: 0.12 });
}
CITIES.forEach(([n, la, lo]) => { const o = document.createElement("option"); o.value = `${la},${lo}`; o.textContent = n; $("city").appendChild(o); });
$("city").onchange = (e) => { if (!e.target.value) return; const [la, lo] = e.target.value.split(",").map(Number); loadOverhead(la, lo, e.target.selectedOptions[0].textContent); };
$("geoBtn").onclick = () => {
  if (!navigator.geolocation) { $("overheadNote").textContent = "Your browser does not share location. Pick a city instead."; return; }
  $("geoBtn").disabled = true;
  navigator.geolocation.getCurrentPosition(
    (pos) => { $("geoBtn").disabled = false; loadOverhead(pos.coords.latitude, pos.coords.longitude, "you"); },
    () => { $("geoBtn").disabled = false; $("overheadNote").textContent = "Location not shared. Pick a city instead."; },
    { timeout: 8000 });
};

// ---- Gaia DR3 overlay on the sky map (ESA Gaia archive, relayed by the backend; CDS-independent)
$("gaiaToggle").onchange = async (e) => {
  if (!S.aladin) return;
  if (S.gaia) { S.aladin.removeLayer(S.gaia); S.gaia = null; }
  if (!e.target.checked || !S.data) return;
  status("Fetching Gaia DR3 from the ESA archive…", true);
  try {
    const t = S.data.target;
    const g = await fetchJSON(`${API}/api/gaia?ra=${t.ra}&dec=${t.dec}&radius_deg=${Math.max(t.size_deg, 0.1).toFixed(3)}&limit=400`, API);
    const col = Object.fromEntries(g.columns.map((c, i) => [c, i]));
    S.gaia = A.catalog({ name: "Gaia DR3", color: COLD, shape: "circle", sourceSize: 8, onClick: "showPopup" });
    S.gaia.addSources(g.rows.map((r) => A.source(r[col.ra], r[col.dec], {
      "Gaia DR3": String(r[col.source_id]), "G mag": r[col.phot_g_mean_mag]?.toFixed(2),
      "pm RA (mas/yr)": r[col.pmra]?.toFixed(1), "pm Dec (mas/yr)": r[col.pmdec]?.toFixed(1), "parallax (mas)": r[col.parallax]?.toFixed(2),
    })));
    S.aladin.addCatalog(S.gaia);
    status(`${g.rows.length} Gaia DR3 stars drawn (ESA archive).`);
  } catch (err) {
    status(offline(err) ? "Gaia needs the live server, which is not reachable right now." : "Gaia overlay failed: " + err.message);
    e.target.checked = false;
  }
};

// ---- wiring
$("modeSeg").onclick = (e) => { if (e.target.dataset.mode) { S.mode = e.target.dataset.mode; rebuildList(); show(0); syncURL(); } };
$("playBtn").onclick = play;
$("prevBtn").onclick = () => { stop(); show(S.i - 1); };
$("nextBtn").onclick = () => { stop(); show(S.i + 1); };
$("speed").oninput = () => { if (S.timer) { stop(); play(); } };
$("findSatBtn").onclick = findSatellites;
$("coordForm").onsubmit = (e) => { e.preventDefault(); loadView({ ra: +$("ra").value, dec: +$("dec").value, fov: +$("fov").value }); };
$("shareBtn").onclick = () => { navigator.clipboard?.writeText(location.href); status("Link copied."); };
document.addEventListener("keydown", (e) => {
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) || !S.data) return;
  if (e.key === " ") { e.preventDefault(); play(); }
  if (e.key === "ArrowRight") { stop(); show(S.i + 1); }
  if (e.key === "ArrowLeft") { stop(); show(S.i - 1); }
});

initAladin();
initReveal();
initCensus();
initTargets().then(() => {
  if (params.get("mode")) S.mode = params.get("mode");
  if (params.get("det")) S.det = +params.get("det");
  if (params.get("target")) loadView({ target: params.get("target") });
  else if (params.get("ra") && params.get("dec")) loadView({ ra: +params.get("ra"), dec: +params.get("dec"), fov: +(params.get("fov") || 0.12) });
});
