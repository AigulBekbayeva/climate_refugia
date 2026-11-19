/* Where Can Nature Survive Climate Change? — geoportal logic (vanilla JS + Leaflet) */
(() => {
  "use strict";

  const SCEN = {
    ssp126: { short: "SSP1-2.6", name: "low emissions" },
    ssp245: { short: "SSP2-4.5", name: "intermediate emissions" },
    ssp585: { short: "SSP5-8.5", name: "very high emissions" },
  };

  const MAPS = {
    m1: {
      kicker: "MAP 01 · WHERE WILL NATURE FIND REFUGE?",
      layers: [
        { id: "index", title: "Climate refugia index", sub: "Combined score: slow climate velocity, low novelty, high topographic shelter.", scen: true, file: s => `m1_index_${s}` },
        { id: "dt", title: "Projected warming", sub: "Change in mean annual land temperature, 2041–2060 vs 1995–2014.", scen: true, file: s => `m1_dt_${s}` },
        { id: "vel", title: "Climate velocity", sub: "How fast a given temperature moves across the landscape (km per year).", scen: true, file: s => `m1_vel_${s}` },
        { id: "relief", title: "Topographic diversity", sub: "Elevation range within ~55 km — slopes and valleys that offer micro-refugia.", scen: false, file: () => "m1_relief" },
      ],
    },
    m2: {
      kicker: "MAP 02 · WHICH SPECIES WILL HAVE A SAFE HAVEN?",
      layers: [
        { id: "biv", title: "Safe havens vs. biodiversity at risk", sub: "Vertebrate richness crossed with the climate refugia index.", scen: true, file: s => `m2_biv_${s}`, pixel: true },
        { id: "rich", title: "Vertebrate species richness", sub: "Amphibians, birds, mammals and reptiles per 0.5° cell.", scen: false, file: () => "m2_rich", pixel: true },
        { id: "thr", title: "Threatened species richness", sub: "Threatened amphibians, birds and mammals (IUCN).", scen: false, file: () => "m2_thr", pixel: true },
      ],
    },
  };

  const BIV_NAMES = [
    "Species-poor · exposed", "Species-poor · moderate refuge", "Species-poor · strong refuge",
    "Moderately rich · exposed", "Middle ground", "Moderately rich · strong refuge",
    "Biodiversity at risk", "Species-rich · moderate refuge", "Safe haven",
  ];

  const state = { map: "m1", scen: "ssp245", layer: { m1: "index", m2: "biv" }, hs: true, hsSort: "refuge", activeHs: null };
  let S = null, GRID = null, gridPromise = null;
  let map, overlay, hsLayer, hsLines, countries, probeMarker;

  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];
  const fmt = (v, d = 1) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(d));
  const css = (stops, dir = "90deg") => `linear-gradient(${dir}, ${stops.map(([t, c]) => `${c} ${t * 100}%`).join(", ")})`;

  // ------------------------------------------------------------ hash state
  function readHash() {
    const [m, s, l] = location.hash.replace("#", "").split("/");
    if (MAPS[m]) state.map = m;
    if (SCEN[s]) state.scen = s;
    if (l && MAPS[state.map].layers.some(x => x.id === l)) state.layer[state.map] = l;
  }
  function writeHash() {
    history.replaceState(null, "", `#${state.map}/${state.scen}/${state.layer[state.map]}`);
  }

  // ------------------------------------------------------------ map
  function initMap() {
    map = L.map("map", {
      zoomControl: false, worldCopyJump: false, minZoom: window.innerWidth < 700 ? 1 : 2, maxZoom: 8,
      maxBounds: [[-75, -230], [88, 230]], maxBoundsViscosity: 0.8, attributionControl: true,
      zoomSnap: 0.25,
    });
    L.control.zoom({ position: "topright" }).addTo(map);
    map.attributionControl.setPrefix(false);
    map.attributionControl.addAttribution("Data: IPCC AR6 Atlas, CMIP6, ERA-Interim, ETOPO5, IUCN, BirdLife, GARD, CI · Basemap &amp; labels: Natural Earth");

    for (const [name, z] of [["land", 200], ["raster", 300], ["borders", 350], ["labels", 380], ["hotspots", 420]]) {
      map.createPane(name).style.zIndex = z;
    }
    map.getPane("labels").style.pointerEvents = "none";
    map.getPane("borders").style.pointerEvents = "none";
    homeView();

    // graticule
    const g = [];
    for (let lat = -60; lat <= 80; lat += 20) g.push([[lat, -180], [lat, 180]]);
    for (let lon = -180; lon <= 180; lon += 30) g.push([[-60, lon], [85, lon]]);
    L.polyline(g, { pane: "land", color: "#1a2a3a", weight: 0.6, interactive: false }).addTo(map);

    map.on("click", e => probe(e.latlng));
  }

  // ------------------------------------------------------------ labels (Natural Earth, drawn locally — no tile service)
  let LABELS = [], labelLayer = null;
  function initLabels(list) {
    LABELS = list
      .map(([t, name, lat, lon, z]) => ({ t, name, lat, lon, z }))
      .sort((a, b) => (a.t === b.t ? a.z - b.z : a.t === "c" ? -1 : 1));
    labelLayer = L.layerGroup().addTo(map);
    map.on("zoomend moveend", drawLabels);
    drawLabels();
  }
  function drawLabels() {
    if (!labelLayer) return;
    labelLayer.clearLayers();
    const z = map.getZoom(), b = map.getBounds().pad(0.05), boxes = [];
    for (const l of LABELS) {
      const need = l.t === "c" ? l.z - 0.6 : l.z - 0.2;
      if (z < need || !b.contains([l.lat, l.lon])) continue;
      const pt = map.latLngToContainerPoint([l.lat, l.lon]);
      const fs = l.t === "c" ? (z >= 4 ? 12 : 10.5) : 11;
      const w = l.name.length * fs * (l.t === "c" ? 0.68 : 0.56) + 10, h = fs + 6;
      const box = l.t === "c" ? [pt.x - w / 2, pt.y - h / 2, pt.x + w / 2, pt.y + h / 2] : [pt.x - 4, pt.y - h / 2, pt.x + w, pt.y + h / 2];
      if (boxes.some(o => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1])) continue;
      boxes.push(box);
      const html = l.t === "c" ? `<span class="lbl-c" style="font-size:${fs}px">${l.name}</span>` : `<span class="lbl-p"><i></i>${l.name}</span>`;
      labelLayer.addLayer(L.marker([l.lat, l.lon], {
        pane: "labels", interactive: false, keyboard: false,
        icon: L.divIcon({ className: "lbl", html, iconSize: null, iconAnchor: l.t === "c" ? [w / 2, h / 2] : [3, h / 2] }),
      }));
      if (boxes.length > 260) break;
    }
  }

  function homeView() {
    const w = map.getSize().x;
    map.fitBounds([[-50, -165], [78, 180]], { animate: false, padding: [0, w > 900 ? 20 : 0] });
  }

  function addBasemap(cty) {
    countries = cty;
    L.geoJSON(cty, { pane: "land", interactive: false, style: { stroke: false, fillColor: "#18242f", fillOpacity: 1 } }).addTo(map);
    L.geoJSON(cty, { pane: "borders", interactive: false, style: { color: "#0b141c", weight: 0.7, opacity: 0.85, fill: false } }).addTo(map);
  }

  function addHotspots(hs) {
    const casing = L.geoJSON(hs, { pane: "hotspots", interactive: false, style: { color: "#08121a", weight: 3.6, opacity: 0.55, fill: false } });
    const lines = L.geoJSON(hs, {
      pane: "hotspots",
      style: () => hsStyle(false),
      onEachFeature: (f, lyr) => {
        lyr.bindTooltip(f.properties.name, { className: "hs-tip", sticky: true, direction: "top", offset: [0, -6] });
        lyr.on("mouseover", () => lyr.setStyle(hsStyle(true)));
        lyr.on("mouseout", () => lyr.setStyle(hsStyle(state.activeHs === f.properties.id)));
        lyr.on("click", e => { L.DomEvent.stopPropagation(e); selectHotspot(f.properties.id, false); probe(e.latlng); });
      },
    });
    hsLines = lines;
    hsLayer = L.layerGroup([casing, lines]);
  }
  const hsStyle = on => ({ color: on ? "#ffe08a" : "#ffffff", weight: on ? 2.4 : 1.4, opacity: on ? 1 : 0.9, fillColor: "#ffffff", fillOpacity: on ? 0.1 : 0.02 });

  function setOverlay() {
    const L_ = MAPS[state.map].layers.find(x => x.id === state.layer[state.map]);
    const url = `data/layers/${L_.file(state.scen)}.png`;
    const next = L.imageOverlay(url, S.bounds, { pane: "raster", opacity: 0, className: L_.pixel ? "pixel" : "", interactive: false }).addTo(map);
    const prev = overlay;
    overlay = next;
    next.once("load", () => {
      fade(next, 0, 1);
      if (prev) fade(prev, 1, 0, () => map.removeLayer(prev));
    });
    const showHs = state.map === "m2" && state.hs;
    if (hsLayer) { if (showHs) hsLayer.addTo(map); else map.removeLayer(hsLayer); }
  }
  function fade(layer, a, b, done) {
    const t0 = performance.now(), dur = 280;
    const step = t => {
      const k = Math.min(1, (t - t0) / dur);
      layer.setOpacity(a + (b - a) * k);
      if (k < 1) requestAnimationFrame(step); else done && done();
    };
    requestAnimationFrame(step);
  }

  // ------------------------------------------------------------ panel UI
  function renderLayers() {
    for (const m of ["m1", "m2"]) {
      $(`#${m}-layers`).innerHTML = MAPS[m].layers.map(l => `
        <button class="layer" role="radio" data-layer="${l.id}" aria-checked="${state.layer[m] === l.id}">
          <span class="radio"></span><span><span class="t">${l.title}</span><br><span class="s">${l.sub}</span></span>
        </button>`).join("");
    }
  }

  function legendHTML() {
    const lay = state.layer[state.map], P = S.palettes, R = S.ranges;
    if (lay === "index") {
      const cols = [0.1, 0.3, 0.5, 0.7, 0.9].map(t => sampleRamp(P.index, t));
      return `<div class="lt">Refugia potential, ${SCEN[state.scen].short}</div>
        <div class="classes">${cols.map(c => `<div style="background:${c}"></div>`).join("")}</div>
        <div class="cnames"><span>Highly exposed</span><span></span><span>Transitional</span><span></span><span>Strong refugium</span></div>
        <div class="note">Five classes with fixed thresholds (SSP2-4.5 quintiles), so the share of each colour changes with the scenario.</div>`;
    }
    if (lay === "dt") return `<div class="lt">Warming by 2041–2060, °C</div><div class="bar" style="background:${css(P.dt)}"></div>
        <div class="ticks"><span>≤ ${R.dt[0]}</span><span>${(R.dt[0] + R.dt[1]) / 2}</span><span>≥ ${R.dt[1]}</span></div>
        <div class="note">CMIP6 multi-model median per IPCC AR6 land region; global land mean ${fmt(S.headline[state.scen].land_dT)} °C.</div>`;
    if (lay === "vel") return `<div class="lt">Climate velocity, km / year (log scale)</div><div class="bar" style="background:${css(P.vel)}"></div>
        <div class="ticks"><span>≤ 0.03</span><span>0.3</span><span>3</span><span>≥ 30</span></div>
        <div class="note">Darker = isotherms barely move → species can track their climate within a short distance.</div>`;
    if (lay === "relief") return `<div class="lt">Topographic diversity — elevation range, m</div><div class="bar" style="background:${css(P.relief)}"></div>
        <div class="ticks"><span>0</span><span>50</span><span>500</span><span>≥ 3 000</span></div>`;
    if (lay === "rich") return `<div class="lt">Vertebrate species per 0.5° cell</div><div class="bar" style="background:${css(P.rich)}"></div>
        <div class="ticks"><span>0</span><span>${Math.round(R.rich_max / 2)}</span><span>≥ ${Math.round(R.rich_max)}</span></div>
        <div class="note">${S.species_counts.amphibians.toLocaleString()} amphibians · ${S.species_counts.birds.toLocaleString()} birds · ${S.species_counts.mammals.toLocaleString()} mammals · ${S.species_counts.reptiles.toLocaleString()} reptiles.</div>`;
    if (lay === "thr") return `<div class="lt">Threatened species per 0.5° cell</div><div class="bar" style="background:${css(P.thr)}"></div>
        <div class="ticks"><span>1</span><span>${Math.round(R.thr_max / 4)}</span><span>≥ ${Math.round(R.thr_max)}</span></div>
        <div class="note">${S.threatened_species.toLocaleString()} threatened amphibians, birds and mammals (square-root scale).</div>`;
    if (lay === "biv") {
      const C = S.biv_colors;
      let cells = "";
      for (let r = 2; r >= 0; r--) for (let c = 0; c <= 2; c++) cells += `<div style="background:${C[r * 3 + c]}" title="${BIV_NAMES[r * 3 + c]}"></div>`;
      return `<div class="lt">Richness × refugia, ${SCEN[state.scen].short}</div>
        <div class="biv">
          <div class="biv-grid-wrap"><div class="biv-ax-y">More species →</div><div class="biv-grid">${cells}</div><div class="biv-ax-x">More refuge →</div></div>
          <div class="biv-keys">
            <div><i style="background:${C[8]}"></i><span><b>Safe haven</b><br>species-rich and climatically stable</span></div>
            <div><i style="background:${C[6]}"></i><span><b>Biodiversity at risk</b><br>species-rich but highly exposed</span></div>
            <div><i style="background:${C[2]}"></i><span><b>Refuge, fewer species</b><br>stable land that could receive migrants</span></div>
          </div>
        </div>`;
    }
    return "";
  }

  function sampleRamp(stops, t) {
    for (let i = 1; i < stops.length; i++) {
      if (t <= stops[i][0]) {
        const [t0, c0] = stops[i - 1], [t1, c1] = stops[i], k = (t - t0) / (t1 - t0);
        const a = hex(c0), b = hex(c1);
        return `rgb(${a.map((v, j) => Math.round(v + (b[j] - v) * k)).join(",")})`;
      }
    }
    return stops[stops.length - 1][1];
  }
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));

  function tile(v, unit, desc, delta, dot) {
    return `<div class="tile"><div class="v">${v}<small>${unit}</small></div><div class="d">${dot ? `<span class="dot" style="background:${dot}"></span>` : ""}${desc}</div>${delta ? `<div class="delta">${delta}</div>` : ""}</div>`;
  }
  function across(key, d = 0, unit = "%") {
    return `<span class="sc3">${Object.keys(SCEN).map(s => `<span class="${s === state.scen ? "cur" : ""}"><em>${SCEN[s].short.replace("SSP", "")}</em>${fmt(S.headline[s][key], d)}${unit}</span>`).join("")}</span>`;
  }

  function renderTiles() {
    const h = S.headline[state.scen];
    $("#m1-tiles").innerHTML =
      tile(fmt(h.refugia_pct, 0), "%", "of ice-free land keeps moderate-to-strong refugium potential", across("refugia_pct"), "#3aa7a0") +
      tile(fmt(h.exposed_pct, 0), "%", "of land is exposed or highly exposed", across("exposed_pct"), "#e0714f") +
      tile(`+${fmt(h.land_dT)}`, "°C", `land warming by 2050 (${h.models} CMIP6 models, median)`) +
      tile(fmt(h.median_velocity), " km/yr", "median speed at which climate zones shift across land");
    const nHs = S.hotspots.length;
    const majority = S.hotspots.filter(x => x[`refuge_${state.scen}`] >= 50).length;
    $("#m2-tiles").innerHTML =
      tile(fmt(h.rich_in_refuge_pct, 0), "%", "of the most species-rich third of land is a safe haven", across("rich_in_refuge_pct"), S.biv_colors[8]) +
      tile(fmt(h.rich_exposed_pct, 0), "%", "of the most species-rich land is highly exposed — biodiversity at risk", across("rich_exposed_pct"), S.biv_colors[6]) +
      tile(`${majority}<small> / ${nHs}</small>`, "", "biodiversity hotspots keep refugium potential on more than half their land") +
      tile((S.species_counts.amphibians + S.species_counts.birds + S.species_counts.mammals + S.species_counts.reptiles).toLocaleString("en-US"), "", "vertebrate species ranges analysed");
  }

  function renderRank() {
    const r = S.regions[state.scen];
    const top = r.slice(0, 7), low = r.slice(-4);
    const row = (x, cls = "") => `<div class="row ${cls}" title="${x.name.replace(/-/g, " ")}: warming +${x.dT} °C"><span class="n">${x.name.replace(/-/g, " ").replace(/\./g, ". ")}</span><span class="track"><span class="fill" style="width:${x.refuge_pct}%"></span></span><span class="p">${fmt(x.refuge_pct, 0)}%</span></div>`;
    $("#m1-rank").innerHTML = top.map(x => row(x)).join("") + `<div class="sep"><span>LEAST REFUGE</span></div>` + low.map(x => row(x, "low")).join("");
  }

  function renderHotspots() {
    const s = state.scen, k = state.hsSort;
    const rows = [...S.hotspots].sort((a, b) => k === "richness" ? b.richness - a.richness : k === "exposed" ? b[`exposed_${s}`] - a[`exposed_${s}`] : b[`refuge_${s}`] - a[`refuge_${s}`]);
    $("#hs-table").innerHTML = `<div class="hs-head"><span>Hotspot</span><span>Refuge · exposed</span><span>Refuge</span></div>` +
      rows.map(h => {
        const r = h[`refuge_${s}`], e = h[`exposed_${s}`];
        return `<button class="hs-row ${state.activeHs === h.id ? "active" : ""}" data-hs="${h.id}" title="${h.name} — ~${h.richness} vertebrate species per cell, warming +${h[`dT_${s}`]} °C">
          <span class="n">${h.name}</span>
          <span class="stack"><i class="r" style="width:${r}%"></i><i class="m"></i><i class="e" style="width:${e}%"></i></span>
          <span class="nums"><b>${fmt(r, 0)}%</b> · ${fmt(e, 0)}%</span></button>`;
      }).join("");
  }

  function renderTitle() {
    const L_ = MAPS[state.map].layers.find(x => x.id === state.layer[state.map]);
    $("#map-title").innerHTML = `<div class="a">${MAPS[state.map].kicker}</div><div class="b">${L_.title}</div><div class="c">${L_.scen ? `${SCEN[state.scen].short} (${SCEN[state.scen].name}) · 2041–2060` : "Present day"}</div>`;
  }

  function renderAll() {
    document.body.dataset.map = state.map;
    $$(".tab").forEach(b => b.setAttribute("aria-selected", b.dataset.map === state.map));
    $("#sec-m1").hidden = state.map !== "m1";
    $("#sec-m2").hidden = state.map !== "m2";
    $$(".seg button").forEach(b => b.setAttribute("aria-checked", b.dataset.scen === state.scen));
    $$(".hs-sort button").forEach(b => b.setAttribute("aria-checked", b.dataset.sort === state.hsSort));
    $$("[data-scen-label]").forEach(el => (el.textContent = `· ${SCEN[state.scen].short}`));
    renderLayers();
    $(`#${state.map}-legend`).innerHTML = legendHTML();
    renderTiles();
    renderRank();
    renderHotspots();
    renderTitle();
    setOverlay();
    writeHash();
    if (lastProbe) probe(lastProbe, true);
  }

  // ------------------------------------------------------------ hotspots
  function selectHotspot(id, fly = true) {
    state.activeHs = id;
    if (hsLines) hsLines.eachLayer(l => l.setStyle(hsStyle(l.feature.properties.id === id)));
    if (fly && hsLines) {
      const lyr = hsLines.getLayers().find(l => l.feature.properties.id === id);
      if (lyr) {
        const b = lyr.getBounds();
        map.flyToBounds(b, { padding: [60, 60], maxZoom: 5, duration: 0.9 });
        const pt = lyr.feature.properties.pt;
        map.once("moveend", () => probe(pt ? L.latLng(pt[0], pt[1]) : b.getCenter(), false, id));
      }
    }
    renderHotspots();
  }

  // ------------------------------------------------------------ probe / read-out
  let lastProbe = null;
  function loadGrid() {
    if (!gridPromise) {
      gridPromise = fetch("data/grid.json").then(r => r.json()).then(g => {
        for (const [k, f] of Object.entries(g.fields)) {
          const bin = Uint8Array.from(atob(f.d), c => c.charCodeAt(0));
          f.a = f.t === "u16" ? new Uint16Array(bin.buffer) : bin;
          delete f.d;
        }
        GRID = g;
        return g;
      });
    }
    return gridPromise;
  }

  function cellVal(name, i) {
    const f = GRID.fields[name];
    const v = f.a[i];
    if (f.t === "u8" ? v === 255 : v === 65535) return null;
    return v / f.scale + f.off;
  }

  function classOf(idx) {
    const b = S.class_breaks;
    let c = 0;
    while (c < 4 && idx >= b[c]) c++;
    return c;
  }

  function pointInRing(pt, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if (((yi > pt[1]) !== (yj > pt[1])) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function countryAt(lon, lat) {
    if (!countries) return null;
    for (const f of countries.features) {
      const g = f.geometry; if (!g) continue;
      const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
      for (const p of polys) if (pointInRing([lon, lat], p[0])) return f.properties.n;
    }
    return null;
  }

  async function probe(latlng, silent = false, forceHs = null) {
    $("#map-hint").classList.add("gone");
    await loadGrid();
    let lon = ((latlng.lng + 180) % 360 + 360) % 360 - 180, lat = latlng.lat;
    const r = Math.floor((GRID.lat0 - lat) / GRID.res), c = Math.floor((lon + 180) / GRID.res);
    const box = $("#probe");
    if (r < 0 || r >= GRID.ny || c < 0 || c >= GRID.nx) { if (!silent) box.hidden = true; return; }
    const i = r * GRID.nx + c;
    const reg = cellVal("region", i);
    if (reg == null) {
      if (!silent) { box.hidden = true; if (probeMarker) map.removeLayer(probeMarker); lastProbe = null; }
      return;
    }
    lastProbe = latlng;
    const s = state.scen;
    const hsId = forceHs != null ? forceHs : cellVal("hotspot", i);
    const country = countryAt(lon, lat);
    const regName = GRID.regions[reg] ? GRID.regions[reg].name.replace(/-/g, " ").replace(/\./g, ". ") : "";
    const idx = cellVal(`index_${s}`, i), cls = idx == null ? null : classOf(idx);
    const clsColor = cls == null ? "#555" : sampleRamp(S.palettes.index, cls * 0.2 + 0.1);
    const rich = ["amphibians", "birds", "mammals", "reptiles"].map(k => cellVal(`rich_${k}`, i) || 0);
    const tot = rich.reduce((a, b) => a + b, 0);
    const biv = cellVal(`biv_${s}`, i);
    const lv = cellVal(`lvel_${s}`, i);
    const vel = lv == null ? null : Math.pow(10, lv);
    const latS = `${Math.abs(lat).toFixed(2)}° ${lat >= 0 ? "N" : "S"}`, lonS = `${Math.abs(lon).toFixed(2)}° ${lon >= 0 ? "E" : "W"}`;

    let badge;
    if (state.map === "m2" && biv != null) badge = `<span class="badge"><i style="background:${S.biv_colors[biv]}"></i>${BIV_NAMES[biv]}</span>`;
    else badge = `<span class="badge"><i style="background:${clsColor}"></i>${cls == null ? "No data" : S.class_names[cls]}</span>`;

    const scenRow = Object.keys(SCEN).map(k => {
      const v = cellVal(`index_${k}`, i);
      return `<div class="${k === s ? "cur" : ""}">${SCEN[k].short}<b>${v == null ? "—" : ["Very exposed", "Exposed", "Transitional", "Moderate", "Strong"][classOf(v)]}</b></div>`;
    }).join("");

    box.innerHTML = `<button class="close" aria-label="Close">×</button>
      <div class="loc">${latS}, ${lonS}${regName ? ` · ${regName}` : ""}</div>
      <div class="ttl">${hsId != null ? `${GRID.hotspots[hsId]} <span style="color:var(--ink-3);font-weight:500;font-size:12px">hotspot</span>` : country || regName || "Land cell"}</div>
      ${badge}
      <dl>
        <dt>Refugia index</dt><dd>${idx == null ? "—" : fmt(idx, 2)} / 1</dd>
        <dt>Warming 2041–60</dt><dd>+${fmt(cellVal(`dt_${s}`, i))} °C</dd>
        <dt>Climate velocity</dt><dd>${vel == null ? "—" : vel < 1 ? fmt(vel, 2) : fmt(vel, 1)} km/yr</dd>
        <dt>Elevation range</dt><dd>${(cellVal("relief", i) || 0).toLocaleString("en-US")} m</dd>
        <span class="div"></span>
        <dt>Vertebrate species</dt><dd>${tot ? tot.toLocaleString("en-US") : "—"}</dd>
        ${tot ? `<dt style="font-size:11.5px" title="amphibians · birds · mammals · reptiles">Amph · Bird · Mam · Rep</dt><dd style="font-size:11.5px;color:var(--ink-2)">${rich.join(" · ")}</dd>` : ""}
        <dt>Threatened species</dt><dd>${cellVal("thr", i) ?? "—"}</dd>
      </dl>
      <div class="scen-row">${scenRow}</div>`;
    box.hidden = false;
    box.querySelector(".close").onclick = () => { box.hidden = true; lastProbe = null; if (probeMarker) map.removeLayer(probeMarker); };

    if (probeMarker) map.removeLayer(probeMarker);
    probeMarker = L.circleMarker(latlng, { radius: 6, color: "#fff", weight: 2, fillColor: clsColor, fillOpacity: 1, pane: "hotspots", interactive: false }).addTo(map);
  }

  // ------------------------------------------------------------ events
  function bind() {
    $$(".tab").forEach(b => b.addEventListener("click", () => { state.map = b.dataset.map; renderAll(); $(".scroll").scrollTop = 0; }));
    $$(".seg button").forEach(b => b.addEventListener("click", () => { state.scen = b.dataset.scen; renderAll(); }));
    document.addEventListener("click", e => {
      const lb = e.target.closest(".layer");
      if (lb) { state.layer[state.map] = lb.dataset.layer; renderAll(); return; }
      const hr = e.target.closest(".hs-row");
      if (hr) { selectHotspot(+hr.dataset.hs, true); return; }
      const sb = e.target.closest(".hs-sort button");
      if (sb) { state.hsSort = sb.dataset.sort; $$(".hs-sort button").forEach(x => x.setAttribute("aria-checked", x === sb)); renderHotspots(); return; }
      const ib = e.target.closest(".info-btn");
      if (ib) { const p = document.getElementById(ib.dataset.popover); p.hidden = !p.hidden; }
    });
    $("#hs-toggle").addEventListener("change", e => { state.hs = e.target.checked; setOverlay(); });
    $("#btn-home").addEventListener("click", homeView);
    $("#btn-panel").addEventListener("click", () => {
      document.body.classList.toggle("panel-closed");
      setTimeout(() => map.invalidateSize(), 320);
    });
    document.addEventListener("keydown", e => {
      if (e.target.closest("input, textarea")) return;
      if (e.key === "1") { state.map = "m1"; renderAll(); }
      if (e.key === "2") { state.map = "m2"; renderAll(); }
    });
  }

  // ------------------------------------------------------------ boot
  async function boot() {
    readHash();
    initMap();
    const [stats, cty, hs, lbl] = await Promise.all([
      fetch("data/stats.json").then(r => r.json()),
      fetch("data/countries.geojson").then(r => r.json()),
      fetch("data/hotspots.geojson").then(r => r.json()),
      fetch("data/labels.json").then(r => r.json()),
    ]);
    S = stats;
    addBasemap(cty);
    addHotspots(hs);
    initLabels(lbl);
    bind();
    renderAll();
    setTimeout(loadGrid, 800);
  }
  boot();
})();
