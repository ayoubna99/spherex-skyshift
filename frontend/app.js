// SkyShift frontend. Talks to the backend API described in docs/api.md.
const params = new URLSearchParams(location.search);
const API = (params.get("api") || window.SKYSHIFT_API || "http://localhost:8000").replace(/\/$/, "");

const BANDS = { 1: "0.75–1.09 µm", 2: "1.10–1.62 µm", 3: "1.63–2.41 µm", 4: "2.42–3.82 µm", 5: "3.83–4.41 µm", 6: "4.42–5.00 µm" };
const MODE_HINT = {
  stacks: "Each image is one SPHEREx sky pass (about every 6 months), stacked for clarity. Same brightness scale throughout, so changes are real. The target is at the centre marks.",
  diffs: "Later pass minus earlier pass. Orange = brighter now, blue = brighter before. A moving star shows up as a blue–orange pair.",
  frames: "Every single exposure, in time order. Pick one and ask which satellites crossed it.",
};
const S = { data: null, mode: "stacks", det: 1, list: [], i: 0, timer: null, view: null, aladin: null };
const $ = (id) => document.getElementById(id);

// ---------- helpers
async function api(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}
function status(msg, busy = false) { $("status").textContent = msg || ""; $("status").classList.toggle("busy", busy); }
function fmtDate(s) { return s ? s.slice(0, 10) : ""; }

// ---------- targets and URL
async function initTargets() {
  const box = $("targets");
  try {
    const ts = await api("/api/targets");
    ts.forEach((t) => {
      const b = document.createElement("button");
      b.textContent = t.name; b.title = t.note;
      b.onclick = () => loadView({ target: t.key });
      box.appendChild(b);
    });
  } catch (e) { status("Backend not reachable: " + e.message); }
  const sat = document.createElement("button");
  sat.textContent = "🛰 Satellite showcase"; sat.className = "special";
  sat.onclick = loadShowcase;
  box.appendChild(sat);
}
function syncURL() {
  const p = new URLSearchParams();
  if (S.view?.target) p.set("target", S.view.target);
  else if (S.view) { p.set("ra", S.view.ra); p.set("dec", S.view.dec); p.set("fov", S.view.fov); }
  p.set("mode", S.mode); p.set("det", S.det);
  if (params.get("api")) p.set("api", params.get("api"));
  history.replaceState(null, "", "?" + p.toString());
}

// ---------- sky map
function initAladin() {
  if (!window.A) return;
  A.init.then(() => {
    S.aladin = A.aladin("#aladin", { survey: "P/allWISE/color", fov: 0.5, showReticle: true, showLayersControl: false, showGotoControl: false, showFullscreenControl: false });
    S.overlay = A.graphicOverlay({ color: "#ffb347", lineWidth: 2 });
    S.aladin.addOverlay(S.overlay);
  });
}
function mapGoto(ra, dec, fovDeg) {
  if (!S.aladin) return;
  S.aladin.gotoRaDec(ra, dec); S.aladin.setFoV(Math.max(fovDeg * 3, 0.3));
  S.overlay.removeAll();
  const h = fovDeg / 2, c = Math.cos(dec * Math.PI / 180);
  S.overlay.add(A.polyline([[ra - h / c, dec - h], [ra + h / c, dec - h], [ra + h / c, dec + h], [ra - h / c, dec + h], [ra - h / c, dec - h]]));
}
function mapStreak(line) {
  if (!S.aladin) return;
  S.overlay.add(A.polyline(line, { color: "#ff5f5f", lineWidth: 3 }));
  const ra = (line[0][0] + line[1][0]) / 2, dec = (line[0][1] + line[1][1]) / 2;
  S.aladin.gotoRaDec(ra, dec); S.aladin.setFoV(4.5);
}

// ---------- loading a view
async function loadView(v) {
  stop();
  S.view = v; S.data = null; $("cards").innerHTML = "";
  const q = v.target ? `target=${v.target}` : `ra=${v.ra}&dec=${v.dec}&size_deg=${v.fov}`;
  $("title").textContent = "Loading…"; $("empty").hidden = false; $("empty").textContent = "Fetching SPHEREx images from NASA…";
  status("First time for a position takes 1–3 minutes; after that it is instant.", true);
  try {
    const d = await api("/api/frames?" + q);
    S.data = d;
    $("title").textContent = `${d.target.name || "Custom position"} · ${d.n_epochs} sky passes · ${d.frames.length} exposures`;
    $("ra").value = d.target.ra; $("dec").value = d.target.dec;
    status(`RA ${d.target.ra.toFixed(4)}°, Dec ${d.target.dec.toFixed(4)}° · field ${(d.target.size_deg * 60).toFixed(1)}′`);
    mapGoto(d.target.ra, d.target.dec, d.target.size_deg);
    buildDetButtons();
    rebuildList(); show(0); syncURL();
  } catch (e) {
    $("title").textContent = "Could not load"; status(e.message); $("empty").textContent = "Something went wrong: " + e.message;
  }
}
function buildDetButtons() {
  const seg = $("detSeg"); seg.innerHTML = "";
  for (let d = 1; d <= 6; d++) {
    const b = document.createElement("button");
    b.textContent = "D" + d; b.title = BANDS[d];
    const has = S.data.stacks.some((s) => s.detector === d);
    b.disabled = !has;
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
  if (S.mode === "stacks") S.list = S.data.stacks.filter((s) => s.detector === S.det);
  else if (S.mode === "diffs") S.list = S.data.diffs.filter((s) => s.detector === S.det);
  else S.list = S.data.frames.filter((s) => s.detector === S.det).sort((a, b) => a.mjd - b.mjd);
}
function label(item) {
  if (S.mode === "stacks") return `Pass ${item.epoch + 1} · ${item.date} · ${item.band} · ${item.n_frames} exposure(s)${item.streak ? " · 🛰 streak flagged" : ""}`;
  if (S.mode === "diffs") {
    const a = S.data.stacks.find((s) => s.epoch === item.from_epoch && s.detector === item.detector);
    const b = S.data.stacks.find((s) => s.epoch === item.to_epoch && s.detector === item.detector);
    return `${a?.date || "pass " + (item.from_epoch + 1)} → ${b?.date || "pass " + (item.to_epoch + 1)} · ${BANDS[item.detector]}`;
  }
  return `${fmtDate(item.date)} · ${item.wavelength_um ? item.wavelength_um.toFixed(2) + " µm" : item.band} · ${item.obs_id}${item.flags.streak ? " · 🛰 streak flagged" : ""}`;
}
function show(i) {
  if (!S.list.length) { $("frame").removeAttribute("src"); $("empty").hidden = false; $("empty").textContent = "No images for this detector."; $("frameInfo").textContent = ""; return; }
  S.i = (i + S.list.length) % S.list.length;
  const it = S.list[S.i];
  $("frame").src = API + "/" + it.png;
  $("empty").hidden = true;
  $("frameInfo").textContent = `${S.i + 1}/${S.list.length} · ${label(it)}`;
}
function play() {
  if (S.timer) return stop();
  $("playBtn").textContent = "❚❚ Pause";
  S.timer = setInterval(() => show(S.i + 1), +$("speed").value);
}
function stop() { clearInterval(S.timer); S.timer = null; $("playBtn").textContent = "▶ Play"; }

// ---------- satellites
async function findSatellites() {
  const it = S.list[S.i];
  if (!it || !it.url) return;
  $("findSatBtn").disabled = true;
  status("Analysing the full exposure in both cameras…", true);
  try {
    const r = await api(`/api/streaks?url=${encodeURIComponent(it.url)}&det=${it.detector}`);
    renderCards([r], `Exposure ${it.obs_id} (${fmtDate(it.date)})`);
    status(`${r.streaks.length} satellite(s) measured in this exposure.`);
  } catch (e) { status("Satellite search failed: " + e.message); }
  $("findSatBtn").disabled = false;
}
async function loadShowcase() {
  status("Loading validated satellite streaks…", true);
  try {
    const r = await api("/api/showcase");
    renderCards(r, "Satellites that crossed SPHEREx near Barnard's Star, August 2026");
    status("");
  } catch (e) { status("Showcase failed: " + e.message); }
}
function renderCards(results, heading) {
  const box = $("cards"); box.innerHTML = `<p class="small">${heading}</p>`;
  let n = 0;
  results.forEach((res) => res.streaks.forEach((c) => { n++; box.appendChild(card(c)); }));
  if (!n) box.innerHTML += `<p class="small">No satellite streak with enough signal in this exposure.</p>`;
}
function card(c) {
  const el = document.createElement("div"); el.className = "card";
  const T = c.temp_k ? `${c.temp_k} K` : "—";
  const range = c.temp_range_k ? `(${c.temp_range_k[0]}–${c.temp_range_k[1]} K)` : "(temperature needs the 3.8–5 µm cameras)";
  const r = (x) => (x == null ? "—" : x >= 10 ? x.toFixed(0) + "×" : x.toFixed(2) + "×");
  const sat = c.satellite;
  const who = sat
    ? `<div class="sat"><span class="satname">${esc(sat.name)}</span>
         <a href="https://www.n2yo.com/satellite/?s=${encodeURIComponent(sat.norad)}" target="_blank" rel="noopener">NORAD ${esc(sat.norad)}</a>
         <div class="small">${sat.distance_km.toLocaleString()} km from SPHEREx · orbiting ${sat.altitude_km.toLocaleString()} km up ·
         crossed the field at ${sat.time_utc.slice(11)} UTC in ${sat.crossing_s} s · track matches the streak to ${sat.offset_deg}°</div></div>`
    : `<div class="small">${c.catalog_checked ? "Not in the public satellite catalogue (or orbit too uncertain)" : "Satellite not named yet"}</div>`;
  el.innerHTML = `
    ${who}
    <div><span class="temp">${T}</span> <span class="small">${range} · about ${c.temp_k ? (c.temp_k - 273) + " °C" : "?"}</span></div>
    <div class="small">${fmtDate(c.date)} · cameras D${c.detectors[0]} + D${c.detectors[1]} · streak ${c.length_deg.toFixed(1)}° long</div>
    <div class="row">
      <figure><img src="${API}/${c.png_short}"><figcaption>reflected light, ${c.wavelength_short_um.toFixed(1)} µm</figcaption></figure>
      <figure><img src="${API}/${c.png_long}"><figcaption>heat glow, ${c.wavelength_long_um.toFixed(1)} µm</figcaption></figure>
    </div>
    <canvas width="560" height="320"></canvas>
    <table>
      <tr><td>Long / short camera, this satellite</td><td><b>${r(c.ratio_long_short)}</b></td></tr>
      <tr><td>Same ratio for ${c.n_stars} stars in these images</td><td>${r(c.ratio_stars)}</td></tr>
      <tr><td>If it only reflected sunlight</td><td>${r(c.ratio_reflected_only)}</td></tr>
    </table>`;
  el.onclick = () => mapStreak(c.line_sky);
  setTimeout(() => drawSpectrum(el.querySelector("canvas"), c), 0);
  return el;
}
function esc(t) { return String(t).replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m])); }
function bnu(l, T) { return Math.pow(l, -3) / (Math.exp(14387.77 / (l * T)) - 1); }
function drawSpectrum(cv, c) {
  const g = cv.getContext("2d"), W = cv.width, H = cv.height, pad = 46;
  const pts = c.spectrum.filter((p) => p.value > 0);
  if (!pts.length) return;
  const xs = (l) => pad + (l - 1.5) / (5.1 - 1.5) * (W - pad - 12);
  const vals = pts.map((p) => p.value);
  const lo = Math.log10(Math.min(...vals) / 3), hi = Math.log10(Math.max(...vals) * 2);
  const ys = (v) => H - 30 - (Math.log10(v) - lo) / (hi - lo) * (H - 50);
  g.fillStyle = "#93a0b8"; g.font = "18px system-ui"; g.fillText("brightness (log)", 8, 22); g.fillText("wavelength (µm)", W / 2 - 60, H - 6);
  [2, 3, 4, 5].forEach((l) => { g.fillText(l, xs(l) - 5, H - 26); });
  // reflected-sunlight-only line scaled to the short camera
  const sun = (l) => bnu(l, 5772);
  g.strokeStyle = "#5c6a86"; g.setLineDash([6, 6]); g.beginPath();
  for (let l = 1.5; l <= 5.1; l += 0.05) { const v = c.flux_short * sun(l) / sun(c.wavelength_short_um); const y = ys(v); l === 1.5 ? g.moveTo(xs(l), y) : g.lineTo(xs(l), y); }
  g.stroke(); g.setLineDash([]);
  if (c.temp_k) {
    g.strokeStyle = "#ffb347"; g.beginPath();
    for (let l = 3.6; l <= 5.1; l += 0.02) { const v = c.flux_long * bnu(l, c.temp_k) / bnu(c.wavelength_long_um, c.temp_k); const y = ys(v); l === 3.6 ? g.moveTo(xs(l), y) : g.lineTo(xs(l), y); }
    g.stroke();
  }
  pts.forEach((p) => { g.fillStyle = p.camera === "short" ? "#5fb3ff" : "#ffb347"; g.beginPath(); g.arc(xs(p.um), ys(p.value), 4, 0, 7); g.fill(); });
  g.fillStyle = "#93a0b8"; g.fillText("- - reflected sunlight only   — heat glow fit", W - 390, 22);
}

// ---------- wiring
$("modeSeg").onclick = (e) => { if (e.target.dataset.mode) { S.mode = e.target.dataset.mode; rebuildList(); show(0); syncURL(); } };
$("playBtn").onclick = play;
$("prevBtn").onclick = () => { stop(); show(S.i - 1); };
$("nextBtn").onclick = () => { stop(); show(S.i + 1); };
$("speed").oninput = () => { if (S.timer) { stop(); play(); } };
$("findSatBtn").onclick = findSatellites;
$("coordForm").onsubmit = (e) => { e.preventDefault(); loadView({ ra: +$("ra").value, dec: +$("dec").value, fov: +$("fov").value }); };
$("shareBtn").onclick = () => { navigator.clipboard?.writeText(location.href); status("Link copied."); };
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === " ") { e.preventDefault(); play(); }
  if (e.key === "ArrowRight") { stop(); show(S.i + 1); }
  if (e.key === "ArrowLeft") { stop(); show(S.i - 1); }
});

initAladin();
initTargets().then(() => {
  if (params.get("mode")) S.mode = params.get("mode");
  if (params.get("det")) S.det = +params.get("det");
  if (params.get("target")) loadView({ target: params.get("target") });
  else if (params.get("ra") && params.get("dec")) loadView({ ra: +params.get("ra"), dec: +params.get("dec"), fov: +(params.get("fov") || 0.12) });
  if (params.get("showcase")) loadShowcase();
});
