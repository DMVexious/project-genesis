"""Run from orchestrator/:  python -m unittest discover -s tests -v"""
import asyncio
import dataclasses
import json
import random
import re
import unittest
from collections import Counter

from genesis.brain import (MAX_WORDS, HeuristicBrain, Interaction, LlamaBrain, NpcView, action_grammar,
                           action_prompt, clamp_words, interaction_grammar, interaction_prompt, is_generic,
                           reply_prompt)
from genesis.engine import Engine, Rng, World
from genesis.engine import actions as A
from genesis.engine import defs as D
from genesis.memory import Memory, SimpleMemory
from genesis.sim import SimConfig, Simulation
from genesis.traits import BELIEFS, POOLS, VOICES, Genome, random_genome, splice


def scripted_choices(state, rng):
    """Deterministic pseudo-random choices that exercise every action kind."""
    out = {}
    for key, acts in sorted(state["actions"].items()):
        i = rng.randrange(len(acts))
        opt = rng.randrange(len(acts[i]["options"])) if acts[i]["kind"] == "interact" else 0
        out[int(key)] = (i, opt)
    return out


class EngineTests(unittest.TestCase):
    def test_rng_known_values(self):
        # SplitMix64 reference values for seed 0.
        r = Rng(0)
        self.assertEqual(r.next(), 0xE220A8397B1DCDAF)
        self.assertEqual(r.next(), 0x6E789E6AA1B965F4)

    def test_deterministic(self):
        def run():
            e, rng, s = Engine(), random.Random(3), None
            s = e.init(11, 20, 15, 8)
            for _ in range(200):
                s = e.step(scripted_choices(s, rng))
            return s
        self.assertEqual(run(), run())

    def test_actions_are_valid_and_invariants_hold(self):
        e, rng = Engine(), random.Random(5)
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
                    self.assertTrue(e.world.passable(n["x"], n["y"]), "NPC inside a solid tile")
        self.assertEqual(fails, 0)
        for k in ("chop", "forage", "eat", "sleep", "talk", "attack"):
            self.assertIn(k, kinds)

    def test_zero_energy_only_sleep(self):
        e = Engine()
        e.init(1, 10, 10, 1)
        npc = e.world.npcs[0]
        npc.energy = 0
        self.assertEqual(A.generate_actions(e.world, npc), [{"kind": "sleep"}])

    def test_old_age_is_fatal(self):
        e = Engine()
        e.init(9, 12, 12, 3)
        for n in e.world.npcs:
            n.age = D.ELDER_AGE + 400
        for _ in range(400):
            e.step({})
            for n in e.world.npcs:  # keep them fed and rested: only age can kill
                n.hunger = n.energy = n.health = 100
        self.assertEqual(e.world.npcs, [])  # the dead leave the active list...
        self.assertEqual([g["cause"] for g in e.world.graveyard], ["old age"] * 3)  # ...for the graveyard

    def test_npcs_choose_what_to_build(self):
        e = Engine()
        s = e.init(3, 14, 14, 1)
        w = e.world
        n = w.npcs[0]
        n.give("wood", 4)  # enough for a campfire only
        w.generate_all_actions()
        builds = [a["what"] for a in w.actions[n.id] if a["kind"] == "build"]
        self.assertEqual(builds, ["fire"])
        n.give("wood", 16)
        w.generate_all_actions()
        acts = w.actions[n.id]
        self.assertEqual([a["what"] for a in acts if a["kind"] == "build"], [b.key for b in D.BUILDINGS])
        idx = next(i for i, a in enumerate(acts) if a.get("what") == "farm")
        site = (acts[idx]["x"], acts[idx]["y"])
        s = e.step({n.id: (idx, 0)})
        ev = s["events"][0]
        self.assertEqual((ev["type"], ev["npc"], ev["what"], ev["x"], ev["y"]), ("build", n.id, "farm", *site))
        farm = w.obj_at(*site)
        self.assertEqual((farm.kind, farm.ripe, farm.owner), ("farm", False, n.id))
        self.assertEqual(w.tile(*site), D.OBJECTS["farm"].spent_glyph)
        self.assertEqual(n.have("wood"), 14)
        for _ in range(D.OBJECTS["farm"].regrow):  # the crop ripens
            w.world_time(0)
        self.assertTrue(farm.ripe)
        self.assertEqual(w.tile(*site), D.OBJECTS["farm"].glyph)
        self.assertNotIn(D.OBJECTS["hut"].glyph, "".join(s["tiles"]))
        # eating beside a campfire is a cooked meal
        w.place("fire", *site)
        spot = next((site[0] + dx, site[1] + dy) for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0))
                    if w.passable(site[0] + dx, site[1] + dy))
        w.teleport(n, *spot)
        n.give("food", 1)
        n.hunger = 10
        w.generate_all_actions()
        s = e.step({n.id: (next(i for i, a in enumerate(w.actions[n.id]) if a["kind"] == "eat"), 0)})
        self.assertTrue(s["events"][0]["cooked"])
        self.assertEqual(n.hunger, 10 + 30 + 15 - 1)  # (-1: a unit of world time passed)

    def test_one_flood_finds_every_destination(self):
        # goto offers come from a single search; each must be a real first step toward the place
        e = Engine()
        e.init(6, 30, 20, 6)
        w = e.world
        seen = 0
        for n in w.npcs:
            for a in w.actions[n.id]:
                if a["kind"] == "goto":
                    seen += 1
                    self.assertGreater(a["dist"], 0)
                    self.assertTrue(w.passable(n.x + a["dx"], n.y + a["dy"]))
                    self.assertGreater(w.count[D.OBJECTS[a["what"]].glyph], 0)
        self.assertGreater(seen, 0)

    def test_apply_changes_the_world_from_outside(self):
        e = Engine()
        e.init(5, 16, 12, 2)
        w = e.world
        a, b = w.npcs
        x, y = next((x, y) for y in range(w.h) for x in range(w.w) if w.free(x, y))
        s = e.apply([{"op": "place", "kind": "hut", "x": x, "y": y},
                     {"op": "give", "npc": a.id, "item": "food", "n": 5},
                     {"op": "stat", "npc": a.id, "stat": "health", "value": 500},
                     {"op": "spawn", "x": a.x, "y": a.y},
                     {"op": "kill", "npc": b.id, "cause": "lightning"},
                     {"op": "nonsense"}])
        self.assertEqual(w.obj_at(x, y).kind, "hut")
        self.assertFalse(w.passable(x, y))
        self.assertEqual((a.have("food"), a.health), (5, 100))
        self.assertEqual(len(w.npcs), 2)  # one arrived, one died
        self.assertEqual([ev["type"] for ev in s["events"]], ["arrive", "death"])
        self.assertIn(b.id, [n["id"] for n in s["npcs"] if not n["alive"]])
        s = e.step({})  # the events are reported once more, with the step they precede
        self.assertEqual([ev["type"] for ev in s["events"]][:2], ["arrive", "death"])
        self.assertEqual(e.step({})["events"], [])
        e.apply([{"op": "clear", "x": x, "y": y}])
        self.assertIsNone(w.obj_at(x, y))

    def test_registries_are_complete(self):
        # every declared action is offered, executed, scored and described; every tile has a glyph
        from genesis.brain import SCORERS
        self.assertEqual(set(D.ACTIONS), set(A.OFFER))
        self.assertEqual(set(D.ACTIONS), set(A.RUN))
        self.assertLessEqual(set(D.ACTIONS), set(SCORERS))
        self.assertEqual(set(D.INTERACTIONS), set(A.OPTION_RUN))
        for b in D.BUILDINGS:
            self.assertTrue(b.why and b.build_ticks > 0, b.key)
        defs = D.client_defs()
        json.dumps(defs)  # must be sendable to the front end
        self.assertEqual(set(defs["objects"]), set(D.OBJECTS))

    def test_carry_limits(self):
        w = World(1, 8, 8, 1)
        n = w.npcs[0]
        self.assertEqual(n.give("spear", 3), 1)  # one spear at most
        self.assertEqual(n.give("wood", -5), 0)
        self.assertNotIn("wood", n.inv)


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
        s = Engine().init(2, 16, 12, 3)
        n = s["npcs"][0]
        v = NpcView(n["id"], "Ash", Genome("Hostile", "Lazy", "Loner"), n, s["actions"][n["id"]])
        msgs = action_prompt(v, {1: "Ash", 2: "Bo", 3: "Cy"})
        self.assertIn("Your traits are [Hostile, Lazy, Loner]. Act accordingly.", msgs[0]["content"])
        payload = json.loads(msgs[1]["content"].split("\n")[0])
        self.assertEqual(len(payload["valid_actions"]), len(v.actions))

    def test_llm_falls_back_when_server_down(self):
        async def go():
            b = LlamaBrain("http://127.0.0.1:9", HeuristicBrain(0), timeout=0.5)
            s = Engine().init(2, 16, 12, 2)
            n = s["npcs"][0]
            v = NpcView(n["id"], "Ash", Genome("Neutral", "Lazy", "Loner"), n, s["actions"][n["id"]])
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
        s = Engine().init(2, 16, 12, 2)
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

    def test_interaction_prompt_explains_options(self):
        # bare "mate" was never picked by small models; each option now says what it means
        s = Engine().init(2, 16, 12, 2)
        a, b = s["npcs"][:2]
        me = NpcView(a["id"], "Ash", Genome("Neutral", "Lazy", "Charismatic"), a, [])
        them = NpcView(b["id"], "Bo", Genome("Pacifist", "Lazy", "Loner"), b, [])
        ix = Interaction(me, them, ["talk", "mate"], [], 3)
        payload = json.loads(interaction_prompt(ix)[1]["content"].split("\n")[0])
        self.assertEqual(set(payload["options"]), {"talk", "mate"})
        self.assertIn("family", payload["options"]["mate"])
        self.assertIn("social_urge", payload["you"])
        self.assertIn('"mate"', interaction_grammar(ix.options))  # the grammar still uses the bare keys


class BreedingTests(unittest.TestCase):
    def test_parents_rest_after_a_child(self):
        w = World(1, 16, 12, 2)
        a, b = w.npcs[:2]
        for n in (a, b):
            n.age, n.social, n.hunger, n.health = 200, 100, 100, 100
        w.teleport(b, a.x, a.y)
        self.assertTrue(A.mate_ok(w, a, b))
        self.assertEqual(A.interact_options(w, a, b), ["talk", "fight", "mate"])
        a.rest = D.MATE_COOLDOWN
        self.assertFalse(A.mate_ok(w, a, b))


class SimTests(unittest.TestCase):
    def test_sim_runs_and_logs_memories(self):
        async def go():
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7))
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
        # frames carry the engine events the front end animates (swings, chops, ...)
        fx = [e for f in frames for e in f["fx"]]
        self.assertTrue(any(e["type"] in ("chop", "forage", "eat") for e in fx))
        for e in fx:
            if e["type"] == "attack":
                self.assertIn("target", e)
                self.assertIn("damage", e)
        births = [m for m in sim.meta.values() if m.parents]
        for child in births:
            pa, pb = (sim.meta[p].genome for p in child.parents)
            for slot in POOLS:
                if slot not in child.mutated:
                    self.assertIn(getattr(child.genome, slot), (getattr(pa, slot), getattr(pb, slot)))

    def test_conversations_get_replies_and_spread_beliefs(self):
        async def go():
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, convert_chance=1.0))
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
            sim = Simulation(Engine(), brain, SimpleMemory(), SimConfig(seed=7, llm_interval=6))
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
            sim = Simulation(Engine(), HungLLM(), SimpleMemory(),
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
        starved = [g for g in sim.engine.world.graveyard if g["cause"] in ("starvation", "exhaustion")]
        self.assertEqual(starved, [])

    def test_npcs_act_asynchronously(self):
        async def go():
            sim = Simulation(Engine(), HeuristicBrain(2), SimpleMemory(),
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
        sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7))
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

    def test_same_seed_same_story(self):
        async def run():
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=11, width=40, height=30))
            return [await sim.tick() for _ in range(500)]
        a, b = asyncio.run(run()), asyncio.run(run())
        self.assertEqual(a, b)

    def test_player_commands_take_effect_next_tick(self):
        async def go():
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7))
            first = sim.alive()[0]
            sim.enqueue({"op": "spawn", "x": first["x"], "y": first["y"]})
            sim.enqueue({"op": "give", "npc": first["id"], "item": "food", "n": 9})
            frame = await sim.tick()
            return sim, first, frame
        sim, first, frame = asyncio.run(go())
        self.assertEqual(frame["stats"]["population"], 17)
        newcomer = max(n["id"] for n in frame["npcs"])
        self.assertTrue(sim.meta[newcomer].name and sim.meta[newcomer].look)
        self.assertTrue(any("appears" in line for line in frame["log"]))
        self.assertGreaterEqual(next(n for n in frame["npcs"] if n["id"] == first["id"])["inv"]["food"], 8)

    def test_llm_sees_a_shortlist(self):
        brain = HeuristicBrain(0)
        acts = ([{"kind": "sleep"}, {"kind": "eat"}] + [{"kind": "goto_npc", "target": i, "dist": 30 + i,
                 "dx": 0, "dy": 1} for i in range(20)] + [{"kind": "chop", "x": 0, "y": 0}, {"kind": "wander"}])
        stats = {"health": 90, "hunger": 90, "energy": 90, "social": 0, "age": 100, "inv": {}}
        v = NpcView(1, "Ash", Genome("Neutral", "Industrious", "Loner"), stats, acts)
        keep = brain.shortlist(v, 6)
        kinds = [acts[i]["kind"] for i in keep]
        self.assertEqual(len(keep), 6)
        self.assertEqual(keep, sorted(keep))
        self.assertEqual(kinds[:2], ["sleep", "eat"])  # survival options always stay
        self.assertIn("chop", kinds)                   # and so does what the NPC most wants
        self.assertEqual(brain.shortlist(dataclasses.replace(v, actions=acts[:4]), 6), [0, 1, 2, 3])

    def test_terminal_draws_every_tile(self):
        # (the old glyph table had no farms or campfires and crashed the live view)
        from genesis.terminal import render
        sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, width=20, height=12))
        w = sim.engine.world
        spots = [(x, y) for y in range(w.h) for x in range(w.w) if w.free(x, y)]
        for i, o in enumerate(D.OBJECTS.values()):
            w.place(o.key, *spots[2 * i], ripe=True)
            if o.spent_glyph:
                w.place(o.key, *spots[2 * i + 1], ripe=False)
        sim.state = sim.engine.state()
        text = render(sim._frame({}, []), [])
        for o in D.OBJECTS.values():
            self.assertIn(o.term[0], text, o.key)
            if o.spent_glyph:
                self.assertIn(o.spent_term[0], text, o.key)

    def test_memory_grudges(self):
        m = SimpleMemory()
        m.log(Memory(1, 2, 5, "attack", "Bo attacked me.", -4))
        m.log(Memory(1, 2, 9, "trade", "Bo traded fairly.", 2))
        self.assertEqual(m.grudge(1, 2), -2)
        self.assertEqual([x.tick for x in m.recall(1, 2)], [5, 9])
        self.assertEqual(m.recall(2, 1), [])


if __name__ == "__main__":
    unittest.main()
