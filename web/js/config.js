/* Constants, URL options and small DOM helpers shared by every module. */
export const TILE = 24;
export const NPC_SCALE = 1.25;
// Speech bubbles fade this many ticks after they appear. Ticks are short now
// (~150 ms; NPC actions span several), so this is ~3.5 s - long enough to read.
export const BUBBLE_TICKS = 24;
export const BUBBLE_MIN_MS = 6000;   // ...and never before 6 s, however fast the sim runs
export const REPLY_DELAY_MS = 1200;  // the listener's answer appears this long after the line
export const MAX_TALKS = 60;         // exchanges kept in the Conversations feed
export const GRAVE_TICKS = 400; // graves stay ~1 minute
export const MAX_GRAVES = 80;
export const AGGRO_COLOR = { Pacifist: 0x7bb661, Neutral: 0x6aa6d8, Hostile: 0xe0a458, Bloodthirsty: 0xd9534f };
export const hex = (n) => "#" + n.toString(16).padStart(6, "0");

export const params = new URLSearchParams(location.search);
// Served by the orchestrator itself -> same host. Inside Tauri (or opened as
// a file) the page has its own origin, so talk to the sidecar's fixed port.
export const servedByOrchestrator = /^https?:$/.test(location.protocol) && !/tauri\.localhost$/.test(location.hostname);
export const WS_URL = params.get("server") ||
  (servedByOrchestrator ? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws` : "ws://127.0.0.1:8765/ws");

export const $ = (id) => document.getElementById(id);
export const overlay = $("overlay");
if (params.has("bare")) $("panel").hidden = $("timeline").hidden = true;  // ?bare=1: world only (screenshots, recordings)
