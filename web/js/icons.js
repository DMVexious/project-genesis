/* Pixel icons, drawn in code, in place of the system's emoji wherever the world
 * shows what villagers are up to (over their heads, in the people list and in
 * their inventory). Each is a 9x9 grid of palette letters; a dark outline is
 * added around it, like every other sprite. An emoji with no icon here is
 * shown as itself. */

const PAL = {
  k: "#1d1712", w: "#fffaf0", r: "#d8463a", R: "#8a2a22", o: "#f08a32", y: "#f6d24a", b: "#5aa6e8", B: "#2f6f9f",
  g: "#5fb84a", G: "#3c7a32", n: "#8a5a32", N: "#4a3220", s: "#9a9aa4", S: "#e0e0e6", c: "#f2c9a0", p: "#f07ab0",
};

const ART = {
  "💤": ["bbbbb....", "...b.....", "..b......", ".b.......", "bbbbb....", ".....bbbb", "......b..", ".....b...", ".....bbbb"],
  "💭": [".ww.ww...", "wwwwwww..", "wwwwwwww.", ".wwwwwww.", "..wwwww..", ".........", "..ww.....", ".........", "ww......."],
  "💬": [".wwwwwww.", "wwwwwwwww", "wwkwkwkww", "wwwwwwwww", ".wwwwwww.", "..ww.....", ".ww......", ".........", "........."],
  "🤤": ["..yyyyy..", ".yyyyyyy.", "yykyyykyy", "yyyyyyyyy", "yykkkkkyy", "yykrrrkyy", ".yykkkyy.", "..yyyyb..", "......b.."],
  "🤰": [".pp...pp.", "pppp.pppp", "pwppppppp", "ppppppppp", "ppppppppp", ".ppppppp.", "..ppppp..", "...ppp...", "....p...."],
  "❤": [".rr...rr.", "rrrr.rrrr", "rwrrrrrrr", "rrrrrrrrr", "rrrrrrrrr", ".rrrrrrr.", "..rrrrr..", "...rrr...", "....r...."],
  "💀": ["..wwwww..", ".wwwwwww.", "wwwwwwwww", "wkkwwwkkw", "wkkwwwkkw", "wwwwkwwww", ".wwwwwww.", "..wkwkw..", "..wwwww.."],
  "🪓": ["...sss...", "..sSsss..", "..sSssn..", "...ssn...", ".....n...", "....n....", "...n.....", "..n......", ".n......."],
  "🔨": ["sssssss..", "sSSSSss..", "sssssss..", "...n.....", "...n.....", "...n.....", "...n.....", "...n.....", "...N....."],
  "⛏": [".sSSSSSs.", "s...n...s", "....n....", "....n....", "....n....", "....n....", "....n....", "....N....", "........."],
  "💢": [".rr...rr.", ".r.....r.", "rr.....rr", ".........", ".........", ".........", "rr.....rr", ".r.....r.", ".rr...rr."],
  "👶": ["....n....", "..ccccc..", ".ccccccc.", "ccckcckcc", "ccccccccc", "cccpppccc", ".ccccccc.", "..ccccc..", "........."],
  "🍒": ["......G..", ".....GG..", "....G.G..", "...G...G.", "..G....G.", ".rr...rr.", "rwrr.rwrr", "rrrr.rrrr", ".rr...rr."],
  "🍖": ["......ww.", ".....www.", "....wn...", "..nnnn...", ".nrrrrn..", "nrrwrrrn.", "nrrrrrrn.", ".nrrrrn..", "..nnnn..."],
  "🤝": [".........", "cc.....cc", "ccc...ccc", "cccccccc.", ".ccccccc.", ".ccckccc.", "..ccccc..", ".........", "........."],
  "🎁": ["..rr.rr..", "...rrr...", "ggggrgggg", "ggggrgggg", "rrrrrrrrr", "ggggrgggg", "ggggrgggg", "ggggrgggg", "........."],
  "🗡": [".......S.", "......SS.", ".....SS..", "....SS...", "...SS....", ".nyS.....", "..n......", ".n.y.....", "n........"],
  "⭐": ["....y....", "....y....", "...yyy...", "yyyyyyyyy", ".yyyyyyy.", "..yyyyy..", "..yy.yy..", ".yy...yy.", ".y.....y."],
  "🎣": [".......n.", "......n.w", ".....n..w", "....n...w", "...n....w", "..n.....b", ".n.......", "N........", "........."],
  "🌱": [".........", ".gg...gg.", "gggg.gggg", ".gggGggg.", "....G....", "....G....", "....G....", "..nnnnn..", ".nnnnnnn."],
  "🏠": ["....r....", "...rrr...", "..rrrrr..", ".rrrrrrr.", "rrrrrrrrr", ".ccccccc.", ".cccNNcc.", ".cccNNcc.", ".cccNNcc."],
  "📦": [".........", "nnnnnnnnn", "nyNyyyNyn", "nyyNyNyyn", "nyyyNyyyn", "nyyNyNyyn", "nyNyyyNyn", "nnnnnnnnn", "........."],
  "🙏": ["....w....", "....y....", ".w..y..w.", "..yyyyy..", "wyyywyyyw", "..yyyyy..", ".w..y..w.", "....y....", "....w...."],
  "🏹": [".....SSS.", "......SS.", ".....n.S.", "....n....", "...n.....", "..n......", "wn.......", ".w.......", "........."],
  "🐺": ["r..r..r..", ".r..r..r.", ".r..r..r.", "..r..r..r", "..r..r..r", "...r..r..", ".........", ".........", "........."],
  "💦": ["....b....", "...bb....", "..bbbb...", "..bwbb.b.", "..bbbb.bb", "...bb.bbb", ".......b.", ".........", "........."],
  "🔥": ["....r....", "...rr....", "..rro.r..", "..rooor..", ".rooyor..", ".royyyor.", ".royyyor.", "..ryyyr..", "...rrr..."],
  "⚡": [".....yy..", "....yy...", "...yy....", "..yyyyy..", "....yy...", "...yy....", "..yy.....", ".yy......", ".y......."],
  "🤒": ["......ww.", ".....wrw.", "....wrw..", "...wrw...", "..wrw....", ".rrr.....", "rrrr.....", "rrr......", "........."],
  "💪": ["...ggg...", "...ggg...", "...ggg...", "ggggggggg", "ggggggggg", "ggggggggg", "...ggg...", "...ggg...", "...ggg..."],
  "🥶": ["....b....", ".b..b..b.", "..b.b.b..", "...bbb...", "bbbbwbbbb", "...bbb...", "..b.b.b..", ".b..b..b.", "....b...."],
  "🐟": [".........", "..bbbb...", ".bbbbbb.b", "bkbbbbbbb", "bbbbbbbbb", ".bbbbbb.b", "..bbbb...", ".........", "........."],
  "🪵": [".........", "..nnnnnnn", ".yynnnnnn", "yNyynnnnn", "yyNynnnnn", ".yynnnnnn", "..nnnnnnn", ".........", "........."],
  "🪨": [".........", "...sss...", "..sSsss..", ".sSsssss.", ".ssssssss", "sssssssss", "sssssssss", ".........", "........."],
};
// the same picture for more than one emoji
const SAME = { "🏗": "🔨", "🏘": "🏠", "✨": "🙏", "🛠": "🔨", "🪣": "💦", "⛩": "🙏", "🍓": "🍒", "🍎": "🍒", "🍄": "🍒" };

const urls = new Map();
const norm = (e) => String(e || "").replace(/️/g, "");

export function iconUrl(emoji) {
  let e = norm(emoji);
  e = SAME[e] || e;
  if (urls.has(e)) return urls.get(e);
  const art = ART[e];
  let url = "";
  if (art) {
    const cv = document.createElement("canvas");
    cv.width = cv.height = 11;
    const g = cv.getContext("2d");
    g.fillStyle = PAL.k;  // the outline: every filled pixel's neighbours, drawn first
    art.forEach((row, y) => [...row].forEach((c, x) => { if (c !== ".") g.fillRect(x, y, 3, 3); }));
    art.forEach((row, y) => [...row].forEach((c, x) => {
      if (c === "." ) return;
      g.fillStyle = PAL[c] || "#ff00ff";
      g.fillRect(x + 1, y + 1, 1, 1);
    }));
    url = cv.toDataURL();
  }
  urls.set(e, url);
  return url;
}

// An emoji as HTML: its pixel icon when there is one, else the emoji itself.
// (Several emoji in a row, like "🪓🌲", are each looked up.)
export function ico(text, cls = "") {
  if (!text) return "";
  const parts = [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(String(text))].map((s) => s.segment);
  return parts.map((e) => {
    const url = iconUrl(e);
    return url ? `<img class="px-ico ${cls}" src="${url}" alt="${e}" draggable="false">`
      : e.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }).join("");
}
