/* The start screen: choose the kind of land, its size, how many founders and
 * the seed, see what each land would look like, and begin. It opens by itself
 * when the server is waiting for a world to be chosen, and from the "New
 * world" button at any time. */
import { $ } from "./config.js";
import { paintMap } from "./minimap.js";
import { send } from "./net.js";
import { state } from "./state.js";

const SIZES = [["Small", 60, 42], ["Medium", 80, 56], ["Large", 120, 84], ["Huge", 160, 112]];
const m = { open: false, first: false, built: false, map: "valley", size: 1, ask: 0, timer: 0 };

export const menuOpen = () => m.open;
const options = () => {
  const [, width, height] = SIZES[m.size];
  return { map: m.map, seed: Math.max(0, Math.floor(Number($("menu-seed").value) || 0)), width, height,
           population: Math.max(1, Math.min(24, Math.floor(Number($("menu-pop").value) || 3))) };
};

function build() {
  const maps = (state.defs && state.defs.maps) || [];
  $("menu-maps").innerHTML = maps.map((d) => `
    <button class="map-card" data-map="${d.key}" title="${d.blurb}">
      <canvas width="80" height="56"></canvas><b>${d.label}</b><span>${d.blurb}</span>
    </button>`).join("");
  $("menu-size").innerHTML = SIZES.map(([name, w, h], i) => `<option value="${i}">${name} (${w} × ${h})</option>`).join("");
  m.built = maps.length > 0;
}

function mark() {
  for (const card of $("menu-maps").children) card.classList.toggle("sel", card.dataset.map === m.map);
}

// Ask the server what each kind of land would look like with these choices.
function preview() {
  clearTimeout(m.timer);
  m.timer = setTimeout(() => {
    const o = options();
    m.ask += 1;
    for (const card of $("menu-maps").children) {
      card.classList.add("loading");
      send({ cmd: "preview", ...o, map: card.dataset.map, id: m.ask });
    }
  }, 200);
}

export function onPreview(msg) {
  if (!m.open || msg.id !== m.ask) return;  // an answer to choices since changed
  const card = [...$("menu-maps").children].find((c) => c.dataset.map === msg.map);
  if (!card) return;
  const canvas = card.querySelector("canvas");
  const map = paintMap(msg.terrain, msg.tiles);
  canvas.width = msg.width * 2;
  canvas.height = msg.height * 2;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(map, 0, 0, canvas.width, canvas.height);
  if (msg.start) {  // where the founders would settle
    const [x, y] = msg.start;
    ctx.strokeStyle = "#140e09"; ctx.lineWidth = 4; ctx.strokeRect(x * 2 - 5, y * 2 - 5, 12, 12);
    ctx.strokeStyle = "#ffd24a"; ctx.lineWidth = 2; ctx.strokeRect(x * 2 - 5, y * 2 - 5, 12, 12);
  }
  card.classList.remove("loading");
}

export function openMenu(first = false) {
  if (!m.built) build();
  if (!m.built) return;  // (the server has not said what maps there are yet)
  const c = state.control || {};
  if (!m.open) {
    m.map = c.map || m.map;
    const size = SIZES.findIndex(([, w, h]) => w === c.width && h === c.height);
    m.size = size >= 0 ? size : m.size;
    $("menu-size").value = m.size;
    $("menu-pop").value = c.population || 3;
    $("menu-seed").value = first ? c.seed ?? 7 : Math.floor(Math.random() * 1e6);
  }
  m.open = true;
  m.first = first;
  $("menu-cancel").hidden = first;
  $("menu").hidden = false;
  mark();
  preview();
}

export function closeMenu() {
  m.open = false;
  $("menu").hidden = true;
}

export function initMenu() {
  $("menu-maps").addEventListener("click", (e) => {
    const card = e.target.closest(".map-card");
    if (card) { m.map = card.dataset.map; mark(); }
  });
  $("menu-maps").addEventListener("dblclick", (e) => { if (e.target.closest(".map-card")) $("menu-start").click(); });
  $("menu-size").onchange = (e) => { m.size = Number(e.target.value); preview(); };
  $("menu-pop").onchange = preview;
  $("menu-seed").oninput = preview;
  $("menu-dice").onclick = () => { $("menu-seed").value = Math.floor(Math.random() * 1e6); preview(); };
  $("menu-cancel").onclick = closeMenu;
  $("menu-start").onclick = () => { send({ cmd: "reset", ...options() }); closeMenu(); };
  document.addEventListener("keydown", (e) => {
    if (!m.open) return;
    if (e.key === "Escape" && !m.first) closeMenu();
    else if (e.key === "Enter") $("menu-start").click();
  });
}
