const LAKE_ERIE_BOUNDS = [-83.6, 41.25, -78.7, 43.2];

const BASEMAP_IDS = ["satellite", "dark", "osm"];

const COLORMAPS = {
  gray: "linear-gradient(90deg, #000, #fff)",
  turbo: "linear-gradient(90deg, #30123b, #466be3, #1ae4b6, #a4fc3b, #faba39, #e4450a, #7a0403)",
  ylgn: "linear-gradient(90deg, #ffffe5, #c2e699, #78c679, #238443, #004529)",
  ylgnbu: "linear-gradient(90deg, #ffffd9 0%, #c7e9b4 25%, #41b6c4 50%, #225ea8 75%, #081d58 100%)",
  viridis:
    "linear-gradient(90deg, #440154 0%, #3b528b 25%, #21918c 50%, #5ec962 75%, #fde725 100%)",
  inferno:
    "linear-gradient(90deg, #000004 0%, #420a68 20%, #932667 45%, #dd513a 70%, #fca50a 86%, #fcffa4 100%)",
};

const state = {
  granule: null,
  layers: [],
  active: null,
  opacity: 85,
  visible: true,
  sampleSequence: 0,
};

const style = {
  version: 8,
  sources: {
    satellite: {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      attribution: "Esri, Maxar, Earthstar Geographics",
    },
    dark: {
      type: "raster",
      tiles: ["https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "CARTO, OpenStreetMap contributors",
    },
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "OpenStreetMap contributors",
    },
  },
  layers: [
    {
      id: "satellite",
      type: "raster",
      source: "satellite",
      layout: { visibility: "visible" },
      paint: { "raster-fade-duration": 0 },
    },
    {
      id: "dark",
      type: "raster",
      source: "dark",
      layout: { visibility: "none" },
      paint: { "raster-fade-duration": 0 },
    },
    {
      id: "osm",
      type: "raster",
      source: "osm",
      layout: { visibility: "none" },
      paint: { "raster-fade-duration": 0 },
    },
  ],
};

const map = new maplibregl.Map({
  container: "map",
  style,
  center: [-81.2, 42.2],
  zoom: 6,
  attributionControl: true,
});

map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");

const elements = {
  scene: document.getElementById("scene"),
  loading: document.getElementById("loading"),
  error: document.getElementById("error"),
  content: document.getElementById("content"),
  layers: document.getElementById("layers"),
  basemap: document.getElementById("basemap"),
  opacity: document.getElementById("opacity"),
  opacityValue: document.getElementById("opacity-value"),
  legendTitle: document.getElementById("legend-title"),
  legendNote: document.getElementById("legend-note"),
  legendBar: document.getElementById("legend-bar"),
  legendMin: document.getElementById("legend-min"),
  legendMid: document.getElementById("legend-mid"),
  legendMax: document.getElementById("legend-max"),
  coverageNote: document.getElementById("coverage-note"),
  metadata: document.getElementById("metadata"),
  readout: document.getElementById("readout"),
  crs: document.getElementById("crs"),
  fitLake: document.getElementById("fit-lake"),
  fitStudy: document.getElementById("fit-study"),
  visible: document.getElementById("overlay-visible"),
};

map.on("load", () => {
  init().catch(showError);
});

async function init() {
  const [granule, layers] = await Promise.all([
    fetchJSON("/api/granule"),
    fetchJSON("/api/layers"),
  ]);

  state.granule = granule;
  state.layers = layers;
  state.active = granule.default_layer || layers[0]?.id || null;

  const moisture = granule.product === "SME2";
  const lake = granule.product === "LAKE_ERIE";
  document.querySelector("h1").textContent = lake ? "Lake Erie" : moisture ? "Lake Erie · Soil Moisture" : "GCOV Backscatter";
  document.title = lake ? "Lake Erie · Satellite observations" : moisture ? "Lake Erie · Soil Moisture" : "NISAR GCOV";
  document.getElementById("product-label").textContent = lake ? "NOAA · NASA NISAR" : moisture ? "SME2 · m³/m³" : "HH · HV · false color";
  elements.scene.textContent = lake ? "Radar imagery · water temperature · bloom detections" : granule.scene || "NISAR observation";
  document.querySelector(".eyebrow").textContent = lake ? "Satellite observations · Lake Erie" : "NASA NISAR · L-band SAR";
  elements.fitStudy.textContent = lake ? "Zoom to layer" : "Zoom to study area";
  elements.crs.textContent = `${granule.crs || "EPSG:32617"} · ${granule.output_spacing_m?.toFixed(2) ?? "?"} m`;
  elements.coverageNote.textContent = granule.coverage_note || "";
  elements.coverageNote.hidden = !granule.coverage_note;

  renderMetadata(granule);
  renderLayerButtons(layers);
  addNisarLayers(layers, granule);
  addFootprint(granule);
  addAoi(granule);
  addToronto(granule);
  addLakeOutline(granule);
  bindControls();
  map.on("error", (event) => {
    if (event.sourceId === `nisar-${state.active}`) {
      elements.readout.textContent = "Could not load this overlay. Refresh the page to retry.";
    }
  });
  setActiveLayer(state.active);
  fitBounds(moisture || lake ? (granule.overview_bbox || LAKE_ERIE_BOUNDS) : granule.bounds_wgs84, { duration: 0 });
  if (!layers.length) {
    elements.legendTitle.textContent = "Soil moisture · m³/m³ · dry → wet";
    elements.legendBar.style.background = COLORMAPS.ylgnbu;
    elements.legendMin.textContent = "0.0";
    elements.legendMid.textContent = "0.3";
    elements.legendMax.textContent = "0.6";
    elements.readout.textContent = "No usable observation. Click the map to inspect a location.";
    elements.visible.disabled = true;
    elements.opacity.disabled = true;
    elements.layers.textContent = "No usable observation";
  }

  elements.loading.hidden = true;
  elements.content.hidden = false;
}

function addNisarLayers(layers, granule) {
  for (const layer of layers) {
    const sourceId = `nisar-${layer.id}`;
    map.addSource(sourceId, {
      type: "raster",
      tiles: [layer.tile_url],
      tileSize: layer.tile_size || 256,
      minzoom: 0,
      maxzoom: 14,
      bounds: layer.bounds_wgs84 || granule.bounds_wgs84,
      attribution: layer.source || `NASA NISAR ${granule.product} · ASF DAAC`,
    });
    map.addLayer({
      id: sourceId,
      type: "raster",
      source: sourceId,
      layout: { visibility: "none" },
      paint: {
        "raster-opacity": state.opacity / 100,
        "raster-fade-duration": 0,
        "raster-resampling": "nearest",
      },
    });
  }
}

function addLakeOutline(granule) {
  if (!granule.lake_outline) return;
  map.addSource("lake-outline", { type: "geojson", data: granule.lake_outline });
  map.addLayer({
    id: "lake-outline-line", type: "line", source: "lake-outline",
    paint: { "line-color": "#7ae4ff", "line-width": 1.5, "line-opacity": 0.9 },
  });
}

function addFootprint(granule) {
  if (!granule.footprint?.length) {
    return;
  }
  map.addSource("nisar-footprint", {
    type: "geojson",
    data: {
      type: "Feature",
      properties: {},
      geometry: { type: "Polygon", coordinates: [granule.footprint] },
    },
  });
  map.addLayer({
    id: "nisar-footprint-fill",
    type: "fill",
    source: "nisar-footprint",
    paint: { "fill-color": "#39d0ff", "fill-opacity": 0.035 },
  });
  map.addLayer({
    id: "nisar-footprint-line",
    type: "line",
    source: "nisar-footprint",
    paint: {
      "line-color": "#39d0ff",
      "line-width": 1.4,
      "line-opacity": 0.9,
      "line-dasharray": [2, 1],
    },
  });
}

function addAoi(granule) {
  const bbox = granule.aoi_bbox;
  if (!bbox) {
    return;
  }
  const [west, south, east, north] = bbox;
  const ring = [
    [west, south],
    [east, south],
    [east, north],
    [west, north],
    [west, south],
  ];
  map.addSource("aoi-outline", {
    type: "geojson",
    data: {
      type: "Feature",
      properties: {},
      geometry: { type: "Polygon", coordinates: [ring] },
    },
  });
  map.addLayer({
    id: "aoi-outline-line",
    type: "line",
    source: "aoi-outline",
    paint: {
      "line-color": "#ffc266",
      "line-width": 1.8,
      "line-opacity": 0.95,
      "line-dasharray": [1, 1],
    },
  });
}

function addToronto(granule) {
  if (granule.aoi_name !== "toronto") {
    return;
  }
  new maplibregl.Marker({ color: "#ff6b6b" })
    .setLngLat([-79.3832, 43.6532])
    .setPopup(
      new maplibregl.Popup({ offset: 18 }).setHTML(
        "<strong>Toronto</strong><br />Outside this granule&apos;s swath",
      ),
    )
    .addTo(map);
}

function renderLayerButtons(layers) {
  elements.layers.replaceChildren();
  for (const layer of layers) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.layer = layer.id;
    button.textContent = state.granule.product === "LAKE_ERIE" ? layer.label : layer.id === "soil_moisture" ? "Soil moisture" : layer.id.toUpperCase();
    button.title = layer.label;
    button.addEventListener("click", () => {
      state.visible = true;
      elements.visible.checked = true;
      setActiveLayer(layer.id);
      if (state.granule.product === "LAKE_ERIE") {
        fitBounds(layer.valid_bounds_wgs84 || LAKE_ERIE_BOUNDS);
      }
    });
    elements.layers.append(button);
  }
}

function setActiveLayer(layerId) {
  state.sampleSequence += 1;
  elements.readout.textContent = "Click the lake to sample this layer.";
  state.active = layerId;
  for (const layer of state.layers) {
    const id = `nisar-${layer.id}`;
    if (map.getLayer(id)) {
      map.setLayoutProperty(id, "visibility", state.visible && layer.id === layerId ? "visible" : "none");
    }
  }
  for (const button of elements.layers.querySelectorAll("button")) {
    button.classList.toggle("active", button.dataset.layer === layerId);
  }
  updateLegend();
  if (state.granule.product === "LAKE_ERIE") {
    const layer = state.layers.find((item) => item.id === layerId);
    renderLakeMetadata(layer);
  }
}

function updateLegend() {
  const layer = state.layers.find((item) => item.id === state.active);
  if (!layer) {
    return;
  }
  elements.legendNote.textContent = layer.legend_note || "";
  elements.legendNote.hidden = !layer.legend_note;
  if (layer.type === "rgb") {
    elements.legendTitle.textContent = "False color · R HH / G HV / B ratio";
    elements.legendBar.style.background =
      "linear-gradient(90deg, #080808, #4b2a2a, #b36b3d, #e8d7a8, #f4f4f4)";
    elements.legendMin.textContent = "HH";
    elements.legendMid.textContent = "HV";
    elements.legendMax.textContent = "ratio";
    return;
  }
  if (layer.legend_labels) {
    elements.legendTitle.textContent = "Algae blooms · relative signal";
    elements.legendBar.style.background = layer.legend_gradient;
    [elements.legendMin.textContent, elements.legendMid.textContent, elements.legendMax.textContent] = layer.legend_labels;
    return;
  }
  const [low, high] = layer.rescale;
  const unit = layer.unit || "";
  elements.legendTitle.textContent = `${layer.label} · ${unit}${layer.scale === "log" ? " · log scale" : ""}${layer.id === "soil_moisture" ? " · dry → wet" : ""}`;
  elements.legendBar.style.background = COLORMAPS[layer.colormap] || COLORMAPS.viridis;
  elements.legendMin.textContent = `${low.toFixed(1)} ${unit}`;
  elements.legendMid.textContent = `${(layer.scale === "log" ? Math.sqrt(low * high) : (low + high) / 2).toFixed(1)} ${unit}`;
  elements.legendMax.textContent = `${high.toFixed(1)} ${unit}`;
}

function renderLakeMetadata(layer) {
  if (!layer) return;
  elements.crs.textContent = layer.resolution_label;
  elements.coverageNote.textContent = layer.coverage_text || `${layer.coverage_percent.toFixed(1)}% of Lake Erie has usable pixels in this layer. ${layer.description}`;
  elements.coverageNote.hidden = false;
  elements.metadata.replaceChildren();
  const rows = [
    ["Observation", formatTime(layer.start)],
    ["Period", layer.period],
    ["Source", layer.source],
    ["Status", layer.status],
    ["Resolution", layer.resolution_label],
    ["Units", layer.unit],
  ];
  if (layer.stop && layer.stop !== layer.start) rows.splice(1, 0, ["Through", formatTime(layer.stop)]);
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "meta-row";
    const name = document.createElement("span");
    name.textContent = label;
    const content = document.createElement("strong");
    content.textContent = value;
    row.append(name, content);
    elements.metadata.append(row);
  }
  const link = document.createElement("a");
  link.href = layer.source_url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "About this data source ↗";
  elements.metadata.append(link);
}

function renderMetadata(granule) {
  if (granule.product === "LAKE_ERIE") return;
  const stats = granule.stats?.hh_db;
  const rows = granule.product === "SME2" ? [
    ["Acquired", formatTime(granule.start)],
    ["Status", granule.maturity || "Unknown"],
    ["Source spacing", `${granule.source_spacing_m?.toFixed(3)} m · native grid`],
    ["Usable area", `${granule.coverage?.usable_percent?.toFixed(2)}% of study area`],
    ["Grid", granule.crs],
    ["Orbit", `Track ${granule.track} · frame ${granule.frame}`],
    ["Quality rule", "Retrieval = 0 and surface = 0"],
  ] : [
    ["Scene", granule.scene || "—"],
    ["Acquired", `${formatTime(granule.start)} → ${formatTime(granule.stop)}`],
    [
      "Orbit",
      `${granule.orbit_direction || "—"} · track ${granule.track ?? "—"} · frame ${granule.frame ?? "—"}`,
    ],
    ["Absolute orbit", `${granule.absolute_orbit ?? "—"} · ${granule.look_direction || "—"} looking`],
    ["Polarizations", (granule.polarizations || []).join(" · ") || "—"],
    ["Grid", `${granule.crs || "—"} · ${granule.output_spacing_m?.toFixed(2) ?? "?"} m`],
    ["HH stats", stats ? `${stats[0]} / ${stats[1]} / ${stats[2]} dB` : "—"],
  ];
  elements.metadata.replaceChildren();
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "meta-row";
    const name = document.createElement("span");
    name.textContent = label;
    const content = document.createElement("strong");
    content.textContent = value;
    row.append(name, content);
    elements.metadata.append(row);
  }
}

function bindControls() {
  elements.basemap.addEventListener("change", (event) => {
    const selected = event.target.value;
    for (const id of BASEMAP_IDS) {
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, "visibility", id === selected ? "visible" : "none");
      }
    }
  });

  elements.opacity.addEventListener("input", (event) => {
    state.opacity = Number(event.target.value);
    elements.opacityValue.textContent = `${state.opacity}%`;
    for (const layer of state.layers) {
      const id = `nisar-${layer.id}`;
      if (map.getLayer(id)) {
        map.setPaintProperty(id, "raster-opacity", state.opacity / 100);
      }
    }
  });

  elements.fitLake.addEventListener("click", () => {
    fitBounds(LAKE_ERIE_BOUNDS);
  });

  elements.fitStudy.addEventListener("click", () => {
    const layer = state.layers.find((item) => item.id === state.active);
    fitBounds(layer?.valid_bounds_wgs84 || state.granule.aoi_bbox || state.granule.bounds_wgs84);
  });

  elements.visible.addEventListener("change", () => {
    state.visible = elements.visible.checked;
    setActiveLayer(state.active);
  });

  map.on("click", (event) => {
    samplePoint(event.lngLat.lng, event.lngLat.lat);
  });
}

async function samplePoint(lon, lat) {
  if (!state.active) {
    elements.readout.textContent = `No usable observation · ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
    return;
  }
  const layer = state.layers.find((item) => item.id === state.active);
  const sequence = ++state.sampleSequence;
  elements.readout.textContent = "Sampling…";
  try {
    const data = await fetchJSON(
      `/api/point?layer=${encodeURIComponent(state.active)}&lon=${lon}&lat=${lat}`,
    );
    if (sequence !== state.sampleSequence) return;
    if (layer?.type === "rgb") {
      const values = (data.values || []).map((value) => Math.round(value)).join(" / ");
      elements.readout.textContent = `RGB ${values} · ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
      return;
    }
    if (data.value == null) {
      const message = data.status === "no_bloom_detected" ? "No bloom detected in this observation" : data.status === "cloud" ? "Cloud-obscured · no usable bloom observation" : "No usable observation";
      elements.readout.textContent = `${message} · ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
      return;
    }
    elements.readout.textContent = `${data.unit === "CI" ? data.value.toExponential(2) : data.value.toFixed(data.unit === "m³/m³" ? 3 : 2)} ${data.unit || ""} · ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  } catch {
    if (sequence !== state.sampleSequence) return;
    elements.readout.textContent = `No usable observation · ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  }
}

function fitBounds(bounds, options = {}) {
  if (!bounds) {
    return;
  }
  const padding =
    window.innerWidth <= 720
      ? { top: 40, right: 40, bottom: Math.round(window.innerHeight * 0.5), left: 40 }
      : { top: 50, right: 50, bottom: 50, left: 400 };
  map.fitBounds(
    [
      [bounds[0], bounds[1]],
      [bounds[2], bounds[3]],
    ],
    { padding, duration: 900, ...options },
  );
}

function formatTime(value) {
  if (!value) {
    return "—";
  }
  return `${value.replace("T", " ").slice(0, 19)} UTC`;
}

async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json();
}

function showError(error) {
  console.error(error);
  elements.loading.hidden = true;
  elements.content.hidden = true;
  elements.error.hidden = false;
  elements.error.textContent =
    "Could not load observation metadata. Prepare map data and refresh this page.";
}
