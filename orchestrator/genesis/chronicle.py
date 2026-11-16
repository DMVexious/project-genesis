"""The chronicle: the land's own written history.

Each in-game day the notable things that happened (births, deaths, homes built,
fevers, wildfires, the god's doings...) are written up by the talk model as a
short entry, like an old village record. Notable people get a few lines when they
die, and what the villagers have seen the sky do becomes a myth. A plain
template stands in whenever no language model is free.

What is written is remembered: every villager alive learns each entry and myth as
a story, so the past comes up in their talk ("remember the flood?").

The engine's facts are the only material: the model is told to use nothing else.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter, deque

from .engine.defs import years

log = logging.getLogger("genesis.chronicle")

KEEP = 80            # entries kept (the oldest are forgotten)
MAX_FACTS = 12       # lines given to the writer for one day
MYTH_AFTER = 2       # god's acts seen before the villagers make a story of them

# How much a log line matters, by how it starts (its emoji). Unlisted lines are left out.
WEIGHT = {"👶": 5, "💀": 6, "🪦": 5, "⚡": 6, "💞": 3, "🏠": 3, "🏘️": 5, "🏚️": 4, "🌀": 2, "🤒": 3, "🔥": 4,
          "❄️": 3, "🌱": 2, "🍂": 2, "🌊": 5, "🥵": 4, "⛈️": 2, "🐺": 3, "🎉": 4, "☄️": 5, "🌑": 5, "🧳": 4,
          "🧙": 4, "🧺": 3, "🙏": 3, "✨": 4, "❤️": 3, "🍖": 2, "🎮": 2, "🏹": 1}
COUNTED = {"attacks": "fights", "strikes back": None, "trades": "trades", "gives": "gifts"}
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*[^<>]*>")
_EMOJI = re.compile(r"^[^\w\"'(]+")


def _weight(line: str) -> int:
    for mark, w in WEIGHT.items():
        if line.startswith(mark):
            return w
    return 0


def _plain(line: str) -> str:
    return _EMOJI.sub("", line).strip()


def _clean(text: str) -> str:
    """A model's prose, tidied: no markup, headings, bullets or quote marks around it."""
    text = _TAG.sub(" ", text or "")
    lines = [ln.strip(" -*#>\t") for ln in text.splitlines()]
    text = " ".join(ln for ln in lines if ln and not re.match(r"^(day|entry|record)\b.*:$", ln, re.I))
    text = re.sub(r"\*+|_{2,}", "", text)  # (markdown bold and italics)
    text = re.sub(r"\s{2,}", " ", text).strip().strip('"“”').strip()
    return text


class Chronicle:
    def __init__(self):
        self.entries: list[dict] = []   # {kind: day|life|myth, day, title, text, tick, llm}
        self.rev = 0
        self.day: int | None = None
        self.facts: list[tuple[int, str]] = []
        self.counts: Counter = Counter()
        self.deeds: dict[str, deque] = {}   # name -> their notable lines, for obituaries
        self.kills: Counter = Counter()
        self.god_seen: list[str] = []
        self.myth = ""
        self._tasks: set = set()

    # (tasks in flight are not saved)
    def __getstate__(self):
        return {k: v for k, v in self.__dict__.items() if k != "_tasks"}

    def __setstate__(self, d):
        self.__dict__.update(d)
        self._tasks = set()

    def recent(self, n: int = 40) -> list[dict]:
        return self.entries[-n:]

    # --- every tick ---------------------------------------------------------------------
    def note(self, sim, now: int, lines: list[str], god_lines: list[str]):
        clock = sim.state.get("clock", {})
        day = clock.get("day")
        if self.day is None:
            self.day = day
        elif day is not None and day != self.day:
            if self.facts:
                self._spawn(self._write_day(sim, self.day, clock.get("season", ""), self.facts, dict(self.counts), now))
            self.day, self.facts, self.counts = day, [], Counter()
        names = {m.name for m in sim.meta.values()}
        god = set(god_lines)
        for line in lines:
            if " talks to " in line:
                continue
            for word, what in COUNTED.items():
                if f" {word} " in line and what:
                    self.counts[what] += 1
            w = 6 if line in god else _weight(line)
            if not w:
                continue
            self.facts.append((w, line))
            for who in {n for n in names if n in line}:  # (whole names only)
                if re.search(rf"\b{re.escape(who)}\b", line):
                    self.deeds.setdefault(who, deque(maxlen=6)).append(_plain(line))
            m = re.search(r"was killed by (\w+)", line)
            if m:
                self.kills[m.group(1)] += 1
        if len(self.facts) > 200:  # (a very busy day: keep the weightiest)
            self.facts = sorted(self.facts, key=lambda f: -f[0])[:120]
        if god_lines:
            self.god_seen.extend(_plain(g) for g in god_lines)
            if len(self.god_seen) >= MYTH_AFTER:
                acts, self.god_seen = self.god_seen[-6:], []
                self._spawn(self._write_myth(sim, acts, now))

    def died(self, sim, npc_id: int, cause_line: str, now: int):
        """Someone died: notable people get a few lines of their own."""
        m = sim.meta.get(npc_id)
        if m is None:
            return
        kids = sum(1 for o in sim.meta.values() if npc_id in o.parents)
        deeds = list(self.deeds.get(m.name, ()))
        founder = m.generation == 0
        if not (founder or kids >= 3 or self.kills[m.name] or len(deeds) >= 3):
            return
        body = next((n for n in sim.state["npcs"] if n["id"] == npc_id), {})
        facts = {
            "name": m.name, "age": years(body.get("age", 0)), "how": _plain(cause_line), "children": kids,
            "founder": founder, "role": body.get("role", ""), "village": sim.village_name(body.get("village", -1)),
            "killed": self.kills[m.name], "deeds": deeds[-5:],
            "belief": getattr(m, "persona", None) and _belief(m),
        }
        self._spawn(self._write_life(sim, facts, now))

    # --- writing ------------------------------------------------------------------------------
    def _spawn(self, coro):
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # (no loop: e.g. a plain script) write it at once with the template
            coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _ask(self, sim, system: str, user: str, max_tokens: int) -> str:
        """The talk model's prose, or "" when none is free (the caller then uses its template)."""
        talker = getattr(sim, "talker", None)
        if talker is None or not hasattr(talker, "_text") or sim.llm_paused(sim.state["tick"]):
            return ""
        try:
            text = await asyncio.wait_for(
                talker._text([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens), 90)
        except Exception as e:  # (busy, down, slow: the template will do)
            log.info("chronicle: no model free (%s)", e)
            return ""
        text = _clean(text)
        return text if re.search(r"[A-Za-z]{3,}", text) and len(text) > 20 else ""

    def _add(self, sim, entry: dict, now: int):
        entry["tick"] = now
        self.entries.append(entry)
        del self.entries[:-KEEP]
        self.rev += 1
        # what is written down is remembered: everyone alive learns it as a story
        story = f"{entry['title']}: {entry['text']}" if entry["kind"] != "myth" else f"We say: {entry['text']}"
        for n in sim.state["npcs"]:
            if n["alive"]:
                sim._remember(n["id"], -1, now, "story", story[:300], 0)

    async def _write_day(self, sim, day: int, season: str, facts, counts, now: int):
        top = sorted(facts, key=lambda f: -f[0])[:MAX_FACTS]
        top = [line for _w, line in sorted(top, key=lambda f: facts.index(f))]  # (back in the order they happened)
        also = ", ".join(f"{n} {what}" for what, n in counts.items() if n)
        land = sim.cfg.map_name.replace("_", " ")
        text = await self._ask(
            sim,
            f"You keep the chronicle of a small settlement in the {land}. Write the record of one day as two to four "
            "short, plain sentences, like an old village diary: calm, matter-of-fact, no poetry. Use only the facts "
            "given and keep everyone's names. Invent nothing. No lists, no headings, no quotation marks.",
            f"Day {day}{', ' + season if season else ''}. What happened:\n" + "\n".join(f"- {_plain(t)}" for t in top)
            + (f"\nAlso that day: {also}." if also else ""),
            160)
        llm = bool(text)
        if not text:  # the template: the three weightiest things, as they were logged
            best = [_plain(line) for _w, line in sorted(facts, key=lambda f: -f[0])[:3]]
            text = ". ".join(best) + "." + (f" Also: {also}." if also else "")
        self._add(sim, {"kind": "day", "day": day, "title": f"Day {day}", "text": text, "llm": llm}, now)

    async def _write_life(self, sim, f: dict, now: int):
        facts = [f"{f['name']}, {f['age']} years old" + (f", {f['role']}" if f["role"] else "")
                 + (f", of {f['village']}" if f["village"] and not f["village"].startswith("#") else ""),
                 f"How they died: {f['how']}"]
        if f["founder"]:
            facts.append("One of the first settlers of this land")
        if f["children"]:
            facts.append(f"Had {f['children']} children")
        if f["killed"]:
            facts.append(f"Killed {f['killed']} {'person' if f['killed'] == 1 else 'people'}")
        if f["belief"]:
            facts.append(f"Believed {f['belief']}")
        facts += [f"Remembered for: {d}" for d in f["deeds"]]
        text = await self._ask(
            sim,
            "You keep a village's chronicle. Write two or three short, plain sentences remembering someone who just "
            "died: who they were and what they'll be remembered for. Honest, not flowery; mention bad deeds too. "
            "Use only the facts given. No headings, no quotation marks.",
            "\n".join(f"- {x}" for x in facts), 130)
        llm = bool(text)
        if not text:
            bits = [f"{f['name']} died at {f['age']}"]
            if f["founder"]:
                bits.append("one of the first settlers")
            if f["children"]:
                bits.append(f"leaving {f['children']} children")
            text = ", ".join(bits) + "." + (f" Remembered for this: {f['deeds'][-1]}." if f["deeds"] else "")
        self._add(sim, {"kind": "life", "day": self.day, "title": f"In memory of {f['name']}", "text": text, "llm": llm},
                  now)

    async def _write_myth(self, sim, acts: list[str], now: int):
        text = await self._ask(
            sim,
            "Villagers who have seen strange things come from the sky tell a story about it among themselves. Write "
            "that story in one or two short sentences, the way simple farmers would tell it: what they think the sky "
            "is and what it wants. Base it only on what they saw. No headings, no quotation marks.",
            ("What they believed before: " + self.myth + "\n" if self.myth else "")
            + "What they saw:\n" + "\n".join(f"- {a}" for a in acts), 90)
        llm = bool(text)
        if not text:
            text = f"Something in the sky watches us. It has shown itself: {acts[-1].rstrip('.')}."
        self.myth = text
        self._add(sim, {"kind": "myth", "day": self.day, "title": "What they say about the sky", "text": text,
                        "llm": llm}, now)

    async def settle(self):
        """Wait for what is being written (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _belief(m) -> str:
    try:
        from .traits import belief_text
        return belief_text(m.persona).rstrip(".")
    except Exception:
        return ""
