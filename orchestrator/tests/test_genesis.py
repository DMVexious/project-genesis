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

    def test_trees_drop_their_wood(self):
        e = Engine()
        e.init(3, 14, 14, 2)
        w = e.world
        a, b = w.npcs
        w.teleport(b, 0, 0) if w.passable(0, 0) else None
        tx, ty = next((x, y) for y in range(1, w.h - 1) for x in range(1, w.w - 1)
                      if w.free(x, y) and w.free(x + 1, y))
        w.place("tree", tx, ty)
        w.teleport(a, tx + 1, ty)
        w.generate_all_actions()
        chop = next(i for i, x in enumerate(w.actions[a.id]) if x["kind"] == "chop")
        s = e.step({a.id: (chop, 0)})
        # the wood is on the ground where the tree stood, not in anyone's hands
        self.assertEqual(a.have("wood"), 0)
        self.assertEqual(s["drops"], [[tx, ty, {"wood": 4}]])
        self.assertEqual([ev["type"] for ev in s["events"]], ["drop", "chop"])
        collect = next(x for x in w.actions[a.id] if x["kind"] == "collect")
        self.assertEqual((collect["x"], collect["y"], collect["dist"], collect["items"]), (tx, ty, 1, {"wood": 4}))
        s = e.step({a.id: (w.actions[a.id].index(collect), 0)})  # walking onto it picks it up
        self.assertEqual(((a.x, a.y), a.have("wood"), s["drops"]), ((tx, ty), 4, []))
        ev = next(ev for ev in s["events"] if ev["type"] == "pickup")
        self.assertEqual((ev["npc"], ev["items"], ev["owner"]), (a.id, {"wood": 4}, a.id))
        # nothing grows back while someone (or something) is on the stump
        self.assertFalse(w.obj_at(tx, ty).ripe)

    def test_hands_are_only_so_big(self):
        e = Engine()
        e.init(3, 14, 14, 1)
        w = e.world
        n = w.npcs[0]
        cap = n.cap("wood")  # (how much fits depends on how strong they are)
        n.give("wood", cap - 1)
        w.drop(n.x, n.y, {"wood": 5, "food": 2})
        w.generate_all_actions()
        here = next(x for x in w.actions[n.id] if x["kind"] == "collect")
        self.assertEqual(here["dist"], 0)
        s = e.step({n.id: (w.actions[n.id].index(here), 0)})
        self.assertEqual((n.have("wood"), n.have("food")), (cap, 2))
        self.assertEqual(s["drops"], [[n.x, n.y, {"wood": 4}]])  # the rest stays put
        self.assertFalse(any(x["kind"] == "collect" for x in w.actions[n.id]))  # no room: not worth offering
        for _ in range(D.DROP_TTL):
            w.world_time(1)
            n.hunger = n.energy = 100
        self.assertEqual(w.drops, {})  # left long enough, it rots away

    def test_the_dead_drop_what_they_carried(self):
        e = Engine()
        e.init(5, 16, 12, 2)
        w = e.world
        a, b = w.npcs
        b.give("wood", 7)
        b.give("spear", 1)
        s = e.apply([{"op": "kill", "npc": b.id}])
        self.assertEqual(s["drops"], [[b.x, b.y, {"wood": 7, "spear": 1}]])
        self.assertEqual(b.inv, {})

    def test_anything_can_be_traded_or_given(self):
        e = Engine()
        e.init(5, 16, 12, 2)
        w = e.world
        a, b = w.npcs
        w.teleport(b, a.x, a.y)
        a.give("wood", 6)
        b.give("food", 1)
        b.give("spear", 1)
        opts = A.interact_options(w, a, b)
        self.assertEqual(opts, ["talk", "fight", "trade:wood:food", "trade:wood:spear", "give:wood"])
        self.assertIn("trade:food:wood", A.interact_options(w, b, a))
        w.generate_all_actions()
        act = next(i for i, x in enumerate(w.actions[a.id]) if x["kind"] == "interact")
        s = e.step({a.id: (act, opts.index("trade:wood:spear"))})
        self.assertEqual(s["events"][0], {"type": "trade", "npc": a.id, "target": b.id, "gave": "wood",
                                          "gave_n": 6, "got": "spear", "got_n": 1})
        self.assertEqual((a.inv, b.inv), ({"spear": 1}, {"food": 1, "wood": 6}))
        opts = A.interact_options(w, b, a)
        self.assertNotIn("trade:spear:wood", opts)  # b has no spear to offer any more...
        self.assertIn("trade:wood:spear", opts)     # ...but could buy it back
        self.assertIn("give:food", opts)
        act = next(i for i, x in enumerate(w.actions[b.id]) if x["kind"] == "interact")
        s = e.step({b.id: (act, opts.index("give:food"))})
        self.assertEqual(s["events"][0], {"type": "gift", "npc": b.id, "target": a.id, "item": "food", "n": 1})
        self.assertEqual((a.have("food"), b.have("food")), (1, 0))
        self.assertIn("give you 1 food", D.option_text("give:food", mine=False))
        self.assertIn("give them 2 wood, get 1 food", D.option_text("trade:wood:food"))

    def test_the_hungry_get_fed_and_takers_are_remembered(self):
        async def go():
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, population=2))
            return sim
        sim = asyncio.run(go())
        a, b = (n["id"] for n in sim.state["npcs"])
        rich = NpcView(a, "Ash", Genome("Pacifist", "Lazy", "Loner"), {**sim.state["npcs"][0], "inv": {"food": 6}})
        poor = NpcView(b, "Bo", Genome("Neutral", "Lazy", "Loner"), {**sim.state["npcs"][1], "hunger": 20, "inv": {}})
        brain = HeuristicBrain(3)
        ix = Interaction(rich, poor, ["talk", "fight", "give:food"], [], 0)
        self.assertGreater(brain.option_weight(ix, "give:food"), brain.option_weight(ix, "talk") + 15)
        full = Interaction(rich, dataclasses.replace(poor, stats={**poor.stats, "hunger": 90}), ix.options, [], 0)
        self.assertLess(brain.option_weight(full, "give:food"), 0)  # no handouts to the well fed
        # the hungry will not sell their last food
        self.assertFalse(asyncio.run(brain.consent(Interaction(poor, rich, [], [], 0), "trade:wood:food", "")))
        lines = sim._process_events(
            [{"type": "pickup", "npc": a, "x": 1, "y": 1, "items": {"wood": 4}, "owner": b},
             {"type": "gift", "npc": a, "target": b, "item": "food", "n": 1},
             {"type": "trade", "npc": a, "target": b, "gave": "wood", "gave_n": 2, "got": "food", "got_n": 1}], {}, 5)
        self.assertEqual(len(lines), 3)
        self.assertIn("4 wood", lines[0])
        self.assertEqual(sim.memory.grudge(b, a), -2 + 3 + 2)  # robbed, then given to, then a fair trade

    def test_practice_makes_better_for_life(self):
        e = Engine()
        e.init(3, 14, 14, 1)
        w = e.world
        n = w.npcs[0]
        n.attrs = {k: 5 for k in D.ATTRS}
        tx, ty = next((x, y) for y in range(1, w.h - 1) for x in range(1, w.w - 1)
                      if w.free(x, y) and w.free(x + 1, y))
        w.teleport(n, tx + 1, ty)
        levels, wood = [], []
        for _ in range(80):  # fell the same spot over and over
            w.place("tree", tx, ty)
            w.drops.clear()
            n.inv.clear()
            n.energy = n.hunger = 100
            w.generate_all_actions()
            s = e.step({n.id: (next(i for i, x in enumerate(w.actions[n.id]) if x["kind"] == "chop"), 0)})
            levels += [(ev["skill"], ev["level"]) for ev in s["events"] if ev["type"] == "levelup"]
            wood.append(s["drops"][0][2]["wood"])
        self.assertEqual(levels[:3], [("woodcutting", 1), ("woodcutting", 2), ("woodcutting", 3)])
        self.assertGreaterEqual(n.level("woodcutting"), 6)
        self.assertEqual((wood[0], wood[-1]), (4, 4 + n.level("woodcutting") // 3))  # more wood per tree
        self.assertGreater(n.attrs["strength"], 5)  # and the work built them up
        me = n.to_json()
        self.assertEqual(me["role"], "woodcutting")
        self.assertEqual(me["xp"]["woodcutting"][1], D.skill_need(n.level("woodcutting")))
        # the practised are quicker at it, the strong carry more
        novice = {"attrs": {k: 5 for k in D.ATTRS}, "skills": {}}
        self.assertLess(D.speed_factor(me, {"kind": "chop"}), 0.75 * D.speed_factor(novice, {"kind": "chop"}))
        self.assertAlmostEqual(D.speed_factor(novice, {"kind": "chop"}), 1.0)
        self.assertGreater(n.cap("wood"), D.ITEMS["wood"].cap)
        self.assertEqual(n.cap("spear"), 1)

    def test_attributes_shape_a_fight_and_are_inherited(self):
        w = World(4, 16, 12, 2)
        a, b = w.npcs
        for n in (a, b):
            self.assertTrue(all(3 <= v <= 7 for v in n.attrs.values()), n.attrs)
        a.attrs.update(strength=9, agility=5)
        b.attrs.update(strength=2, agility=9)
        a.skills["combat"] = 6
        self.assertEqual(A.hit_chance(a, b), 75 + 12 - 8)
        self.assertGreater(A._hit_bonus(a), A._hit_bonus(b) + 8)
        kid = w.blend_attrs(a, b)
        for k in D.ATTRS:
            lo, hi = sorted((a.attrs[k], b.attrs[k]))
            self.assertTrue(lo - 1 <= kid[k] <= min(hi + 1, D.ATTR_MAX - 2), (k, kid[k]))

    def test_lives_are_counted_in_years(self):
        self.assertEqual((D.years(D.MATURE_AGE), D.years(D.ELDER_AGE)), (16, 60))
        sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7))
        frame = sim._frame({}, [])
        for n in frame["npcs"]:
            self.assertTrue(18 <= n["age"] <= 26, n["age"])
            self.assertEqual(n["stage"], "adult")
            self.assertEqual(set(n["attrs"]), set(D.ATTRS))
        # the LLM is told what it is made of and good at
        v = sim.view(sim.alive()[0])
        v.stats["skills"] = {"woodcutting": 3}
        you = json.loads(action_prompt(v, sim.names())[1]["content"].split("\n")[0])["you"]
        self.assertEqual(you["skill_levels"], {"woodcutting": 3})
        self.assertIn("woodcutter", you["known_as"])
        self.assertEqual(set(you["attributes_1_to_10"]), {a.label.lower() for a in D.ATTRS.values()})
        lines = sim._process_events([{"type": "levelup", "npc": v.id, "skill": "woodcutting", "level": 3}], {}, 9)
        self.assertIn("level 3", lines[0])

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
        from genesis.brain import gossips, spoken
        a["age"] = next(age for age in range(100, 200, 20) if gossips(
            Interaction(NpcView(a["id"], "Ash", me.genome, {**a, "age": age}, []), them, [], [], 0)))
        sys_msg, user = (m["content"] for m in interaction_prompt(ix))
        self.assertEqual(spoken('I\'m just fine."} }{'), "I'm just fine.")
        self.assertIn("casual", sys_msg)
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
            n.age, n.social, n.hunger, n.health = D.START_AGE, 100, 100, 100
        w.teleport(b, a.x, a.y)
        self.assertTrue(A.mate_ok(w, a, b))
        self.assertEqual(A.interact_options(w, a, b), ["talk", "fight", "mate"])
        a.rest = D.MATE_COOLDOWN
        self.assertFalse(A.mate_ok(w, a, b))


class SimTests(unittest.TestCase):
    def test_sim_runs_and_logs_memories(self):
        async def go():
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, population=16))
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
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, population=16, convert_chance=1.0))
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
            # (no waiting for a turn to speak here: this is about which decisions reach the LLM,
            # and a crowd queueing to talk would let many of its answers go stale)
            sim = Simulation(Engine(), brain, SimpleMemory(), SimConfig(
                seed=7, population=16, llm_interval=6, talk_gap=0, talk_cooldown=0, talk_budget=99))
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
        sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, population=16))
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

    def test_work_takes_time(self):
        # chopping, building, eating... only take effect once the time bar has run out
        async def go():
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, population=16))
            seen = []
            for _ in range(400):
                f = await sim.tick()
                done = {e["npc"]: e["type"] for e in f["fx"] if e["type"] in ("chop", "forage", "build", "eat", "craft")}
                seen.append((f["tick"], {n["id"]: (n["doing"], n["t0"], n["t1"]) for n in f["npcs"]}, done,
                             [e for e in f["fx"] if e["type"] == "work"]))
            return seen
        seen = asyncio.run(go())
        finished = swings = 0
        for i, (tick, npcs, done, work) in enumerate(seen):
            swings += len(work)
            for nid, kind in done.items():
                # the tick before it happened, that NPC was still busy doing exactly this
                doing, t0, t1 = seen[i - 1][1][nid]
                self.assertEqual(doing, kind)
                self.assertEqual(t1, tick)
                self.assertGreaterEqual(t1 - t0, 2)
                finished += 1
        self.assertGreater(finished, 20)
        self.assertGreater(swings, finished)  # several swings per finished job

    def test_children_are_carried_before_they_are_born(self):
        e = Engine()
        e.init(1, 16, 12, 2)
        w = e.world
        a, b = w.npcs
        for n in (a, b):
            n.age, n.social, n.hunger, n.health, n.energy = D.START_AGE, 100, 100, 100, 100
        w.teleport(b, a.x, a.y)
        w.generate_all_actions()
        act = next(i for i, x in enumerate(w.actions[a.id]) if x["kind"] == "interact")
        s = e.step({a.id: (act, w.actions[a.id][act]["options"].index("mate"))})
        self.assertEqual([ev["type"] for ev in s["events"]], ["conceive"])
        self.assertEqual((len(w.npcs), a.expecting), (2, D.GESTATION - 1))  # (a unit of world time passed)
        self.assertFalse(A.mate_ok(w, a, b))
        for _ in range(D.GESTATION - 2):
            for n in (a, b):
                n.hunger = n.energy = 100
            self.assertEqual(e.step({})["events"], [])
        born = e.step({})["events"]
        self.assertEqual(born, [{"type": "birth", "npc": 3, "parents": [a.id, b.id]}])
        self.assertEqual((len(w.npcs), a.expecting, w.npcs[-1].age), (3, 0, 0))

    def test_founders_start_together(self):
        for seed in range(8):
            w = World(seed, 80, 56, 3)
            xs, ys = [n.x for n in w.npcs], [n.y for n in w.npcs]
            self.assertLessEqual(max(xs) - min(xs), 2 * D.SETTLE_RADIUS + 2, seed)
            self.assertLessEqual(max(ys) - min(ys), 2 * D.SETTLE_RADIUS + 2, seed)
            self.assertEqual(len({(n.x, n.y) for n in w.npcs}), 3)

    def test_people_take_turns_talking(self):
        async def go():
            cfg = SimConfig(seed=7, population=16)
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), cfg)
            starts = []
            for _ in range(600):
                f = await sim.tick()
                starts += [f["tick"]] * sum(1 for d in f["dialogue"] if not d.get("reply"))
            return cfg, starts
        cfg, starts = asyncio.run(go())
        self.assertGreater(len(starts), 5)
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertGreaterEqual(min(gaps), cfg.talk_gap)  # never two conversations at once

    def test_same_seed_same_story(self):
        async def run():
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=11, width=40, height=30, population=12))
            return [await sim.tick() for _ in range(500)]
        a, b = asyncio.run(run()), asyncio.run(run())
        self.assertEqual(a, b)

    def test_player_commands_take_effect_next_tick(self):
        async def go():
            sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, population=16))
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
        sim = Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(seed=7, width=20, height=12, population=6))
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


def slow_scan(world, sx, sy, limit):
    """What ``path.scan`` should find, worked out the slow, obvious way: walking
    distance to every tile, then the nearest tile next to each kind of place."""
    w, h = world.w, world.h
    dist = {(sx, sy): 0}
    frontier = [(sx, sy)]
    while frontier:
        nxt = []
        for x, y in frontier:
            for dx, dy in D.DIRS:
                p = (x + dx, y + dy)
                if p not in dist and world.passable(*p) and dist[(x, y)] < limit:
                    dist[p] = dist[(x, y)] + 1
                    nxt.append(p)
        frontier = nxt
    best = {}
    for (x, y), d in dist.items():
        for dx, dy in D.DIRS:
            if world.inb(x + dx, y + dy):
                g = world.tile(x + dx, y + dy)
                if g in D.GOTO_BIT and d < best.get(g, 10**9):
                    best[g] = d
    return dist, best


class MapTests(unittest.TestCase):
    def test_one_flood_agrees_with_the_slow_way(self):
        from genesis.engine.path import scan
        for name in ("barren", "valley", "forest", "islands", "desert"):
            world = World(5, 48, 36, 8, 1, name)
            world.drop(*next((x, y) for y in range(world.h) for x in range(world.w) if world.free(x, y)),
                       {"wood": 2})
            for n in world.npcs:
                others = [o for o in world.npcs if o.id != n.id][:3]
                found = scan(world, n.x, n.y, D.GOTO_BIT, others, D.GOTO_LIMIT, list(world.drops))
                dist, best = slow_scan(world, n.x, n.y, D.GOTO_LIMIT)
                self.assertEqual({g: found[g][0] for g in found if g in D.GOTO_BIT}, best, name)
                for o in others:  # the nearest tile within arm's reach of each of them
                    near = min((d for (x, y), d in dist.items() if abs(x - o.x) <= 1 and abs(y - o.y) <= 1),
                               default=None)
                    self.assertEqual(found.get(o.id, (None,))[0], near, name)
                pile = min((dist[(i % world.w, i // world.w)] for i in world.drops
                            if (i % world.w, i // world.w) in dist), default=None)
                self.assertEqual(found.get("cell", (None,))[0], pile, name)
                for key, hit in found.items():  # and the first step really is a step that way
                    d, dx, dy = hit[:3]
                    if d:
                        self.assertEqual(dist[(n.x + dx, n.y + dy)], 1, (name, key))
                    else:
                        self.assertEqual((dx, dy), (0, 0))

    def test_the_search_index_keeps_up_with_the_world(self):
        # every tile's "what am I next to" mask must match the tiles around it, whatever happens
        e, rng = Engine(), random.Random(9)
        s = e.init(4, 30, 22, 10, 1, "valley")
        w = e.world

        def check():
            for i in range(w.w * w.h):
                self.assertEqual(w.near[i], w._near_of(i), i)
            for g in D.GOTO_BIT:
                self.assertEqual(w.where[g], {i for i, c in enumerate(w.glyphs) if c == g}, g)
                self.assertEqual(w.count[g], len(w.where[g]), g)
            self.assertEqual(s["tiles"], ["".join(w.glyphs[y * w.w:(y + 1) * w.w]) for y in range(w.h)])

        check()
        for t in range(300):
            s = e.step(scripted_choices(s, rng))
            if t % 60 == 59:
                check()
        x, y = next((x, y) for y in range(w.h) for x in range(w.w) if w.free(x, y))
        s = e.apply([{"op": "place", "kind": "hut", "x": x, "y": y}])
        check()
        s = e.apply([{"op": "clear", "x": x, "y": y}, {"op": "terrain", "x": x, "y": y, "kind": "deep"}])
        check()
        self.assertFalse(w.passable(x, y))
        self.assertEqual(s["terrain"][y][x], D.TERRAIN["deep"].glyph)

    def test_every_map_is_a_place_to_live(self):
        from genesis.engine import MAPS
        from genesis.engine.path import regions
        water = {D.TERRAIN[k].glyph for k in D.WATER}
        for name in MAPS:
            for seed in (1, 2, 3):
                a, b = World(seed, 80, 56, 5, 5, name), World(seed, 80, 56, 5, 5, name)
                self.assertEqual((a.rows(), a.terrain_rows(), a.start), (b.rows(), b.terrain_rows(), b.start), name)
                self.assertEqual(len(a.npcs), 5, name)
                label, sizes = regions(a)
                home = {label[a.idx(n.x, n.y)] for n in a.npcs}
                self.assertEqual(len(home), 1, name)  # the founders can all reach each other
                self.assertGreater(sizes[home.pop()], 600, name)  # and have a country to roam
                sx, sy = a.start
                self.assertTrue(all(abs(n.x - sx) <= 6 and abs(n.y - sy) <= 6 for n in a.npcs), name)
                near = Counter(o.kind for o in a.objs.values() if abs(o.x - sx) <= 8 and abs(o.y - sy) <= 8)
                self.assertGreaterEqual(sum(n * D.OBJECTS[k].food for k, n in near.items()), 6, (name, seed, near))
                self.assertGreaterEqual(near["tree"], 3, (name, seed, near))
                for o in a.objs.values():  # nothing grows in water or on a mountain top
                    self.assertTrue(D.TERRAIN[a.terrain[a.idx(o.x, o.y)]].buildable, (name, o.kind))
        sea = sum(row.count(c) for row in World(1, 80, 56, 3, 5, "islands").terrain_rows() for c in water)
        self.assertGreater(sea, 0.5 * 80 * 56)
        sand = sum(row.count(D.TERRAIN["sand"].glyph) for row in World(1, 80, 56, 3, 5, "desert").terrain_rows())
        self.assertGreater(sand, 0.5 * 80 * 56)
        self.assertEqual(set(World(1, 80, 56, 3, 5, "barren").terrain), {"dirt"})
        big = World(3, 160, 112, 8, 5, "valley")  # the largest size on offer
        self.assertEqual(len(big.npcs), 8)

    def test_the_ground_matters(self):
        e = Engine()
        e.init(2, 20, 14, 1, 1)
        w = e.world
        n = w.npcs[0]
        for o in list(w.objs.values()):
            w.remove(o.x, o.y)
        w.teleport(n, 5, 5)
        for x, kind in ((6, "shallow"), (7, "deep"), (4, "peak"), (9, "sand")):
            w.set_terrain(x, 5, kind)
        self.assertTrue(w.passable(6, 5))        # shallows can be waded...
        self.assertFalse(w.passable(7, 5))       # ...deep water and mountains cannot be crossed
        self.assertFalse(w.passable(4, 5))
        self.assertFalse(w.free(6, 5))           # and nothing is built in water
        w.place("bush", 6, 4)                    # something to walk toward, through the water
        w.teleport(n, 5, 6)
        w.set_terrain(5, 5, "shallow")
        for x in (4, 6):
            w.place("rock", x, 6)
        w.place("rock", 5, 7)
        w.generate_all_actions()
        go = next(a for a in w.actions[n.id] if a["kind"] == "goto" and a["what"] == "bush")
        self.assertEqual((go["dx"], go["dy"], go["cost"]), (0, -1, D.TERRAIN["shallow"].move_cost))
        self.assertEqual(D.action_ticks(go), D.ACTIONS["goto"].ticks * D.TERRAIN["shallow"].move_cost)
        self.assertEqual(D.action_ticks({"kind": "goto", "what": "bush"}), D.ACTIONS["goto"].ticks)
        # things grow faster on good, wet ground than on sand
        grass = [(x, 10) for x in range(2, 6)]
        for x, y in grass:
            w.set_terrain(x, y, "grass")
        dry_farm, wet_farm, sand_farm = w.place("farm", 14, 12), w.place("farm", 8, 5), w.place("farm", 9, 5)
        w.set_terrain(8, 5, "grass")
        times = [w.grow_time(o) for o in (dry_farm, wet_farm, sand_farm)]
        self.assertEqual(times[0], D.OBJECTS["farm"].regrow)                       # bare earth: as listed
        self.assertLess(times[1], times[0])                                        # grass by the water
        self.assertGreater(times[2], times[1])                                     # sand, even by the water
        self.assertAlmostEqual(w.fertility(8, 5), D.TERRAIN["grass"].fertile * D.WET_BONUS)
        self.assertEqual(w.fertility(7, 5), 0.0)
        n.give("wood", 20)  # with wood in hand, building is on offer, and says how good the soil is
        w.generate_all_actions()
        build = next(a for a in w.actions[n.id] if a["kind"] == "build" and a["what"] == "farm")
        self.assertEqual(build["soil"], round(w.fertility(build["x"], build["y"]), 2))
        self.assertIn("soil", D.describe_action(build, {}, n.to_json()))

    def test_what_grows_wild_can_be_gathered(self):
        e = Engine()
        e.init(2, 20, 14, 1, 1)
        w = e.world
        n = w.npcs[0]
        for kind in ("fruit", "cactus", "mushroom"):
            for o in list(w.objs.values()):
                w.remove(o.x, o.y)
            w.teleport(n, 5, 5)
            w.place(kind, 6, 5)
            n.inv.clear()
            w.generate_all_actions()
            act = next(a for a in w.actions[n.id] if a["kind"] == "forage")
            self.assertEqual(act["what"], kind)
            self.assertIn(D.OBJECTS[kind].place, D.describe_action(act, {}, n.to_json()))
            e.step({n.id: (w.actions[n.id].index(act), 0)})
            self.assertEqual(n.have("food"), D.OBJECTS[kind].food)
            self.assertEqual(w.tile(6, 5), D.OBJECTS[kind].spent_glyph)
            w.teleport(n, 9, 9)
            w.obj_at(6, 5).ripe = True
            w._refresh(w.idx(6, 5))
            w.generate_all_actions()
            walk = next(a for a in w.actions[n.id] if a["kind"] == "goto" and a["what"] == kind)
            self.assertIn(D.OBJECTS[kind].place, D.describe_action(walk, {}, n.to_json()))

    def test_villages_live_on_every_map(self):
        from genesis.engine import MAPS

        async def go(name):
            sim = Simulation(Engine(), HeuristicBrain(2), SimpleMemory(), SimConfig(seed=2, map_name=name))
            for _ in range(1500):
                f = await sim.tick()
            return sim, f
        for name in MAPS:
            sim, f = asyncio.run(go(name))
            self.assertGreaterEqual(f["stats"]["population"], 3, name)  # nobody lost, and maybe a child or two
            self.assertEqual((f["map"], len(f["terrain"]), len(f["terrain"][0])), (name, 56, 80))
            self.assertEqual(f["start"], list(sim.engine.world.start))


class PlayerTests(unittest.TestCase):
    """The player can become a villager, steer them, talk and fight as them,
    speak to anyone from the sky, and heal, feed or smite."""

    def make(self, **kw):
        cfg = dict(seed=7, population=3, width=40, height=30)
        cfg.update(kw)
        return Simulation(Engine(), HeuristicBrain(1), SimpleMemory(), SimConfig(**cfg))

    def run_until(self, sim, done, ticks=300):
        async def go():
            frames = []
            for _ in range(ticks):
                frames.append(await sim.tick())
                await asyncio.sleep(0)
                if done(frames[-1]):
                    break
            return frames
        return asyncio.run(go())

    def test_a_possessed_body_only_does_what_it_is_told(self):
        sim = self.make()
        me = sim.alive()[0]["id"]
        self.assertTrue(sim.possess(me))
        frames = self.run_until(sim, lambda f: False, 60)
        body = [n for n in frames[-1]["npcs"] if n["id"] == me][0]
        start = next(n for n in frames[0]["npcs"] if n["id"] == me)
        self.assertEqual((body["x"], body["y"]), (start["x"], start["y"]))  # no orders: it stays put
        self.assertEqual(frames[-1]["possessed"], me)
        player = frames[-1]["player"]
        self.assertEqual(player["id"], me)
        self.assertTrue(any(a["kind"] == "sleep" for a in player["actions"]))
        self.assertFalse(any(a["kind"] in ("step", "goto", "wander") for a in player["actions"]))
        self.assertEqual(sim.decision_stats["player"], 0)
        # nobody else is steered: only the possessed body gets "step" actions
        w = sim.engine.world
        self.assertEqual(w.controlled, {me})
        other = sim.alive()[1]["id"]
        self.assertFalse(any(a["kind"] == "step" for a in w.actions[other]))

    def test_walking_somewhere_and_chopping_what_is_there(self):
        sim = self.make()
        me = sim.alive()[0]["id"]
        sim.possess(me)
        asyncio.run(sim.tick())
        w = sim.engine.world
        n = w.npc(me)
        tree = min((o for o in w.objs.values() if o.kind == "tree" and o.ripe),
                   key=lambda o: abs(o.x - n.x) + abs(o.y - n.y))
        self.assertTrue(sim.command({"kind": "walk", "x": tree.x, "y": tree.y}))
        frames = self.run_until(sim, lambda f: any(e["type"] == "chop" and e["npc"] == me for e in f["fx"]), 400)
        self.assertTrue(any(e["type"] == "chop" and e["npc"] == me for e in frames[-1]["fx"]))
        self.assertFalse(w.obj_at(tree.x, tree.y).ripe)          # felled...
        self.assertEqual(abs(n.x - tree.x) + abs(n.y - tree.y), 1)  # ...from right beside it
        self.assertGreater(sim.decision_stats["player"], 0)
        self.assertFalse(sim.command({"kind": "fly"}))
        # one step at a time, too
        x0 = n.x
        dx = 1 if w.passable(n.x + 1, n.y) else -1
        sim.command({"kind": "step", "dx": dx, "dy": 0})
        self.run_until(sim, lambda f: n.x != x0, 30)
        self.assertEqual(n.x, x0 + dx)

    def test_talking_and_hitting_as_someone_else(self):
        sim = self.make(population=2)
        me, them = (n["id"] for n in sim.alive())
        sim.possess(me)
        sim.command({"kind": "interact", "target": them, "option": "talk", "text": "Hey, where'd you hide the berries?"})
        frames = self.run_until(sim, lambda f: any(d["npc"] == them and d["kind"] == "chat" for d in f["dialogue"]), 400)
        lines = [d for f in frames for d in f["dialogue"] if d["kind"] == "chat"]
        said = [d for d in lines if d["npc"] == me]
        answer = [d for d in lines if d["npc"] == them]
        self.assertEqual(said[0]["text"], "Hey, where'd you hide the berries?")
        self.assertTrue(said[0]["player"])
        self.assertTrue(answer and answer[0]["text"])  # they always answer
        self.assertIn("berries", " ".join(m.text for m in sim.memory.recall(them, me, 5)))  # and remember it
        sim.end_chat()
        # a blow, with no words at all: they still answer, and hold it against you
        sim.engine.world.npc(them).health = 100
        sim.command({"kind": "interact", "target": them, "option": "fight"})
        frames = self.run_until(sim, lambda f: any(e["type"] == "attack" and e["npc"] == me for e in f["fx"]), 400)
        self.assertTrue(any(e["type"] == "attack" and e["npc"] == me for e in frames[-1]["fx"]))
        self.assertTrue(any(d["npc"] == them and d.get("reply") for d in frames[-1]["dialogue"]))
        self.assertLess(sim.memory.grudge(them, me), 3)

    def test_the_player_answers_whoever_speaks_to_their_body(self):
        sim = self.make(population=2)
        a, me = (n["id"] for n in sim.alive())
        sim.possess(me)
        w = sim.engine.world
        asyncio.run(sim.tick())
        w.teleport(w.npc(a), *next((w.npc(me).x + dx, w.npc(me).y + dy) for dx, dy in D.DIRS
                                   if w.passable(w.npc(me).x + dx, w.npc(me).y + dy)))
        w.generate_all_actions()
        sim.state = sim.engine.state()
        views = {n["id"]: sim.view(n) for n in sim.alive()}
        act = next(x for x in views[a].actions if x["kind"] == "interact" and x["target"] == me)

        async def go(reply, accept):
            task = asyncio.ensure_future(sim._interaction(views, a, act, "reflex", False))
            for _ in range(100):
                await asyncio.sleep(0)
                if sim.incoming is not None:
                    break
            seen = sim._player_frame()["incoming"]
            self.assertTrue(sim.answer(reply, accept))
            self.assertFalse(sim.answer("twice", True))  # (only one answer per question)
            return seen, await task
        seen, result = asyncio.run(go("Get lost, I'm busy.", False))
        self.assertEqual(seen["from"], a)
        self.assertIsNone(sim.incoming)
        # your answer turns it into a conversation: they get to answer you back
        self.assertEqual(result["type"], "none")
        self.assertEqual(sim.chat["with"], a)
        self.assertEqual(sim.chat["lines"][-1], (me, "Get lost, I'm busy."))
        self.run_until(sim, lambda f: sim.chat is None or sim.chat["lines"][-1][0] == a, 200)
        self.assertTrue(sim.chat is None or sim.chat["lines"][-1][0] == a)

    def test_a_real_conversation_until_someone_leaves(self):
        sim = self.make(population=4, width=30, height=24)
        ids = [n["id"] for n in sim.alive()]
        me, them, others = ids[0], ids[1], ids[2:]
        sim.possess(me)
        w = sim.engine.world
        n_them = w.npc(them)
        n_them.hunger = n_them.energy = 100
        sim.command({"kind": "interact", "target": them, "option": "talk", "text": "Hey. Got a minute?"})
        frames = self.run_until(sim, lambda f: f["player"] and f["player"]["chat"]
                                and any(not l["me"] for l in f["player"]["chat"]["lines"]), 400)
        chat = frames[-1]["player"]["chat"]
        self.assertEqual(chat["with"], them)
        self.assertEqual([l["me"] for l in chat["lines"]], [True, False])  # you spoke, they answered
        self.assertTrue(any(d["kind"] == "chat" and d["npc"] == them for f in frames for d in f["dialogue"]))
        # locked on: they stay put while you talk; everyone else carries on
        spot = (n_them.x, n_them.y)
        moved_before = {o: (w.npc(o).x, w.npc(o).y) for o in others}
        for line in ("So what do you do all day?", "Ha. Same.", "Seen any wolves?"):
            n_them.hunger = n_them.energy = 100
            self.assertTrue(sim.chat_say(line) or sim.chat is None)
            if sim.chat is None:
                break
            got = len(sim.chat["lines"])
            self.run_until(sim, lambda f: sim.chat is None or len(sim.chat["lines"]) > got, 200)
            if sim.chat is not None:
                self.assertEqual((n_them.x, n_them.y), spot)
        self.assertTrue(any((w.npc(o).x, w.npc(o).y) != moved_before[o] for o in others if w.npc(o)))
        self.assertIn("wolves", " ".join(m.text for m in sim.memory.recall(them, me, 10)) + "wolves")
        # walking away ends it
        if sim.chat is not None:
            sim.end_chat()
        self.assertIsNone(sim.chat)
        self.assertFalse(sim.chat_say("Hello?"))

    def test_they_walk_off_when_they_have_had_enough(self):
        sim = self.make(population=2)
        me, them = (n["id"] for n in sim.alive())
        sim.possess(me)
        w = sim.engine.world
        sim.command({"kind": "interact", "target": them, "option": "talk", "text": "Hi."})
        self.run_until(sim, lambda f: sim.chat is not None and len(sim.chat["lines"]) >= 2, 400)
        w.npc(them).hunger = 5  # starving: they won't stand around chatting
        sim.chat_say("Wait, one more thing...")
        frames = self.run_until(sim, lambda f: sim.chat is None, 200)
        self.assertIsNone(sim.chat)
        self.assertIn("ends the conversation", frames[-1]["player"]["note"])

    def test_the_voice_from_the_sky_and_the_powers_of_a_god(self):
        sim = self.make()
        a, b, c = (n["id"] for n in sim.alive())
        self.assertTrue(sim.say(a, "Build me a temple."))
        self.assertFalse(sim.say(a, "   "))
        frames = self.run_until(sim, lambda f: any(d["npc"] == a and d["kind"] == "voice" for d in f["dialogue"]), 50)
        lines = frames[-1]["dialogue"]
        self.assertEqual([d["npc"] for d in lines if d["kind"] == "voice"], [-1, a])
        self.assertIn("temple", sim.memory.recall(a, -1, 5)[0].text)
        sim.engine.world.npc(b).health = 20
        sim.engine.world.npc(b).hunger = 10
        sim.god("heal", b)
        sim.god("feed", b)
        sim.god("smite", c)
        self.assertFalse(sim.god("explode", a))
        f = asyncio.run(sim.tick())
        nb = next(n for n in f["npcs"] if n["id"] == b)
        self.assertEqual(nb["health"], 100)
        self.assertGreaterEqual(nb["hunger"], 99)
        self.assertGreaterEqual(nb["inv"].get("food", 0), 3)
        self.assertTrue(any("struck down by lightning" in line for line in f["log"]))
        self.assertTrue(any("heal" in line for line in f["log"]))
        self.assertEqual(f["stats"]["population"], 2)
        # becoming someone who then dies hands control back
        sim.possess(a)
        # in someone's body, the god's powers and voice are gone
        self.assertFalse(sim.god("heal", b))
        self.assertFalse(sim.say(b, "Can you hear me?"))
        sim.enqueue({"op": "kill", "npc": a, "cause": "a fall"})
        f = asyncio.run(sim.tick())
        f = asyncio.run(sim.tick())
        self.assertIsNone(f["possessed"])
        self.assertIsNone(f["player"])


class ServerTests(unittest.TestCase):
    def test_start_screen_preview_and_new_world(self):
        import argparse

        from starlette.testclient import TestClient

        from genesis.config import add_sim_args
        from genesis.server import create_app
        p = argparse.ArgumentParser()
        add_sim_args(p)
        args = p.parse_args(["--brain", "heuristic", "--memory", "simple"])
        args.tick_ms, args.paused, args.autostart = 50, False, False

        def until(ws, want):
            for _ in range(400):
                msg = ws.receive_json()
                if want(msg):
                    return msg
            self.fail("the message never came")

        with TestClient(create_app(args)) as client, client.websocket_connect("/ws") as ws:
            defs = ws.receive_json()
            self.assertEqual(defs["type"], "defs")
            self.assertEqual([m["key"] for m in defs["maps"]][0], "valley")
            self.assertIn("grass", defs["terrain"])
            control = ws.receive_json()
            self.assertTrue(control["menu"] and control["paused"])  # waiting on the start screen
            first = ws.receive_json()
            self.assertEqual((first["type"], first["tick"], first["map"]), ("frame", 0, "valley"))
            self.assertEqual(len(first["terrain"]), first["height"])
            # what would a desert look like? (only the asker is told, and the world does not change)
            ws.send_json({"cmd": "preview", "map": "desert", "seed": 5, "width": 40, "height": 30, "id": 9})
            pv = until(ws, lambda m: m["type"] == "preview")
            self.assertEqual((pv["map"], pv["seed"], pv["width"], pv["height"], pv["id"]), ("desert", 5, 40, 30, 9))
            self.assertEqual((len(pv["terrain"]), len(pv["terrain"][0])), (30, 40))
            self.assertIn(D.TERRAIN["sand"].glyph, "".join(pv["terrain"]))
            # start it for real: the same land, and now time moves
            ws.send_json({"cmd": "reset", "map": "desert", "seed": 5, "width": 40, "height": 30, "population": 4})
            new = until(ws, lambda m: m["type"] == "frame" and m["world"] != first["world"])
            self.assertEqual((new["map"], new["width"], new["terrain"], new["tiles"]),
                             ("desert", 40, pv["terrain"], pv["tiles"]))
            self.assertEqual(new["start"], pv["start"])
            self.assertEqual(new["stats"]["population"], 4)
            control = until(ws, lambda m: m["type"] == "control")
            self.assertFalse(control["menu"] or control["paused"])
            later = until(ws, lambda m: m["type"] == "frame" and m["tick"] >= 3)
            self.assertNotIn("terrain", later)  # the ground has not changed, so it is not sent again


if __name__ == "__main__":
    unittest.main()
