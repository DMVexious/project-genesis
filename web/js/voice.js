/* Voices: villagers say their lines out loud, and you can answer with yours.
 *
 * Speaking uses the system's own voices (speechSynthesis): nothing to download.
 * Each villager keeps one voice for life, picked from their id, pitched by age.
 * Only one line is spoken at a time; lines that would come too late are dropped,
 * and lines to or from you go first.
 *
 * Listening: hold T (or the 🎤 by a text box) and talk. The clip is turned into
 * words on this computer by the server (genesis/hearing.py) and lands in
 * whichever box is open: the conversation, the answer, or what to say to someone.
 */
import { $ } from "./config.js";
import { toast } from "./god.js";
import { state } from "./state.js";
import { settings } from "./tabs.js";

const synth = window.speechSynthesis || null;
let voices = [];
function loadVoices() {
  if (!synth) return;
  const all = synth.getVoices();
  const en = all.filter((v) => /^en(-|_|$)/i.test(v.lang));
  const pool = en.length ? en : all;
  // voices on this computer only, when there are a few (online ones send the words away to be spoken)
  const local = pool.filter((v) => v.localService);
  voices = (local.length >= 2 ? local : pool).sort((a, b) => a.name.localeCompare(b.name));
}
if (synth) { loadVoices(); synth.addEventListener("voiceschanged", loadVoices); }

// Who is heard: "off", "mine" (you, whoever you follow or talk with), "near" (anyone in view).
const mode = () => settings.voices || "mine";
const me = () => new Set([state.selected, state.possessed].filter((v) => v != null));

export function speaks(id) {  // (sound.js leaves out its babble for these)
  if (!synth || !voices.length || mode() === "off") return false;
  if (mode() === "near") return true;
  return me().has(id);
}

const queue = [];
let current = null;
const STALE_MS = 7000;

// A line said by villager d.npc (to d.target). Called as its bubble appears.
export function speakLine(d, npc) {
  if (!synth || !npc || mode() === "off" || !d.text || d.npc < 0) return;
  if (d.player && d.npc === state.possessed) return;  // (your own words: you typed them)
  const mine = me().has(d.npc) || me().has(d.target);
  if (mode() === "mine" && !mine) return;
  const line = { text: d.text, npc, mine, at: performance.now() };
  if (mine && current && !current.mine) synth.cancel();  // yours come first
  queue.push(line);
  while (queue.length > 3) queue.splice(queue.findIndex((l) => !l.mine) >= 0 ? queue.findIndex((l) => !l.mine) : 0, 1);
  next();
}

function next() {
  if (!synth || current || !queue.length) return;
  queue.sort((a, b) => (b.mine - a.mine) || a.at - b.at);
  const line = queue.shift();
  if (performance.now() - line.at > STALE_MS) return next();
  const n = line.npc, u = new SpeechSynthesisUtterance(line.text);
  const h = (n.id * 2654435761) >>> 0;
  u.voice = voices[h % voices.length];
  const stage = n.stage || (n.age < 16 ? "child" : "adult");
  u.pitch = Math.max(0.1, Math.min(2, (0.8 + (h >>> 8) % 40 / 100) * (stage === "child" ? 1.5 : stage === "elder" ? 0.85 : 1)));
  u.rate = (0.9 + (h >>> 16) % 25 / 100) * (stage === "elder" ? 0.88 : 1);
  u.volume = Math.max(0, Math.min(1, (settings.volume ?? 55) / 100 * (line.mine ? 1 : 0.7) * distanceFall(n.id)));
  current = line;
  const done = () => { if (current === line) { current = null; setTimeout(next, 250); } };
  u.onend = done;
  u.onerror = done;
  synth.speak(u);
}

// In first person a voice fades with distance; on the map everyone in view is heard alike.
let distanceOf = null;
export function setDistanceOf(fn) { distanceOf = fn; }
function distanceFall(id) {
  const d = distanceOf ? distanceOf(id) : null;
  return d == null ? 1 : Math.max(0.15, Math.min(1, 1.6 - d / 10));
}

export function hushVoices() { queue.length = 0; if (synth) synth.cancel(); current = null; }

// --- your voice ----------------------------------------------------------------------------------
let canHear = false, rec = null, chunks = [], stream = null, holding = false;

async function checkHearing() {
  try {
    const r = await fetch("api/hear");
    canHear = r.ok && (await r.json()).available && !!(navigator.mediaDevices && window.MediaRecorder);
  } catch (e) { canHear = false; }
  document.body.classList.toggle("can-hear", canHear);
}

// Where the words go: the first text box that is open, as if typed and sent.
const TARGETS = [["pb-chat", "pbc-text", "pbc-form"], ["pb-heard", "pb-answer-text", "pb-answer"],
                 ["interact", "ia-text", "ia-say"], ["godmenu", "gm-text", "gm-say"]];
function target() {
  for (const [box, input, form] of TARGETS) if ($(box) && !$(box).hidden) return { input: $(input), form: $(form) };
  return null;
}

async function startListening() {
  if (!canHear || holding) return;
  if (!target()) { toast("Open a conversation first (click someone), then hold T to talk.", false); return; }
  holding = true;
  document.body.classList.add("listening");
  try {
    stream = stream || await navigator.mediaDevices.getUserMedia({ audio: true });
    if (!holding) return;  // (let go before the mic was ready)
    chunks = [];
    rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = send;
    rec.start();
  } catch (e) {
    holding = false;
    document.body.classList.remove("listening");
    toast("No microphone: the browser said no.", false);
  }
}

function stopListening() {
  if (!holding) return;
  holding = false;
  document.body.classList.remove("listening");
  if (rec && rec.state === "recording") rec.stop();
}

async function send() {
  const blob = new Blob(chunks, { type: (rec && rec.mimeType) || "audio/webm" });
  if (blob.size < 800) return;  // (a tap, not a line)
  const t = target();
  if (!t) return;
  const was = t.input.placeholder;
  t.input.placeholder = "listening…";
  try {
    const r = await fetch("api/hear", { method: "POST", body: blob });
    const text = r.ok ? ((await r.json()).text || "").trim() : "";
    if (!text) { toast("Couldn't make that out. Try again?", false); return; }
    t.input.value = text.slice(0, Number(t.input.maxLength) > 0 ? Number(t.input.maxLength) : 240);
    t.form.requestSubmit();
  } catch (e) {
    toast("Couldn't reach the server to listen.", false);
  } finally {
    t.input.placeholder = was;
  }
}

export function initVoice() {
  checkHearing();
  // hold T to talk (not while typing: then the 🎤 by the box does it)
  document.addEventListener("keydown", (e) => {
    if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || (e.key !== "t" && e.key !== "T")) return;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
    e.preventDefault();
    startListening();
  });
  document.addEventListener("keyup", (e) => { if (e.key === "t" || e.key === "T") stopListening(); });
  window.addEventListener("blur", stopListening);
  for (const [, , form] of TARGETS) {
    const f = $(form);
    if (!f) continue;
    const mic = document.createElement("button");
    mic.type = "button";
    mic.className = "mic";
    mic.title = "Hold to talk (or hold T)";
    mic.textContent = "🎤";
    mic.addEventListener("pointerdown", (e) => { e.preventDefault(); startListening(); });
    mic.addEventListener("pointerup", stopListening);
    mic.addEventListener("pointerleave", stopListening);
    f.insertBefore(mic, f.querySelector("button"));
  }
}
