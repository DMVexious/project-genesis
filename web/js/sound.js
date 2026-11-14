/* Sound, heard from the selected villager's point of view.
 *
 * Nothing plays until you click a villager; then you hear what that one
 * person is doing: the axe when they chop, their voice when they speak, the
 * blow when someone hits them. Every sound is synthesised with WebAudio, so
 * there are no audio files to ship.
 */
import { state } from "./state.js";

let ctx = null, master = null, noiseBuf = null;
let enabled = true;
try { enabled = localStorage.getItem("genesis.sound") !== "0"; } catch (_e) { /* storage off */ }

export const soundOn = () => enabled;

export function setSound(on) {
  enabled = on;
  try { localStorage.setItem("genesis.sound", on ? "1" : "0"); } catch (_e) { /* storage off */ }
  if (on) unlock();
}

// Browsers only allow audio after a click or key press; call this from one.
export function unlock() {
  if (!enabled) return;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  if (!ctx) {
    ctx = new AC();
    master = ctx.createGain();
    master.gain.value = 0.55;
    master.connect(ctx.destination);
    noiseBuf = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  }
  if (ctx.state === "suspended") ctx.resume();
}

const ready = () => enabled && ctx && ctx.state === "running";

// A pitched blip: freq -> `to` over `dur` seconds.
function tone(freq, dur, { type = "sine", vol = 0.3, to = null, at = 0, attack = 0.006 } = {}) {
  const t = ctx.currentTime + at;
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, t);
  if (to) o.frequency.exponentialRampToValueAtTime(Math.max(20, to), t + dur);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.connect(g).connect(master);
  o.start(t);
  o.stop(t + dur + 0.02);
}

// A burst of filtered noise (wood, leaves, breath, impacts).
function noise(dur, { vol = 0.3, freq = 1200, q = 1, type = "bandpass", to = null, at = 0, attack = 0.004 } = {}) {
  const t = ctx.currentTime + at;
  const src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
  src.buffer = noiseBuf;
  src.loop = true;
  f.type = type;
  f.frequency.setValueAtTime(freq, t);
  if (to) f.frequency.exponentialRampToValueAtTime(to, t + dur);
  f.Q.value = q;
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  src.connect(f).connect(g).connect(master);
  src.start(t, Math.random() * 0.5);
  src.stop(t + dur + 0.02);
}

// Everyone's voice sits at its own pitch; children are higher, elders lower.
function voicePitch(n) {
  const base = 150 + ((n.id * 37) % 11) * 14;
  return n.stage === "child" ? base * 1.6 : n.stage === "elder" ? base * 0.8 : base;
}

// Speech as a string of vowel-ish blips, one per word (roughly).
function babble(n, words, { at = 0, vol = 0.22 } = {}) {
  const pitch = voicePitch(n), count = Math.max(2, Math.min(9, words));
  for (let i = 0; i < count; i++) {
    const f = pitch * (0.85 + Math.random() * 0.45), d = 0.07 + Math.random() * 0.06;
    tone(f, d, { type: "triangle", vol, to: f * (Math.random() < 0.5 ? 0.85 : 1.15), at: at + i * 0.115 });
    tone(f * 2.02, d * 0.8, { type: "sine", vol: vol * 0.3, at: at + i * 0.115 });
  }
}

const SOUNDS = {
  select: () => { tone(660, 0.07, { vol: 0.15 }); tone(990, 0.09, { vol: 0.12, at: 0.06 }); },
  step: (a = 0) => { noise(0.05, { vol: 0.09, freq: 420, q: 0.8, at: a }); tone(95, 0.05, { vol: 0.08, to: 60, at: a }); },
  // axe into wood: a sharp crack and a low thud
  chop: (a = 0, v = 1) => {
    noise(0.07, { vol: 0.45 * v, freq: 2200, q: 0.7, at: a });
    tone(170, 0.13, { type: "triangle", vol: 0.4 * v, to: 70, at: a });
  },
  timber: (a = 0) => { noise(0.5, { vol: 0.3, freq: 900, to: 200, q: 0.6, at: a }); tone(80, 0.25, { vol: 0.35, to: 40, at: a + 0.3 }); },
  // rummaging through leaves
  forage: (a = 0, v = 1) => { noise(0.16, { vol: 0.2 * v, freq: 3200, q: 0.5, at: a }); noise(0.1, { vol: 0.14 * v, freq: 4500, q: 0.8, at: a + 0.12 }); },
  // getting better at something: a bright rising run
  levelup: (a = 0) => {
    [659, 784, 988, 1319].forEach((f, i) => tone(f, 0.16, { type: "triangle", vol: 0.2, at: a + i * 0.075 }));
    tone(2637, 0.35, { vol: 0.07, at: a + 0.3 });
  },
  attrup: (a = 0) => { tone(784, 0.14, { vol: 0.14, at: a }); tone(1175, 0.22, { vol: 0.14, at: a + 0.1 }); },
  // a pick on stone
  clink: (a = 0, v = 1) => { tone(1900, 0.08, { type: "square", vol: 0.1 * v, to: 1500, at: a }); noise(0.05, { vol: 0.25 * v, freq: 3500, q: 2, at: a }); },
  // something going into the water
  splash: (a = 0, v = 1) => { noise(0.35, { vol: 0.25 * v, freq: 1400, to: 500, q: 0.7, at: a }); },
  // an item jumping into your hands
  pop: (a = 0) => { tone(480, 0.09, { type: "triangle", vol: 0.22, to: 960, at: a }); },
  pluck: (a = 0) => { tone(520, 0.06, { vol: 0.16, to: 780, at: a }); },
  // hammering
  build: (a = 0, v = 1) => {
    tone(240, 0.06, { type: "square", vol: 0.16 * v, to: 150, at: a });
    noise(0.04, { vol: 0.3 * v, freq: 1500, q: 1.2, at: a });
  },
  built: (a = 0) => { [392, 494, 587].forEach((f, i) => tone(f, 0.22, { type: "triangle", vol: 0.16, at: a + i * 0.09 })); },
  // chewing
  eat: (a = 0, v = 1) => { for (let i = 0; i < 2; i++) noise(0.06, { vol: 0.2 * v, freq: 900, q: 1.5, at: a + i * 0.14 }); },
  gulp: (a = 0) => { tone(220, 0.12, { vol: 0.18, to: 110, at: a }); },
  // scraping and whittling
  craft: (a = 0, v = 1) => { noise(0.18, { vol: 0.16 * v, freq: 2600, to: 3600, q: 3, at: a }); },
  crafted: (a = 0) => { tone(1180, 0.3, { vol: 0.16, at: a }); tone(1770, 0.25, { vol: 0.08, at: a }); },
  // slow breath in and out
  snore: () => { noise(0.9, { vol: 0.1, freq: 260, to: 420, q: 2, attack: 0.4 }); noise(0.8, { vol: 0.07, freq: 380, to: 220, q: 2, at: 1.0, attack: 0.3 }); },
  swing: (a = 0) => { noise(0.16, { vol: 0.22, freq: 700, to: 2600, q: 1.2, at: a }); },
  hit: (a = 0, big = false) => {
    tone(big ? 110 : 150, 0.16, { type: "sawtooth", vol: 0.3, to: 50, at: a });
    noise(0.09, { vol: 0.35, freq: big ? 500 : 900, q: 0.7, at: a });
  },
  ouch: (n, a = 0) => { const p = voicePitch(n); tone(p * 1.5, 0.2, { type: "sawtooth", vol: 0.16, to: p * 0.9, at: a }); },
  miss: (a = 0) => { noise(0.2, { vol: 0.12, freq: 1800, to: 600, q: 1.5, at: a }); },
  trade: (a = 0) => { tone(880, 0.09, { vol: 0.16, at: a }); tone(1320, 0.16, { vol: 0.16, at: a + 0.09 }); },
  love: (a = 0) => { [523, 659, 784, 1047].forEach((f, i) => tone(f, 0.3, { vol: 0.13, at: a + i * 0.12 })); },
  baby: (n, a = 0) => { for (let i = 0; i < 3; i++) tone(620, 0.22, { type: "triangle", vol: 0.15, to: 480, at: a + i * 0.28 }); },
  thunder: () => {
    noise(1.6, { vol: 0.5, freq: 180, to: 60, q: 0.6, type: "lowpass", attack: 0.01 });
    noise(0.25, { vol: 0.4, freq: 2400, q: 0.5 });
  },
  // the wild
  howl: (a = 0) => {
    tone(330, 1.6, { type: "sine", vol: 0.12, to: 520, at: a, attack: 0.5 });
    tone(520, 1.1, { type: "sine", vol: 0.1, to: 300, at: a + 1.4, attack: 0.2 });
    tone(661, 1.4, { type: "triangle", vol: 0.04, to: 1040, at: a, attack: 0.5 });
  },
  growl: (a = 0) => { noise(0.35, { vol: 0.22, freq: 160, q: 4, at: a }); tone(90, 0.3, { type: "sawtooth", vol: 0.12, to: 70, at: a }); },
  hiss: (a = 0) => { noise(0.6, { vol: 0.22, freq: 3000, to: 900, q: 0.6, at: a }); },
  rumble: () => { noise(2.2, { vol: 0.32, freq: 120, to: 50, q: 0.6, type: "lowpass", attack: 0.3 }); },
  death: () => { tone(220, 1.1, { type: "triangle", vol: 0.22, to: 55 }); tone(165, 1.2, { vol: 0.15, to: 41, at: 0.1 }); },
};

function play(name, ...args) {
  if (ready() && SOUNDS[name]) SOUNDS[name](...args);
}

export function playSelect() { unlock(); if (enabled && ctx) SOUNDS.select(); }

// Called with every live frame: turn what happened to the selected villager into sound.
let lastPos = null, lastSnore = 0;
// Rain on the leaves: a soft bed of noise for as long as it rains (heard by all).
let rain = null;
function setRain(level) {
  if (!ctx || (!rain && (!level || !ready()))) return;
  if (!rain) {
    const src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    src.buffer = noiseBuf; src.loop = true;
    f.type = "lowpass"; f.frequency.value = 1400;
    g.gain.value = 0.0001;
    src.connect(f).connect(g).connect(master);
    src.start();
    rain = { src, g };
  }
  rain.g.gain.setTargetAtTime(Math.max(0.0001, level), ctx.currentTime, 1.5);
}

export function playFrame(f) {
  const w = f.clock && f.clock.weather;
  setRain(enabled ? (w === "storm" ? 0.14 : w === "rain" ? 0.08 : 0) : 0);
  for (const e of f.fx || []) {  // heard by all
    if ((e.type === "death" && e.cause === "lightning") || e.type === "lightning"
        || (e.type === "ignite" && e.cause === "lightning")) play("thunder");
    else if (e.type === "wolves") play("howl", 0.3);
  }
  if (ready() && (f.happenings || []).some((h) => h.kind === "eclipse" || h.kind === "comet")) play("rumble");
  if (!ready() || state.selected === null) { lastPos = null; return; }
  const me = f.npcs.find((n) => n.id === state.selected);
  if (!me) { lastPos = null; return; }
  const byId = (id) => f.npcs.find((n) => n.id === id);

  // footsteps
  if (lastPos && lastPos.id === me.id && (lastPos.x !== me.x || lastPos.y !== me.y) && me.alive) {
    const ms = Math.max(240, (me.dur || 2) * state.tickMs) / 1000;
    play("step", 0); play("step", ms / 2);
  }
  lastPos = { id: me.id, x: me.x, y: me.y };

  // breathing while asleep
  if (me.alive && me.status === "💤" && performance.now() - lastSnore > 2600) {
    lastSnore = performance.now();
    play("snore");
  }

  const words = (id) => {
    const d = f.dialogue.find((x) => x.npc === id);
    return d ? d.text.split(/\s+/).length : 4;
  };
  for (const e of f.fx || []) {
    const mine = e.npc === me.id, atMe = e.target === me.id;
    switch (e.type) {
      case "work":  // one swing of the tool while a job is under way
        if (mine) play({ chop: "chop", forage: "forage", build: "build", eat: "eat", craft: "craft", mine: "clink",
                         construct: "build", fish: "splash", plant: "forage" }[e.kind] || "build", 0.1, 0.8);
        break;
      case "mine": if (mine) { play("clink", 0.1); play("clink", 0.3); } break;
      case "fish": if (mine) { play("splash", 0.05); if (e.caught) play("pop", 0.3); } break;
      case "construct": if (mine) { play("build", 0.09); play("build", 0.29); } break;
      case "built": if (mine) play("built", 0.2); break;
      case "pray": if (mine) play("attrup", 0.1); break;
      case "chop": if (mine) { play("chop", 0.1); play("chop", 0.38); play("timber", 0.5); } break;
      case "forage": if (mine) { play("forage", 0.1); play("pluck", 0.3); } break;
      case "build": if (mine) { play("build", 0.09); play("build", 0.29); play("built", 0.55); } break;
      case "eat": if (mine) { play("eat", 0.05); play("gulp", 0.4); } break;
      case "craft": if (mine) { play("craft", 0); play("crafted", 0.38); } break;
      case "talk":
        if (mine) babble(me, words(me.id));
        else if (atMe) { const o = byId(e.npc); if (o) babble(o, words(o.id), { vol: 0.15 }); }
        if (mine || atMe) {  // the answer comes a beat later
          const other = byId(mine ? e.target : e.npc);
          const replier = mine ? other : me;
          if (replier && f.dialogue.some((d) => d.reply && d.npc === replier.id))
            babble(replier, words(replier.id), { at: 2.2, vol: mine ? 0.15 : 0.22 });
        }
        break;
      case "attack": {
        const at = (e.retaliation ? 0.32 : 0) + 0.11;
        if (mine) { play("swing", at - 0.1); play(e.hit ? "hit" : "miss", at, e.damage >= 20); }
        else if (atMe) { play("swing", at - 0.1); if (e.hit) { play("hit", at, e.damage >= 20); play("ouch", me, at + 0.05); } else play("miss", at); }
        break;
      }
      case "trade": case "gift": if (mine || atMe) play("trade", 0.12); break;
      case "pickup": if (mine) play("pop", 0.2); break;
      case "levelup": if (mine) play("levelup", 0.15); break;
      case "attrup": if (mine) play("attrup", 0.45); break;
      case "conceive": if (mine || atMe) play("love", 0.1); break;
      case "birth": if (mine || (e.parents || []).includes(me.id)) play("baby", byId(e.npc) || me, 0.2); break;
      case "death": if (mine) play("death"); break;
      case "hunt": if (mine) { play("swing", 0); play(e.hit ? "hit" : "miss", 0.11); } break;
      case "mauled": if (mine) { play("growl", 0); if (e.hit) { play("hit", 0.11); play("ouch", me, 0.16); } } break;
      case "douse": if (mine) play("hiss", 0.1); break;
      case "burn": if (mine) play("ouch", me, 0); break;
      default: break;
    }
  }
}
