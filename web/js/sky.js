/* The sky over the land: night falling and the lights that hold it back, the
 * weather (rain, storms, snow, fog), the seasons, omens in the sky, lightning,
 * and the news of big happenings.
 *
 * The dark is a RenderTexture laid over the view: filled with the colour of the
 * hour, with soft holes erased around every campfire, burning tree and lit
 * window. It is drawn at a fraction of the screen's resolution (the dark is
 * soft anyway) and follows the camera. */
import { $, TILE } from "./config.js";
import { poseAnimals } from "./animals.js";
import { burst, reduceMotion } from "./fx.js";
import { setGroundSeason } from "./ground.js";
import { lights, setTreeSeason } from "./objects.js";
import { state } from "./state.js";
import { scene } from "./world.js";

export const DARK_DEPTH = 15000;
const WEATHER_DEPTH = 14000, FOG_DEPTH = 14500, SKY_DEPTH = 15001;
const PAD = 64;          // world pixels of dark beyond each edge of the view (the camera can move a frame ahead)
const NIGHT = [8, 12, 40], DUSK = [70, 34, 30];  // the colour of the dark, deep night and sunset
// how much each kind of weather darkens the day, and the fog it brings
const GLOOM = { clear: 0, rain: 0.16, storm: 0.3, snow: 0.08, fog: 0.06, drought: 0 };
const FOG = { fog: 0.85, rain: 0.18, storm: 0.22, snow: 0.12 };
const SEASON_ICON = { spring: "🌱", summer: "☀️", autumn: "🍂", winter: "❄️" };
const TIME_ICON = { night: "🌙", dawn: "🌅", morning: "🌤️", midday: "☀️", afternoon: "🌤️", dusk: "🌇" };

const sky = { rt: null, lamp: null, dark: 0, target: 0, warm: 0, rain: null, snow: null, zone: null, fog: [],
              fogA: 0, fogTarget: 0, fresh: true, weather: "", omen: null, comet: 0, clockText: "" };

const lerp = (a, b, t) => a + (b - a) * t;
const rgb = ([r, g, b]) => (r << 16) | (g << 8) | b;

function setup(sc) {
  if (sky.rt && sky.rt.scene === sc) return;
  sky.rt = sc.add.renderTexture(0, 0, 8, 8).setOrigin(0).setDepth(DARK_DEPTH).setVisible(false);
  sky.lamp = sc.make.image({ key: "lightmask", add: false }).setOrigin(0.5);
  sky.zone = new Phaser.Geom.Rectangle(0, 0, 10, 10);
  const fade = (top) => ({ onEmit: () => 0, onUpdate: (_p, _k, t) => Math.sin(Math.PI * t) * top });
  sky.rain = sc.add.particles(0, 0, "raindrop", {
    emitZone: { type: "random", source: sky.zone }, speedY: { min: 430, max: 520 }, speedX: { min: -80, max: -60 },
    rotate: 9, lifespan: 420, frequency: 16, quantity: 4, alpha: fade(0.75), emitting: false,
  }).setDepth(WEATHER_DEPTH);
  sky.snow = sc.add.particles(0, 0, "snowflake", {
    emitZone: { type: "random", source: sky.zone }, speedY: { min: 22, max: 46 }, speedX: { min: -22, max: 14 },
    scale: { min: 0.8, max: 1.7 }, lifespan: 3800, frequency: 60, quantity: 2, alpha: fade(0.95), emitting: false,
  }).setDepth(WEATHER_DEPTH);
  sky.fog = [];
  for (let i = 0; i < 9; i++)
    sky.fog.push(sc.add.image(0, 0, "fogpuff").setDepth(FOG_DEPTH).setAlpha(0).setVisible(false));
}

export function clearSky() {
  sky.fresh = true;  // (a new world: no fading in from daylight)
  sky.omen = null;
  sky.weather = "";
  if (sky.rain) { sky.rain.killAll(); sky.snow.killAll(); }
}

// --- each frame from the server -----------------------------------------------------
export function renderSky(sc, f, opts = {}) {
  setup(sc);
  const c = f.clock;
  if (!c || c.light == null) { sky.target = 0; return; }
  // the dark of the hour, deepened by bad weather and by an eclipse
  const night = (1 - c.light) * 0.68, gloom = (GLOOM[c.weather] || 0) * (0.4 + 0.6 * c.light);
  let dark = 1 - (1 - night) * (1 - gloom);
  if (c.omen && c.omen.kind === "eclipse") dark = Math.max(dark, 0.62);
  sky.target = dark;
  sky.warm = c.light > 0 && c.light < 1 ? 1 - Math.abs(c.light - 0.5) * 2 : 0;  // dawn and dusk glow red
  if (opts.snap || sky.fresh) { sky.dark = dark; sky.fogA = FOG[c.weather] || 0; sky.fresh = false; }
  sky.fogTarget = FOG[c.weather] || 0;
  // the weather
  if (c.weather !== sky.weather) {
    sky.weather = c.weather;
    const wet = c.weather === "rain" || c.weather === "storm";
    sky.rain.emitting = wet && !reduceMotion;
    sky.rain.quantity = c.weather === "storm" ? 8 : 4;
    sky.rain.speedX = c.weather === "storm" ? -140 : -70;
    sky.snow.emitting = c.weather === "snow" && !reduceMotion;
  }
  // the season, on the ground and in the trees
  setGroundSeason(c.season);
  setTreeSeason(c.season);
  // omens
  const omen = c.omen && c.omen.kind === "comet" ? c.omen.until : null;
  if (omen && sky.omen !== omen && !opts.snap && !opts.reverse) { sky.omen = omen; comet(sc); }
  showClock(c);
  if (!opts.snap && !opts.reverse) {
    playWeatherFx(sc, f.fx || []);
    for (const h of opts.tickMs ? [] : f.happenings || []) {  // (not again when replayed)
      announce(h);
      if (h.kind === "festival" && h.x != null && !reduceMotion) {  // a festival: confetti over the village
        const x = (h.x + 0.5) * TILE, y = (h.y + 0.5) * TILE;
        for (let i = 0; i < 4; i++)
          burst(sc, x + (i - 1.5) * 30, y - 10, [0xf6c04a, 0xd9534f, 0x6aa6d8, 0x7bb661, 0xffffff], 18,
                { spread: 28, up: 34, gravity: 22, dur: 1400, delay: i * 350 });
      }
    }
  }
}

// --- every animation frame -------------------------------------------------------------
export function updateSky(sc, t, dt) {
  if (!sky.rt) return;
  sky.dark = lerp(sky.dark, sky.target, Math.min(1, dt / 900));
  sky.fogA = lerp(sky.fogA, sky.fogTarget, Math.min(1, dt / 2500));
  const cam = sc.cameras.main, v = cam.worldView;
  // weather falls across the whole view
  sky.zone.setTo(v.x - 60, v.y - 40, v.width + 120, v.height + 40);
  const area = (v.width * v.height) / 120000;
  if (sky.rain.emitting) sky.rain.quantity = Math.max(1, Math.round(area * (sky.weather === "storm" ? 5 : 3)));
  if (sky.snow.emitting) sky.snow.quantity = Math.max(1, Math.round(area * 1.2));
  drawFog(v, t);
  drawDark(sc, v, t);
  poseAnimals(sc, sky.dark, t);
}

function drawFog(v, t) {
  const on = sky.fogA > 0.01;
  sky.fog.forEach((img, i) => {
    img.setVisible(on);
    if (!on) return;
    const drift = ((i * 0.37 + t / 90000 * (1 + (i % 3) * 0.4)) % 1.4) - 0.2;
    const s = v.width / 520 * (0.9 + (i % 4) * 0.25);
    img.setPosition(v.x + drift * v.width, v.y + ((i * 0.618) % 1) * v.height).setScale(s, s * 0.8)
      .setAlpha(sky.fogA * (0.7 + 0.3 * Math.sin(t / 3000 + i)));
  });
}

function drawDark(sc, v, t) {
  if (sky.dark < 0.012) { sky.rt.setVisible(false); return; }
  const k = Math.max(2, Math.ceil((v.width + 2 * PAD) / 1100));  // world pixels per pixel of dark
  // (sized up in steps, and made afresh rather than resized: a resized one stops drawing)
  const w = Math.ceil((v.width + 2 * PAD) / k / 32) * 32, h = Math.ceil((v.height + 2 * PAD) / k / 32) * 32;
  if (sky.rt.width !== w || sky.rt.height !== h) {
    sky.rt.destroy();
    sky.rt = sc.add.renderTexture(0, 0, w, h).setOrigin(0).setDepth(DARK_DEPTH);
  }
  const rt = sky.rt;
  const x0 = v.x - PAD, y0 = v.y - PAD;
  rt.setVisible(true).setPosition(x0, y0).setScale(k);
  const col = [0, 1, 2].map((i) => Math.round(lerp(NIGHT[i], DUSK[i], sky.warm)));
  rt.clear().fill(rgb(col), sky.dark);
  const lamp = sky.lamp, reach = (r, wob) => (r * TILE * wob) / 64 / k;
  const lit = (x, y, r, a, wob = 1) => {
    if (x < x0 - r * TILE || y < y0 - r * TILE || x > v.right + PAD + r * TILE || y > v.bottom + PAD + r * TILE) return;
    lamp.setPosition((x - x0) / k, (y - y0) / k).setScale(reach(r, wob)).setAlpha(a);
    rt.erase(lamp);
  };
  for (const rec of lights) {
    const fire = rec.key === "fire" || rec.key === "blaze";
    const wob = fire ? 1 + 0.06 * Math.sin(t / 130 + rec.x * 3) + 0.03 * Math.sin(t / 47 + rec.y) : 1;
    lit(rec.img.x, rec.img.y - TILE / 2, rec.light, fire ? 1 : 0.7, wob);
  }
  // the one you are following (or playing) carries a little light of their own
  const me = state.sprites.get(state.possessed != null ? state.possessed : state.selected);
  if (me && me.sprite.active) lit(me.sprite.x, me.sprite.y - 10, state.possessed != null ? 2.6 : 1.8, 0.55);
}

// --- weather events ---------------------------------------------------------------------
// A bolt of lightning from the top of the view down to (x, y) in the world.
export function bolt(sc, x, y, { shake = true } = {}) {
  const cam = sc.cameras.main, top = cam.worldView.y - 20;
  const g = sc.add.graphics().setDepth(SKY_DEPTH);
  const pts = [[x + 6, top]];
  for (let yy = top + 30; yy < y; yy += 26) pts.push([x + (Math.random() - 0.5) * 22, yy]);
  pts.push([x, y]);
  for (const [w, c] of [[6, 0x9fd3ff], [2, 0xffffff]]) {
    g.lineStyle(w, c, 1);
    g.beginPath(); g.moveTo(...pts[0]); for (const p of pts.slice(1)) g.lineTo(...p); g.strokePath();
  }
  sc.tweens.add({ targets: g, alpha: 0, duration: 420, delay: 120, onComplete: () => g.destroy() });
  if (!reduceMotion) {
    cam.flash(160, 230, 240, 255);
    if (shake) cam.shake(220, 0.006);
  }
  burst(sc, x, y + 6, [0xffffff, 0x9fd3ff, 0x3a3a3a], 16, { spread: 20, up: 18, dur: 700 });
}

function playWeatherFx(sc, events) {
  const view = sc.cameras.main.worldView;
  for (const e of events) {
    if (e.x == null) continue;
    const x = e.x * TILE + TILE / 2, y = e.y * TILE + TILE / 2, near = view.contains(x, y);
    if (e.type === "lightning" || (e.type === "ignite" && e.cause === "lightning")) {
      if (near) bolt(sc, x, y);
      else if (!reduceMotion) sc.cameras.main.flash(120, 200, 210, 230);  // a flash on the horizon
    } else if (e.type === "ignite" && near) {
      burst(sc, x, y, [0xf6c04a, 0xe8641e, 0xfff0a0], 12, { spread: 12, up: 20, gravity: -6, dur: 800 });
    } else if (e.type === "burn" && e.what === "farm" && near) {
      burst(sc, x, y, [0xe8641e, 0x3a3532, 0xf6c04a], 10, { spread: 14, up: 14, dur: 700 });
    }
  }
}

// A comet streaks across the night sky (above the dark: it is the brightest thing up there).
function comet(sc) {
  if (reduceMotion) return;
  const v = sc.cameras.main.worldView, s = Math.max(1, v.width / 700);
  const img = sc.add.image(v.x - 80 * s, v.y + v.height * 0.12, "comet").setDepth(SKY_DEPTH).setScale(s)
    .setBlendMode(Phaser.BlendModes.ADD).setAngle(14);
  sc.tweens.add({ targets: img, x: v.right + 80 * s, y: v.y + v.height * 0.42, duration: 9000, ease: "Sine.inOut",
    onComplete: () => img.destroy() });
}

// --- the clock in the corner, and the news ---------------------------------------------
function showClock(c) {
  const wx = state.defs && state.defs.weather && state.defs.weather[c.weather];
  const days = (state.defs && state.defs.clock && state.defs.clock.season_days) || 6;
  const omen = c.omen ? (c.omen.kind === "comet" ? " · ☄️ comet" : " · 🌑 eclipse") : "";
  const html = `<b>Day ${c.day}</b> <span title="${c.season}, day ${c.day_of_season} of ${days}">${SEASON_ICON[c.season] || ""} ${c.season} ${c.day_of_season}/${days}</span>` +
    ` <span>${TIME_ICON[c.time] || ""} ${c.time}</span>` +
    (wx && c.weather !== "clear" ? ` <span>${wx.emoji} ${wx.label}</span>` : "") + omen;
  if (html === sky.clockText) return;
  sky.clockText = html;
  const el = $("clock");
  el.hidden = false;
  el.innerHTML = html;
  el.dataset.night = c.night ? "1" : "";
}

const news = [];
let newsBusy = false;
function announce(h) {
  news.push(h);
  if (news.length > 4) news.shift();
  if (!newsBusy) nextNews();
}

function nextNews() {
  const el = $("news"), h = news.shift();
  if (!h) { newsBusy = false; el.hidden = true; return; }
  newsBusy = true;
  el.textContent = h.text;
  el.dataset.kind = h.kind;
  el.onclick = h.x != null ? () => {
    const sc = scene();
    if (sc) { sc.cameras.main.stopFollow(); sc.cameras.main.pan((h.x + 0.5) * TILE, (h.y + 0.5) * TILE, 700, "Sine.inOut"); }
  } : null;
  el.classList.toggle("go", h.x != null);
  el.hidden = false;
  el.classList.remove("show"); void el.offsetWidth; el.classList.add("show");
  setTimeout(nextNews, 6500);
}

export const darkness = () => sky.dark;
