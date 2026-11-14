// Run the actual frontend script with small DOM/MapLibre stand-ins. No browser required.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.children = []; this.dataset = {}; this.listeners = {}; this.style = {}; this.classList = {toggle() {}}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelectorAll() { return this.children; }
}
class Map {
  constructor() { this.layers = {}; this.fits = []; }
  on() {}
  addControl() {}
  getLayer(id) { return this.layers[id]; }
  setLayoutProperty(id, key, value) { this.layers[id][key] = value; }
  fitBounds(bounds) { this.fits.push(bounds); }
}
function load() {
  const elements = {};
  const document = {
    getElementById(id) { return elements[id] ||= new Element(); },
    createElement() { return new Element(); },
    querySelector() { return new Element(); },
  };
  const context = vm.createContext({document, window: {innerWidth: 1200, innerHeight: 800},
    maplibregl: {Map, NavigationControl: class {}, ScaleControl: class {}}, console});
  vm.runInContext(fs.readFileSync('web/app.js', 'utf8'), context);
  return {context, elements, evaluate: code => vm.runInContext(code, context)};
}

test('clicking a layer enables it and fits its usable coverage; chlorophyll legend uses geometric midpoint', () => {
  const {elements, evaluate} = load();
  evaluate(`
    state.granule = {product: 'LAKE_ERIE'};
    state.layers = [
      {id:'radar', label:'Radar imagery', valid_bounds_wgs84:[-83.5,41.4,-81.6,42.5], rescale:[-25,-14], unit:'dB', coverage_percent:27},
      {id:'chlorophyll', label:'Algae · chlorophyll', valid_bounds_wgs84:[-83.5,41.4,-78.8,43.1], rescale:[.3,30], scale:'log', unit:'mg/m³', coverage_percent:99.7},
    ];
    map.layers['nisar-radar'] = {};
    map.layers['nisar-chlorophyll'] = {};
    state.visible = false;
    renderLayerButtons(state.layers);
  `);
  elements.layers.children[0].listeners.click();
  assert.equal(evaluate('state.active'), 'radar');
  assert.equal(evaluate('state.visible'), true);
  assert.equal(evaluate("map.layers['nisar-radar'].visibility"), 'visible');
  assert.deepEqual(JSON.parse(evaluate('JSON.stringify(map.fits.at(-1))')), [[-83.5,41.4],[-81.6,42.5]]);
  elements.layers.children[1].listeners.click();
  assert.equal(evaluate('state.active'), 'chlorophyll');
  assert.equal(evaluate("map.layers['nisar-radar'].visibility"), 'none');
  assert.equal(evaluate("map.layers['nisar-chlorophyll'].visibility"), 'visible');
  assert.equal(elements.legendMid.textContent, '3.0 mg/m³');
  assert.match(elements.legendTitle.textContent, /log scale/);
});
