<p align="center">
  <img src="docs/media/logo.svg" alt="Project Genesis" width="100%">
</p>

<p align="center">
  <b>A village of LLM-driven NPCs that forage, build, trade, fight, fall in love, grow old and die.</b><br>
  Nothing is scripted. A deterministic C++ engine owns the world, and a small local LLM only ever picks from the engine's list of legal moves.
</p>

<p align="center">
  <img src="docs/media/hero.gif" alt="A live run: villagers argue beside their huts, campfires and two fresh graves" width="100%">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/C%2B%2B-17-00599C?logo=cplusplus&logoColor=white" alt="C++17">
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-WebSocket-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Phaser-3.90-8E44AD" alt="Phaser">
  <img src="https://img.shields.io/badge/Tauri-2-24C8DB?logo=tauri&logoColor=white" alt="Tauri">
  <img src="https://img.shields.io/badge/Ollama-local%20LLM-000000?logo=ollama&logoColor=white" alt="Ollama">
  <img src="https://img.shields.io/badge/llama.cpp-GBNF-6E4C13" alt="llama.cpp GBNF">
  <img src="https://img.shields.io/badge/ChromaDB-RAG%20memory-FF6446" alt="ChromaDB">
  <img src="https://img.shields.io/badge/Windows-installer-0078D6?logo=windows&logoColor=white" alt="Windows">
</p>

<p align="center">
  <a href="#quick-start"><b>Quick start</b></a> &nbsp;|&nbsp;
  <a href="#the-llm-cant-cheat"><b>How the AI is kept honest</b></a> &nbsp;|&nbsp;
  <a href="#reference"><b>Game rules and internals</b></a>
</p>

## Overheard in the village

> **Tonol**, mid-fight: *"You call me the lost king? I will bite you for eating dirt."*<br>
> **Kyxan:** *"I am not a lost king, just another man who eats the mud."*

> **Vykael:** *"You said they remember me as a corpse. My face feels cold like stone."*<br>
> **Bisil:** *"Your eyes look exactly how the dead stare when their hearts stop beating."*

> **Asoxyl:** *"Your huts steal dreams, but you don't sleep. Rest now before the fire."*<br>
> **Mux:** *"They say huts steal dreams? I sleep anyway when there's wood."*

> **Elaox:** *"Names are borrowed, so return mine now."*<br>
> **Krasyth:** *"Your name is a dead coin that must be exchanged."*

Every line above was written by a 2B-parameter model running locally (`bonsai-2:2b` on Ollama) during a single run, and nothing was edited. The last exchange shows a belief spreading: the idea that names are borrowed from the dead passed from villager to villager in conversation, and within the first three minutes of the run Krasyth, Elaox, Tonol and Mux were all arguing about it.

## Gallery

| A village grows | A conversation | One life |
| :---: | :---: | :---: |
| <img src="docs/media/village.gif" alt="Time-lapse: an empty field fills with huts and campfires" width="270"> | <img src="docs/media/talk.gif" alt="A crowd at a campfire trading lines in speech bubbles" width="270"> | <img src="docs/media/life.gif" alt="The camera follows one villager through an argument" width="270"> |
| The first 2,000 ticks of a run. Every hut and campfire is a villager's own decision | Eleven villagers at one campfire. Each bubble comes straight from the LLM, shaped by remembered grudges | Following Elaox while the belief that names are borrowed from the dead turns into an argument |

All clips are the simulation running live on a local model through Ollama. No events are scripted.

## The LLM can't cheat

Small models are bad at rules, so Genesis never asks them to follow any. Every tick the C++ engine works out what each villager is *allowed* to do, and the model's answer is constrained by a grammar to exactly those options. It can be rude, paranoid or strange, but it can never make an illegal move.

What one villager sees on its turn (abridged from `python -m genesis.terminal --show-prompt`):

```text
You are Doian, an NPC surviving in a harsh, barren world.
Your traits are [Hostile, Lazy, Charismatic]. Act accordingly.

health 100 · fullness 97 · energy 82 · wood 0 · home: none

valid_actions:
  0  Sleep (restore energy)
  1  Walk toward the nearest tree (2 steps)
  2  Walk toward the nearest berry bush (10 steps)
  3  Walk toward Komae (14 steps)
  4  Walk toward Thamem (17 steps)
  5  Wander randomly
Reply with only the number of the action you take.
```

And the grammar its reply must match. Anything else can't be generated:

```gbnf
root ::= "0" | "1" | "2" | "3" | "4" | "5"
```

Conversations get the same treatment: an option the engine offered plus at most 14 words.

```gbnf
root ::= opt " | " word (" " word){0,13}
opt  ::= "talk" | "fight" | "trade"
word ::= [A-Za-z',.!?-]{1,16}
```

Each villager also remembers who hurt or helped them (ChromaDB retrieval), so those memories go into the next prompt. That's where the grudges, alliances and occasional cults come from.

## How it fits together

```mermaid
flowchart LR
    UI["<b>web/</b><br/>Phaser world + DOM overlays"]
    ORCH["<b>orchestrator/</b><br/>FastAPI, Python"]
    BRAIN["<b>brain.py</b><br/>Ollama or llama-server<br/>grammar-constrained"]
    MEM["<b>memory.py</b><br/>ChromaDB grudges and alliances"]
    TRAITS["<b>traits.py</b><br/>genetics, looks, personas"]
    ENG["<b>engine/</b> C++17<br/>grid, BFS pathing, stats,<br/>combat, building, births"]
    UI <-->|WebSocket| ORCH
    ORCH --> BRAIN
    ORCH --> MEM
    ORCH --> TRAITS
    ORCH <-->|"stdin/stdout, one JSON line per tick"| ENG
```

### Roadmap

| Phase | State |
|---|---|
| 1 Terminal prototype | ✅ C++ engine, Python orchestrator, ASCII terminal view, prompt/grammar inspector |
| 2 Local web UI | ✅ Phaser world, procedural pixel art, emojis, 14-word speech bubbles (with replies) that fade 4 ticks later |
| 3 RAG & genetics | ✅ ChromaDB memories feed interaction prompts; 50/50 trait splicing with 5% mutation |
| 4 Tauri packaging | ✅ Tauri v2 shell + orchestrator/engine sidecars → NSIS installer |

## Quick start

Windows / PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r orchestrator\requirements.txt
.\engine\build.ps1                       # optional: without it the Python reference engine is used

cd orchestrator
..\.venv\Scripts\python -m genesis.server        # open http://127.0.0.1:8765
..\.venv\Scripts\python -m genesis.terminal      # Phase 1 ASCII view
..\.venv\Scripts\python -m genesis.terminal --show-prompt   # inspect an NPC prompt + GBNF grammar
..\.venv\Scripts\python -m unittest discover -s tests -v
```

If [Ollama](https://ollama.com) is installed, it's used automatically with no setup. Without any LLM, villagers fall back to a heuristic brain, so the sim still runs.

Common flags (both entry points): `--seed`, `--width`, `--height`, `--population`,
`--engine auto|cpp|python`, `--brain auto|llm|heuristic`, `--llm-url`,
`--memory auto|chromadb|simple`, `--memory-path DIR` (persist ChromaDB).

To film it: `?bare=1` hides the panels, `?zoom=2.5` sets the zoom, `?center=<x>,<y>` points the camera at a tile and `?follow=<npc id>` follows a villager.

## Reference

<details>
<summary><b>Using a real LLM:</b> Ollama, llama.cpp and hybrid cognition</summary>

**Ollama (default):** if Ollama is installed, `--brain auto` uses it with no setup.
The orchestrator starts `ollama serve` if it isn't running, pre-loads the model
at launch (the first cold load can take about a minute), then each NPC call takes about 0.5 s.

```
--model bonsai-2:2b          # default (more in character); llama3.2:1b is ~2x faster
                             # or set GENESIS_MODEL (works for the desktop app too)
--brain ollama               # force Ollama;  --ollama-url to point elsewhere
```

Ollama has no GBNF parameter, so its requests use a JSON schema instead, which
Ollama compiles to a grammar: `{"action": <enum of valid indices>}`,
`{"option": <enum of engine options>, "line": str}`, `{"agree": bool}`.
Note: `OLLAMA_NUM_PARALLEL` gave no speed-up with bonsai-2:2b on an RTX 5060 (0.30 s/call serial vs 0.32 s at 4-way), so leave it at the default.

**Hybrid cognition.** Each NPC decision comes from one of three sources, shown in the UI:

* 🧠 **LLM:** when someone new is adjacent (a social moment), plus a goal check every `--llm-every` ticks (default 18). At most `--llm-budget` decision calls in flight at once (default 2), social moments first and then the most overdue. Separately, at most 3 conversations (`talk_budget`) use the LLM at once, and each NPC waits ~90 ticks (`talk_cooldown`) between conversations it starts. An NPC that can't get an LLM slot does something else instead of talking, so **every line is written by the LLM**. Pre-written lines are only used when the LLM is down or paused for being slow, and an LLM call that fails mid-conversation stays silent.
* ⚡ **reflex:** survival and routine choices (energy < 20, fullness < 30, only one option, or over budget), made instantly by the heuristic brain.
* ↻ **plan:** keep walking toward the goal already chosen until arrival or interruption (hurt, someone new adjacent, starving or exhausted, or after 12 ticks).

Interactions (dialogue, fight/trade/mate, consent) use the LLM too.

**Slow or stalled LLM.** A thinking NPC stands still while hunger and energy fall, so the sim
drops any call still unanswered after 40 ticks (~6 s), or sooner if the NPC is close to starving
or collapsing, and acts on reflex instead. A missed deadline also pauses LLM routing (40 ticks,
doubling on repeat misses up to 400); the UI shows `brain: … (paused: slow)` meanwhile.
ChromaDB embedding and recall run in a worker thread so they never stall the tick loop.
The headless terminal runner (`--headless`) waits for in-flight LLM calls each tick instead.
Measured with bonsai-2:2b on an RTX 5060 over 40 ticks: 10 NPCs ~1 s/tick (from 3.2 s when every decision used the LLM), and 30 NPCs ~4.3 s/tick with no tick over 6 s.

**llama.cpp:** `llama-server -m model.gguf --port 8080 --parallel 8 -c 8192`.
`auto` prefers it when `/health` answers. These requests use GBNF grammars:

* action choice: `root ::= "0" | "1" | …`, meaning only indices the engine offered;
* interaction: `<option> | <1–14 words>`;
* reply (the listener always answers): `<1–14 words>`;
* consent (trade/mate): `yes | no`.
Any failed or timed-out call falls back to the heuristic for that NPC, so a slow
model never stalls the tick loop.

</details>

<details>
<summary><b>Personas and conversations:</b> beliefs, cults and two-sided dialogue</summary>

Every NPC is born with one strange belief ("keeps a rock named Grumm and asks it for advice")
and one way of talking ("speaks only in riddles"); both show in the NPC panel.

* **Inherited:** a child takes each from a random parent, with a 25% chance of a fresh one.
* **Contagious:** each conversation has an 8% chance (`convert_chance`) to convert the listener
  to the speaker's belief. Charismatic speakers convert twice as often, Paranoid listeners resist
  (×0.25), and nobody is converted by someone they hold a grudge against. Cults and feuding
  folklores form on their own; conversions show in the log as 🌀.
* **Two-sided:** the listener always replies, including when refusing a trade or mate, and both
  sides remember the exchange.
* **No small talk:** prompts carry the persona, memories and recent village news; lines that are
  generic ("hello", "how are you", "we must survive") or copied from the tone examples get
  re-rolled once. Speech runs at a higher temperature than decisions, with repeat penalties.

</details>

<details>
<summary><b>Time, pacing and the camera</b></summary>

* **Asynchronous NPCs.** The world ticks every ~150 ms (`--tick-ms`) and never waits for the LLM.
  Every action lasts several ticks: a step ~2, chopping ~5, sleeping ~7, building ~10. That is
  scaled by each NPC's own pace (0.8 to 1.5x) plus random jitter, so NPCs act out of step with each
  other. Bonsai calls run in the background: an NPC shows 💭 while it waits, and everyone else
  carries on. At most `--llm-budget` (default 2) decision calls and 3 conversations are in flight at once.
* **World time.** Hunger, energy, ageing and regrowth advance once every `decay_every` = 3 ticks
  (the optional 5th `INIT` argument), so survival balance holds with multi-tick actions.
* **Map.** The default is 80x56 with 16 settlers (`--width/--height/--population`). The view is a
  camera: drag to pan, scroll to zoom, WASD or arrow keys, click an NPC to follow it.
* Speech bubbles fade 24 ticks (~3.5 s) after they appear, which is the PRD's "4 ticks" rescaled for the shorter tick.

</details>

<details>
<summary><b>Game rules:</b> tunable constants at the top of both engine files</summary>

Times below are in units of world time (one unit every `decay_every` = 3 ticks).

* Tiles: dirt, rock, tree → stump (regrows in 60), berry bush (regrows in 40), and the buildings below.
* Stats 0–100. Fullness −1/unit, energy −1 every 2 units plus labour costs; at 0 either one drains health.
* Chop +4 wood, forage +2 food from a bush or +3 from a ripe farm, eat +30 fullness, sleep +20 energy.
* **Buildings are the NPC's choice.** With enough wood the engine offers one build action per type
  and only picks the nearest free tile; which to build (and whether to) is up to the brain:
  * **Hut** (10 wood): sleep +35 energy and a small heal. An NPC's first hut becomes its home.
  * **Farm** (6 wood): planted as growing crops; ripens in 25 and can be harvested again and again.
  * **Campfire** (3 wood): eating beside it is a cooked meal (+15 more fullness); sleeping beside it +28.
  * With an LLM brain, building is always an LLM decision: an NPC with wood to spare gets an LLM
    turn, and reflexes never build (unless the LLM is paused for being slow).
* **Homes** pass to the eldest living child without one when the owner dies; otherwise the hut stands
  empty until a homeless NPC sleeps beside it and moves in.
* 5 wood → spear (+12 damage). Fight: 75% hit for 8–15 damage, 50% retaliation; the killer loots wood and food.
* **Life cycle:** children grow up at age 60; from age 700 an elder may die of old age at any unit
  (chance (age − 700) / 40000 per unit, so most die between ~800 and ~1000). Elders cannot have children.
  At the default 150 ms/tick, an NPC lives roughly 5–7 minutes.
* Mate: both adults and not elders, social urge ≥ 30 each, fed, healthy, nobody else within 1 tile,
  population < 30, and neither parent had a child in the last 100 units of world time (~45 s).
  Talking lowers the urge by 5, mating resets it to 0.
* Courtship (orchestrator): "mate" is only offered once both NPCs are fond of each other
  (affection ≥ 2 each way, i.e. a couple of friendly exchanges), and the partner must still consent.
* Instinct (orchestrator): small models almost never start a family on their own (bonsai-2:2b chose
  "mate" for 1 in 18 offers and refused 21 of 27 proposals, so LLM villages died out). When "mate" is
  on offer, the heuristic decides whether to propose and whether the partner accepts; the LLM still
  writes the proposal and the reply. Trades stay fully LLM-decided.
* In heuristic runs of 12,000 ticks, 5 of 6 seeds held a stable population (20+ generations);
  one collapsed after a run of fights.

</details>

<details>
<summary><b>Looks:</b> inherited, procedurally drawn appearances</summary>

Every NPC inherits an appearance from its parents the same way as its traits (50/50 per slot,
5% mutation): skin tone, hair colour and style, outfit (tunic, robe, vest, furs, overalls),
outfit colour and hat. The front end draws each combination procedurally; children are smaller
and elders turn white-haired and walk with a stick. The coloured ring under each NPC's feet shows
its temperament, and a gravestone marks where someone died for about a minute.
URL options: `?zoom=2.5` sets the starting zoom, `?follow=<npc id>` follows an NPC.

</details>

<details>
<summary><b>How a tick works</b></summary>

1. The engine's last reply holds the grid, every NPC's stats and its valid actions.
2. Python builds a JSON prompt per NPC: stats + `"Your traits are [...]. Act accordingly."` + numbered actions.
3. All NPCs are inferred concurrently (llama-server batches them with `--parallel`).
4. NPCs that chose **Interact** run the secondary sequence: ChromaDB recall for that
   pair → option + dialogue line → the target consents (trade/mate) or refuses.
5. `STEP id:action:option …` goes to the engine, which resolves movement, combat
   RNG, trades, hut placement and births, then decays stats.
6. Events become memories (attacks −4, trades +2, births +3 …) that make grudges and alliances.
7. A frame (tiles, NPCs, emojis, dialogue, chronicle) is broadcast to the UI.

</details>

<details>
<summary><b>Engine protocol</b></summary>

One command per line on stdin, one JSON object per line on stdout:

```
INIT <seed> <width> <height> <npcs>
STATE
STEP <id>:<action-index>[:<interact-option-index>] ...
QUIT
```

`orchestrator/genesis/engine_py.py` is a line-for-line Python mirror of
`engine/src/main.cpp` (same SplitMix64 RNG and call order). `tests/test_genesis.py`
asserts that both produce identical states over 1,500 ticks of random actions,
so **any rule change must be made in both files**.

</details>

<details>
<summary><b>Desktop app (Phase 4):</b> Tauri build and Windows installer</summary>

```powershell
.venv\Scripts\pip install pyinstaller
npm install                                  # Tauri CLI
.\scripts\build-desktop.ps1                  # add -SkipOrchestrator to reuse orchestrator\dist\orchestrator.exe
```

Output: `src-tauri\target\release\bundle\nsis\Project Genesis_0.1.0_x64-setup.exe`
(also runnable in place: `src-tauri\target\release\genesis.exe`).

Needs Rust (rustup, MSVC toolchain), the VS 2022 C++ Build Tools and WebView2 (built into Windows 11).
At launch Tauri starts `orchestrator.exe --port 8765 --parent-pid <app pid>`; the
orchestrator starts `engine.exe` from the same folder. Closing the window kills the
orchestrator, and its parent-PID watchdog also exits it if the app is killed outright.
Port 8765 must be free (stop any `genesis.server` dev instance first).

</details>
