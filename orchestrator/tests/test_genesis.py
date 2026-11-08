"""Run from orchestrator/:  python -m unittest discover -s tests -v"""
import asyncio
import json
import random
import re
import unittest
from collections import Counter

from genesis.brain import (MAX_WORDS, HeuristicBrain, Interaction, LlamaBrain, NpcView, action_grammar,
                           action_prompt, clamp_words, interaction_grammar, interaction_prompt, is_generic,
                           reply_prompt)
from genesis.engine_client import BinaryEngine, PyEngine, find_engine_binary
from genesis.engine_py import Rng
from genesis.memory import Memory, SimpleMemory
from genesis.sim import SimConfig, Simulation
from genesis.traits import BELIEFS, POOLS, VOICES, Genome, random_genome, splice


def scripted_choices(state, rng):
    """Deterministic pseudo-random choices that exercise every action kind."""
    out = {}
    for key, acts in sorted(state["actions"].items(), key=lambda kv: int(kv[0])):
        i = rng.randrange(len(acts))
        opt = rng.randrange(len(acts[i]["options"])) if acts[i]["kind"] == "interact" else 0
        out[int(key)] = (i, opt)
    return out


class EngineTests(unittest.TestCase):
    def test_rng_known_values(self):
        # SplitMix64 reference values for seed 0 (shared with the C++ engine).
        r = Rng(0)
        self.assertEqual(r.next(), 0xE220A8397B1DCDAF)
        self.assertEqual(r.next(), 0x6E789E6AA1B965F4)

    def test_deterministic(self):
        def run():
            e, rng, s = PyEngine(), random.Random(3), None
            s = e.init(11, 20, 15, 8)
            for _ in range(200):
                s = e.step(scripted_choices(s, rng))
            return s
        self.assertEqual(run(), run())

    def test_actions_are_valid_and_invariants_hold(self):
        e, rng = PyEngine(), random.Random(5)
        s = e.init(4, 24, 18, 10)
        kinds, fails = Counter(), 0
        for _ in range(400):
            for acts in s["actions"].values():
                self.assertEqual(acts[0]["kind"], "sleep")  # always available
            s = e.step(scripted_choices(s, rng))
            kinds.update(ev["type"] for ev in s["events"])
            fails += sum(ev["type"] == "fail" and ev["kind"] == "invalid" for ev in s["events"])
            for n in s["npcs"]:
                for k in ("health", "hunger", "energy", "social"):
                    self.assertTrue(0 <= n[k] <= 100, (k, n))
                if n["alive"]:
                    self.assertIn(s["tiles"][n["y"]][n["x"]], ".s", "NPC inside a solid tile")
        self.assertEqual(fails, 0)
        for k in ("chop", "forage", "eat", "sleep", "talk", "attack"):
            self.assertIn(k, kinds)

    def test_zero_energy_only_sleep(self):
        e = PyEngine()
        e.init(1, 10, 10, 1)
        npc = e._proc.world.npcs[0]
        npc.energy = 0
        self.assertEqual(e._proc.world.generate_actions(npc), [{"kind": "sleep"}])

    def test_old_age_is_fatal(self):
        from genesis.engine_py import ELDER_AGE
        e = PyEngine()
        e.init(9, 12, 12, 3)
        for n in e._proc.world.npcs:
            n.age = ELDER_AGE + 400
        for _ in range(400):
            s = e.step({})
            for n in e._proc.world.npcs:  # keep them fed and rested: only age can kill
                n.hunger = n.energy = n.health = 100
        self.assertTrue(all(n["cause"] == "old age" for n in s["npcs"]), s["npcs"])

    def test_npcs_choose_what_to_build(self):
        from genesis.engine_py import FARM, FARM_EMPTY, FARM_REGROW, FIRE, HUT
        e = PyEngine()
        s = e.init(3, 14, 14, 1)
        w = e._proc.world
        n = w.npcs[0]
        n.wood = 4  # enough for a campfire only
        w.generate_all_actions()
        builds = [a["what"] for a in w.actions[n.id] if a["kind"] == "build"]
        self.assertEqual(builds, ["fire"])
        n.wood = 20
        w.generate_all_actions()
        acts = w.actions[n.id]
        self.assertEqual([a["what"] for a in acts if a["kind"] == "build"], ["hut", "farm", "fire"])
        idx = next(i for i, a in enumerate(acts) if a.get("what") == "farm")
        site = (acts[idx]["x"], acts[idx]["y"])
        s = e.step({n.id: (idx, 0)})
        self.assertEqual(s["events"][0], {"type": "build", "npc": n.id, "what": "farm", "x": site[0], "y": site[1]})
        self.assertEqual(w.tile(*site), FARM_EMPTY)
        self.assertEqual(n.wood, 14)
        for _ in range(FARM_REGROW):  # the crop ripens
            w.world_time(0)
        self.assertEqual(w.tile(*site), FARM)
        self.assertNotIn(HUT, "".join(s["tiles"]))
        # eating beside a campfire is a cooked meal
        w.set_tile(site[0], site[1], FIRE)
        n.x, n.y = next((site[0] + dx, site[1] + dy) for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0))
                        if w.passable(site[0] + dx, site[1] + dy))
        n.food, n.hunger = 1, 10
        w.generate_all_actions()
        s = e.step({n.id: (next(i for i, a in enumerate(w.actions[n.id]) if a["kind"] == "eat"), 0)})
        self.assertTrue(s["events"][0]["cooked"])
        self.assertEqual(n.hunger, 10 + 30 + 15 - 1)  # (-1: a unit of world time passed)

    def test_bad_commands(self):
        from genesis.engine_py import EngineProcess
        p = EngineProcess()
        self.assertIn("error", json.loads(p.handle("STEP 1:0")))
        p.handle("INIT 1 8 8 2")
        self.assertIn("error", json.loads(p.handle("STEP x:y")))


@unittest.skipUnless(find_engine_binary(), "C++ engine not built")
class ParityTests(unittest.TestCase):
    def test_cpp_matches_python(self):
        for seed, decay in ((1, 1), (42, 1), (999, 3)):
            py, cpp = PyEngine(), BinaryEngine(find_engine_binary())
            try:
                rng = random.Random(seed)
                a, b = py.init(seed, 28, 20, 12, decay), cpp.init(seed, 28, 20, 12, decay)
                self.assertEqual(a, b)
                for t in range(500):
                    ch = scripted_choices(a, rng)
                    a, b = py.step(ch), cpp.step(ch)
                    self.assertEqual(a, b, f"diverged at seed {seed} tick {t + 1}")
            finally:
                cpp.close()


class GeneticsTests(unittest.TestCase):
    def test_inheritance_and_mutation_rates(self):
        rng = random.Random(0)
        a = Genome("Pacifist", "Lazy", "Loner")
        b = Genome("Hostile", "Industrious", "Charismatic")
        from_a = mutated = 0
        n = 20000
        for _ in range(n):
            child, mut = splice(a, b, rng)
            mutated += len(mut)
            for slot in POOLS:
                if slot not in mut:
                    self.assertIn(getattr(child, slot), (getattr(a, slot), getattr(b, slot)))
                    from_a += getattr(child, slot) == getattr(a, slot)
        self.assertAlmostEqual(mutated / (n * 3), 0.05, delta=0.01)
        self.assertAlmostEqual(from_a / (n * 3 - mutated), 0.5, delta=0.02)

    def test_looks_are_inherited(self):
        from genesis.traits import LOOK_POOLS, random_look, splice_look
        rng = random.Random(2)
        a, b = random_look(rng), random_look(rng)
        for _ in range(200):
            child = splice_look(a, b, rng)
            self.assertEqual(set(child), set(LOOK_POOLS))
            for slot, v in child.items():
                self.assertIn(v, LOOK_POOLS[slot])

    def test_random_genome_uses_pools(self):
        g = random_genome(random.Random(1))
        for slot, pool in POOLS.items():
            self.assertIn(getattr(g, slot), pool)


class BrainTests(unittest.TestCase):
    def test_grammars(self):
        self.assertEqual(action_grammar(3), 'root ::= "0" | "1" | "2"\n')
        g = interaction_grammar(["talk", "fight"])
        self.assertIn('opt ::= "talk" | "fight"', g)
        self.assertIn("{0,13}", g)  # at most 14 words

    def test_clamp_words(self):
        self.assertEqual(len(clamp_words(" ".join(["w"] * 25)).split()), 14)

    def test_prompt_lists_only_engine_actions(self):
        s = PyEngine().init(2, 16, 12, 3)
        n = s["npcs"][0]
        v = NpcView(n["id"], "Ash", Genome("Hostile", "Lazy", "Loner"), n, s["actions"][str(n["id"])])
        msgs = action_prompt(v, {1: "Ash", 2: "Bo", 3: "Cy"})
        self.assertIn("Your traits are [Hostile, Lazy, Loner]. Act accordingly.", msgs[0]["content"])
        payload = json.loads(msgs[1]["content"].split("\n")[0])
        self.assertEqual(len(payload["valid_actions"]), len(v.actions))

    def test_llm_falls_back_when_server_down(self):
        async def go():
            b = LlamaBrain("http://127.0.0.1:9", HeuristicBrain(0), timeout=0.5)
            s = PyEngine().init(2, 16, 12, 2)
            n = s["npcs"][0]
            v = NpcView(n["id"], "Ash", Genome("Neutral", "Lazy", "Loner"), n, s["actions"][str(n["id"])])
            idx = await b.choose_action(v, {})
            ix = Interaction(v, v, ["talk", "fight"], [], 0)
            opt, line = await b.choose_interaction(ix)
            await b.close()
            return idx, opt, line, b.failures
        idx, opt, line, failures = asyncio.run(go())
        self.assertGreaterEqual(idx, 0)
        self.assertIn(opt, ("talk", "fight"))
        self.assertLessEqual(len(line.split()), MAX_WORDS)
        self.assertEqual(failures, 2)

    def test_dialogue_prompt_carries_persona_and_news(self):
        s = PyEngine().init(2, 16, 12, 2)
        a, b = s["npcs"][:2]
        me = NpcView(a["id"], "Ash", Genome("Neutral", "Lazy", "Loner"), a, [], persona={"belief": 3, "voice": 1})
        them = NpcView(b["id"], "Bo", Genome("Pacifist", "Lazy", "Loner"), b, [], persona={"belief": 5, "voice": 2})
        ix = Interaction(me, them, ["talk"], [], 0, ["Kora died of starvation"])
        sys_msg, user = (m["content"] for m in interaction_prompt(ix))
        self.assertIn(BELIEFS[3][0], sys_msg)
        self.assertIn(VOICES[1], sys_msg)
        self.assertNotIn(f'"{BELIEFS[3][1]}"', sys_msg)  # never shown its own catchphrase
        self.assertIn("Kora died", user)
        self.assertIn(BELIEFS[5][0], user)  # gossip about the listener
        self.assertIn("they_said", reply_prompt(ix, "Your shadow left.", "talk")[1]["content"])
        self.assertTrue(is_generic("Hello there, how are you?"))
        self.assertTrue(is_generic("Grumm says you smell bad."))  # parrots a tone example
        self.assertFalse(is_generic("Grumm says you smell bad.", own_belief=3))  # its own belief is fine
        self.assertFalse(is_generic("Your knees look delicious, Bo."))


class SimTests(unittest.TestCase):
    def test_sim_runs_and_logs_memories(self):
        async def go():
            sim = Simulation(PyEngine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7))
            frames = [await sim.tick() for _ in range(300)]
            return sim, frames
        sim, frames = asyncio.run(go())
        self.assertEqual(frames[-1]["tick"], 300)
        self.assertGreater(sim.memory.count(), 0)
        for f in frames:
            for d in f["dialogue"]:
                self.assertLessEqual(len(d["text"].split()), MAX_WORDS)
            for n in f["npcs"]:
                self.assertIn(n["id"], sim.meta)
        births = [m for m in sim.meta.values() if m.parents]
        for child in births:
            pa, pb = (sim.meta[p].genome for p in child.parents)
            for slot in POOLS:
                if slot not in child.mutated:
                    self.assertIn(getattr(child.genome, slot), (getattr(pa, slot), getattr(pb, slot)))

    def test_conversations_get_replies_and_spread_beliefs(self):
        async def go():
            sim = Simulation(PyEngine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, convert_chance=1.0))
            frames = [await sim.tick() for _ in range(300)]
            return sim, frames
        sim, frames = asyncio.run(go())
        said = [d for f in frames for d in f["dialogue"]]
        talks = [d for d in said if d["kind"] == "talk"]
        self.assertGreater(len(talks), 0)
        # every opening talk line is answered by its listener in the same frame
        for f in frames:
            for d in f["dialogue"]:
                if d["kind"] == "talk" and d["npc"] != d["target"]:
                    self.assertTrue(any(r["npc"] == d["target"] and r["target"] == d["npc"]
                                        for r in f["dialogue"]))
        self.assertTrue(any("🌀" in line for f in frames for line in f["log"]))
        self.assertTrue(all(n["belief"] and n["voice"] for n in frames[-1]["npcs"]))
        self.assertGreater(len(sim.news), 0)

    def test_hybrid_routing(self):
        class FakeLLM:
            """Records which NPC states reach the 'LLM'."""
            name = "fake-llm"

            def __init__(self):
                self.fallback = HeuristicBrain(3)
                self.seen = []

            async def choose_action(self, v, names):
                self.seen.append(dict(v.stats, adjacent=any(a["kind"] == "interact" for a in v.actions)))
                return self.fallback.pick_action(v)

            async def choose_interaction(self, ix):
                return await self.fallback.choose_interaction(ix)

            async def consent(self, ix, proposal, line):
                return await self.fallback.consent(ix, proposal, line)

        async def go():
            brain = FakeLLM()
            sim = Simulation(PyEngine(), brain, SimpleMemory(), SimConfig(seed=7, llm_interval=6))
            for _ in range(120):
                await sim.tick()
            return sim, brain
        sim, brain = asyncio.run(go())
        d = sim.decision_stats
        # Async: a few answers are still in flight at the end, or arrive after the
        # chosen action stopped being possible (the NPC then falls back to reflex).
        self.assertLessEqual(d["llm"], len(brain.seen))
        self.assertGreaterEqual(d["llm"], len(brain.seen) * 0.8)
        self.assertGreater(d["reflex"], 0)
        self.assertGreater(d["plan"], 0)
        self.assertLess(d["llm"], sum(d.values()) / 2)  # most decisions skip the LLM
        for s in brain.seen:  # survival reflexes never reach the LLM
            self.assertGreaterEqual(s["energy"], 20)
            self.assertGreaterEqual(s["hunger"], 30)

    def test_hung_llm_does_not_freeze_npcs(self):
        class HungLLM:
            """An LLM backend that never answers (cold load, stalled server...)."""
            name = "hung"

            def __init__(self):
                self.fallback = HeuristicBrain(4)

            async def choose_action(self, v, names):
                await asyncio.sleep(3600)

            async def choose_interaction(self, ix):
                await asyncio.sleep(3600)

            async def consent(self, ix, proposal, line):
                await asyncio.sleep(3600)

        async def go():
            sim = Simulation(PyEngine(), HungLLM(), SimpleMemory(),
                             SimConfig(seed=5, width=40, height=30, population=10, think_max_ticks=20))
            longest = {}
            for _ in range(600):
                f = await sim.tick()
                for nid, since in sim.pending_since.items():
                    longest[nid] = max(longest.get(nid, 0), f["tick"] - since)
            for t in sim.pending.values():
                t.cancel()
            return sim, longest
        sim, longest = asyncio.run(go())
        self.assertGreater(len(longest), 0)  # the LLM was asked...
        self.assertLessEqual(max(longest.values()), 21)  # ...but nobody waits past the deadline
        self.assertGreater(sim.decision_stats["reflex"], 0)
        starved = [n for n in sim.state["npcs"] if n["cause"] in ("starvation", "exhaustion")]
        self.assertEqual(starved, [])

    def test_npcs_act_asynchronously(self):
        async def go():
            sim = Simulation(PyEngine(), HeuristicBrain(2), SimpleMemory(),
                             SimConfig(seed=3, width=40, height=30, population=12))
            per_tick, movers = [], []
            for _ in range(150):
                before = {n["id"]: (n["x"], n["y"]) for n in sim.alive()}
                f = await sim.tick()
                per_tick.append(sum(1 for n in f["npcs"] if n["dur"] > 0))
                movers.append(sum(1 for n in f["npcs"] if before.get(n["id"], (n["x"], n["y"])) != (n["x"], n["y"])))
            return sim, per_tick, movers
        sim, per_tick, movers = asyncio.run(go())
        alive = len(sim.alive())
        # Only a fraction of NPCs start an action on any given tick...
        self.assertLess(sum(per_tick) / len(per_tick), alive * 0.6)
        # ...but every tick someone is usually doing something.
        self.assertGreater(sum(1 for p in per_tick if p > 0), len(per_tick) * 0.8)
        # Paces differ per NPC.
        self.assertGreater(len({m.pace for m in sim.meta.values()}), 3)

    def test_homes_pass_to_children(self):
        sim = Simulation(PyEngine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7))
        parent, child, other = (n["id"] for n in sim.state["npcs"][:3])
        sim.meta[child].parents = [parent, other]
        sim.homes[parent] = (4, 4)
        lines = sim._process_events([{"type": "death", "npc": parent, "cause": "old age", "killer": -1}], {}, 1)
        self.assertEqual(sim.homes.get(child), (4, 4))
        self.assertIn("inherits", lines[-1])
        frame = sim._frame({}, [])
        me = next(n for n in frame["npcs"] if n["id"] == child)
        self.assertEqual(me["home"], [4, 4])
        self.assertEqual(set(me["look"]), {"skin", "hair", "hairstyle", "clothes", "color", "hat"})

    def test_memory_grudges(self):
        m = SimpleMemory()
        m.log(Memory(1, 2, 5, "attack", "Bo attacked me.", -4))
        m.log(Memory(1, 2, 9, "trade", "Bo traded fairly.", 2))
        self.assertEqual(m.grudge(1, 2), -2)
        self.assertEqual([x.tick for x in m.recall(1, 2)], [5, 9])
        self.assertEqual(m.recall(2, 1), [])


if __name__ == "__main__":
    unittest.main()
