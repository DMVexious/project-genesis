// Project Genesis - deterministic game engine.
//
// Owns the grid, NPC base stats, pathfinding, collision, stat decay and all
// action outcomes. Speaks a line protocol on stdin/stdout:
//
//   INIT <seed> <width> <height> <npc_count> [decay_every=1]   -> state JSON
//   STATE                                      -> state JSON
//   STEP <id>:<action>[:<option>] ...          -> state JSON (with events)
//   QUIT
//
// This file is mirrored by orchestrator/genesis/engine_py.py. Any rule change
// must be made in both places; tests/test_genesis.py (test_cpp_matches_python) checks they agree.

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace {

// --- Tiles -------------------------------------------------------------------
constexpr char DIRT = '.', ROCK = '#', TREE = 'T', STUMP = 's', BUSH = 'B',
               BUSH_EMPTY = 'b', HUT = 'H', FARM = 'F', FARM_EMPTY = 'f', FIRE = 'C';

// --- Tuning (must match engine_py.py) ----------------------------------------
constexpr int STAT_MAX = 100;
constexpr int CHOP_WOOD = 4;
constexpr int HUT_COST = 10;
constexpr int SPEAR_COST = 5;
constexpr int FORAGE_FOOD = 2;
constexpr int EAT_HUNGER = 30;
constexpr int SLEEP_ENERGY = 20;
constexpr int SLEEP_HUT_ENERGY = 35;
constexpr int SLEEP_HUT_HEAL = 2;
constexpr int COST_CHOP = 5;
constexpr int COST_BUILD = 15;
constexpr int COST_FORAGE = 2;
constexpr int COST_CRAFT = 5;
constexpr int COST_ATTACK = 6;
constexpr int COST_DEFEND = 4;
constexpr int COST_MATE = 15;
constexpr int TREE_REGROW = 60;
constexpr int BUSH_REGROW = 40;
constexpr int MATURE_AGE = 60;
constexpr int START_AGE = 100;
constexpr int POP_CAP = 30;
constexpr int SIGHT = 20;
constexpr int GOTO_LIMIT = 60;
constexpr int BUILD_SEARCH = 6;
constexpr size_t MAX_TARGETS = 3;
constexpr int TALK_SOCIAL = 5;
constexpr int MATE_SOCIAL = 30;
constexpr int MATE_COOLDOWN = 100;      // world-time units a parent rests before another child
constexpr int MATE_PRIVACY = 1;        // nobody else may be this close when two NPCs mate
constexpr int FARM_FOOD = 3;
constexpr int FARM_REGROW = 25;
constexpr int COOK_BONUS = 15;          // eating next to a campfire
constexpr int SLEEP_FIRE_ENERGY = 28;   // sleeping next to a campfire (a hut beats it)
constexpr int ELDER_AGE = 700;          // from here on, each unit of world time may be the last
constexpr int OLD_AGE_ODDS = 40000;     // death chance per unit = (age - ELDER_AGE) / OLD_AGE_ODDS

// What NPCs can build. NPCs choose which; the engine only picks the nearest free tile.
struct Building {
    const char* name;
    char tile;
    int cost, energy;
};
constexpr Building BUILDINGS[] = {{"hut", HUT, HUT_COST, COST_BUILD}, {"farm", FARM_EMPTY, 6, 8}, {"fire", FIRE, 3, 4}};
constexpr int MIN_BUILD_COST = 3;

constexpr int DIRS[4][2] = {{0, -1}, {1, 0}, {0, 1}, {-1, 0}};  // N, E, S, W

// --- RNG: SplitMix64 -----------------------------------------------------------
struct Rng {
    uint64_t s;
    explicit Rng(uint64_t seed = 0) : s(seed) {}
    uint64_t next() {
        s += 0x9E3779B97F4A7C15ULL;
        uint64_t z = s;
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        return z ^ (z >> 31);
    }
    int below(int n) { return static_cast<int>(next() % static_cast<uint64_t>(n)); }
};

int cheb(int ax, int ay, int bx, int by) { return std::max(std::abs(ax - bx), std::abs(ay - by)); }
int manhattan(int ax, int ay, int bx, int by) { return std::abs(ax - bx) + std::abs(ay - by); }
int clamp0(int v) { return v < 0 ? 0 : v; }
int capMax(int v) { return v > STAT_MAX ? STAT_MAX : v; }

// --- JSON writer (only what we need: ints, bools, plain identifier strings) ---
struct Json {
    std::string out;
    bool first = true;
    void sep() { if (!first) out += ','; first = false; }
    void open(char c) { out += c; first = true; }
    void close(char c) { out += c; first = false; }
    void key(const char* k) { sep(); out += '"'; out += k; out += "\":"; first = true; }
    void str(const std::string& v) { sep(); out += '"'; out += v; out += '"'; }
    void num(long long v) { sep(); out += std::to_string(v); }
    void boolean(bool v) { sep(); out += v ? "true" : "false"; }
    void kv(const char* k, long long v) { key(k); num(v); }
    void kv(const char* k, const std::string& v) { key(k); str(v); }
    void kvb(const char* k, bool v) { key(k); boolean(v); }
};

struct Npc {
    int id, x, y, health, hunger, energy, social;
    int wood = 0, food = 0, spear = 0, age;
    int rest = 0;  // mating cooldown, counts down in world time
    bool alive = true, asleep = false;
    std::string cause;
};

struct Action {
    Action(std::string k = "") : kind(std::move(k)) {}  // NOLINT: implicit on purpose
    std::string kind;
    int x = 0, y = 0, target = 0, dist = 0, dx = 0, dy = 0;
    std::string what;
    std::vector<std::string> options;

    void write(Json& j) const {
        j.sep();
        j.open('{');
        j.kv("kind", kind);
        if (kind == "chop" || kind == "forage" || kind == "build") {
            if (kind == "build") j.kv("what", what);
            j.kv("x", x);
            j.kv("y", y);
        } else if (kind == "interact") {
            j.kv("target", target);
            j.key("options");
            j.open('[');
            for (const auto& o : options) j.str(o);
            j.close(']');
        } else if (kind == "goto") {
            j.kv("what", what);
            j.kv("dist", dist);
            j.kv("dx", dx);
            j.kv("dy", dy);
        } else if (kind == "goto_npc") {
            j.kv("target", target);
            j.kv("dist", dist);
            j.kv("dx", dx);
            j.kv("dy", dy);
        }
        j.close('}');
    }
};

struct BfsResult {
    bool found = false;
    int dist = 0, dx = 0, dy = 0;
};

struct Frontier {
    int x, y, fdx, fdy;
};

class World {
public:
    // Survival stats, ageing and regrowth advance once per `decayEvery` ticks
    // ("world time"), so actions can last several ticks without starving NPCs.
    World(uint64_t seed, int width, int height, int npcCount, int decayEvery = 1)
        : rng_(seed), w_(width), h_(height), decayEvery_(decayEvery < 1 ? 1 : decayEvery) {
        tiles_.reserve(static_cast<size_t>(w_ * h_));
        regrow_.assign(static_cast<size_t>(w_ * h_), 0);
        for (int i = 0; i < w_ * h_; ++i) {
            int r = rng_.below(100);
            if (r < 7) tiles_.push_back(ROCK);
            else if (r < 13) tiles_.push_back(TREE);
            else if (r < 16) tiles_.push_back(BUSH);
            else tiles_.push_back(DIRT);
        }
        for (int i = 0; i < npcCount; ++i) {
            int x, y;
            while (true) {
                x = rng_.below(w_);
                y = rng_.below(h_);
                if (tile(x, y) == DIRT && npcAt(x, y) == nullptr) break;
            }
            // Separate statements: argument evaluation order is unspecified in C++.
            int hunger = 60 + rng_.below(41);
            int energy = 60 + rng_.below(41);
            int social = rng_.below(50);
            spawn(x, y, 100, hunger, energy, social, START_AGE);
        }
        generateAllActions();
    }

    void step(const std::map<int, std::pair<int, int>>& choices) {
        ++tick_;
        events_.clear();
        for (auto& n : npcs_) n.asleep = false;

        std::vector<int> order;
        for (const auto& n : npcs_)
            if (n.alive) order.push_back(n.id);
        for (int i = static_cast<int>(order.size()) - 1; i > 0; --i) {
            int j = rng_.below(i + 1);
            std::swap(order[static_cast<size_t>(i)], order[static_cast<size_t>(j)]);
        }

        for (int nid : order) {
            Npc* n = npc(nid);
            auto it = choices.find(nid);
            if (!n->alive || it == choices.end()) continue;
            int idx = it->second.first, opt = it->second.second;
            auto ait = actions_.find(nid);
            if (ait == actions_.end() || idx < 0 || idx >= static_cast<int>(ait->second.size())) {
                fail(*n, "invalid");
                continue;
            }
            Action act = ait->second[static_cast<size_t>(idx)];
            execute(*n, act, opt);
        }

        if (tick_ % decayEvery_ == 0) worldTime(tick_ / decayEvery_);
        generateAllActions();
    }

    // Passive decay, ageing and regrowth: one step of world time.
    void worldTime(int phase) {
        for (auto& n : npcs_) {
            if (!n.alive) continue;
            n.hunger = clamp0(n.hunger - 1);
            n.social = std::min(STAT_MAX, n.social + 1);
            n.age += 1;
            n.rest = std::max(0, n.rest - 1);
            if (phase % 2 == 0) n.energy = clamp0(n.energy - 1);
            if (n.hunger == 0 || n.energy == 0) {
                if (n.hunger == 0) n.health = clamp0(n.health - 3);
                if (n.energy == 0) n.health = clamp0(n.health - 2);
                if (n.health <= 0) kill(n, n.hunger == 0 ? "starvation" : "exhaustion", -1);
            } else if (n.hunger >= 50 && n.energy >= 30 && n.health < STAT_MAX) {
                n.health += 1;
            }
            if (n.alive && n.age >= ELDER_AGE && rng_.below(OLD_AGE_ODDS) < n.age - ELDER_AGE)
                kill(n, "old age", -1);
        }

        for (int i = 0; i < w_ * h_; ++i) {
            auto ui = static_cast<size_t>(i);
            if (regrow_[ui] > 0) {
                regrow_[ui] -= 1;
                if (regrow_[ui] == 0) {
                    int x = i % w_, y = i / w_;
                    if (npcAt(x, y) != nullptr) regrow_[ui] = 1;
                    else if (tiles_[ui] == STUMP) tiles_[ui] = TREE;
                    else if (tiles_[ui] == BUSH_EMPTY) tiles_[ui] = BUSH;
                    else if (tiles_[ui] == FARM_EMPTY) tiles_[ui] = FARM;
                }
            }
        }
    }

    std::string state() const {
        Json j;
        j.open('{');
        j.kv("tick", tick_);
        j.kv("width", w_);
        j.kv("height", h_);
        j.key("tiles");
        j.open('[');
        for (int y = 0; y < h_; ++y)
            j.str(std::string(tiles_.begin() + y * w_, tiles_.begin() + (y + 1) * w_));
        j.close(']');

        j.key("npcs");
        j.open('[');
        for (const auto& n : npcs_) {
            j.sep();
            j.open('{');
            j.kv("id", n.id); j.kv("x", n.x); j.kv("y", n.y);
            j.kv("health", n.health); j.kv("hunger", n.hunger); j.kv("energy", n.energy);
            j.kv("social", n.social); j.kv("wood", n.wood); j.kv("food", n.food);
            j.kv("spear", n.spear); j.kv("age", n.age);
            j.kvb("alive", n.alive); j.kvb("asleep", n.asleep);
            j.kv("cause", n.cause);
            j.close('}');
        }
        j.close(']');

        j.key("actions");
        j.open('{');
        for (const auto& [id, acts] : actions_) {
            j.key(std::to_string(id).c_str());
            j.open('[');
            for (const auto& a : acts) a.write(j);
            j.close(']');
        }
        j.close('}');

        j.key("events");
        j.open('[');
        for (const auto& e : events_) {
            j.sep();
            j.out += e;
        }
        j.close(']');
        j.close('}');
        return j.out;
    }

private:
    Rng rng_;
    int w_, h_;
    int decayEvery_;
    int tick_ = 0;
    std::vector<char> tiles_;
    std::vector<int> regrow_;
    std::vector<Npc> npcs_;
    int nextId_ = 1;
    std::map<int, std::vector<Action>> actions_;
    std::vector<std::string> events_;  // pre-serialised JSON objects

    // --- helpers --------------------------------------------------------------
    bool inb(int x, int y) const { return x >= 0 && x < w_ && y >= 0 && y < h_; }
    char tile(int x, int y) const { return tiles_[static_cast<size_t>(y * w_ + x)]; }
    void setTile(int x, int y, char t) { tiles_[static_cast<size_t>(y * w_ + x)] = t; }
    bool passable(int x, int y) const {
        if (!inb(x, y)) return false;
        char t = tile(x, y);
        return t == DIRT || t == STUMP;
    }

    Npc* npc(int id) {
        for (auto& n : npcs_)
            if (n.id == id) return &n;
        return nullptr;
    }
    const Npc* npcAt(int x, int y) const {
        for (const auto& n : npcs_)
            if (n.alive && n.x == x && n.y == y) return &n;
        return nullptr;
    }
    int aliveCount() const {
        int c = 0;
        for (const auto& n : npcs_) c += n.alive ? 1 : 0;
        return c;
    }

    void spawn(int x, int y, int health, int hunger, int energy, int social, int age) {
        Npc n;
        n.id = nextId_++;
        n.x = x; n.y = y;
        n.health = health; n.hunger = hunger; n.energy = energy; n.social = social;
        n.age = age;
        npcs_.push_back(n);
    }

    bool adjacentTile(const Npc& n, char kind, int& ox, int& oy) const {
        for (const auto& d : DIRS) {
            int x = n.x + d[0], y = n.y + d[1];
            if (inb(x, y) && tile(x, y) == kind) {
                ox = x; oy = y;
                return true;
            }
        }
        return false;
    }

    bool nearTile(int x, int y, char kind) const {
        for (const auto& d : DIRS) {
            int nx = x + d[0], ny = y + d[1];
            if (inb(nx, ny) && tile(nx, ny) == kind) return true;
        }
        return false;
    }

    // Breadth-first search (uniform-cost Dijkstra) for the nearest tile
    // satisfying `goal`; returns distance and the first step to take.
    template <typename Goal>
    BfsResult bfs(int sx, int sy, Goal goal, int limit) const {
        BfsResult r;
        if (goal(sx, sy)) { r.found = true; return r; }
        std::vector<char> seen(static_cast<size_t>(w_ * h_), 0);
        seen[static_cast<size_t>(sy * w_ + sx)] = 1;
        std::vector<Frontier> frontier{{sx, sy, 0, 0}};
        int dist = 0;
        while (!frontier.empty() && dist < limit) {
            ++dist;
            std::vector<Frontier> nxt;
            for (const auto& f : frontier) {
                for (const auto& d : DIRS) {
                    int nx = f.x + d[0], ny = f.y + d[1];
                    if (!passable(nx, ny) || seen[static_cast<size_t>(ny * w_ + nx)]) continue;
                    seen[static_cast<size_t>(ny * w_ + nx)] = 1;
                    int sdx = dist == 1 ? d[0] : f.fdx;
                    int sdy = dist == 1 ? d[1] : f.fdy;
                    if (goal(nx, ny)) {
                        r.found = true; r.dist = dist; r.dx = sdx; r.dy = sdy;
                        return r;
                    }
                    nxt.push_back({nx, ny, sdx, sdy});
                }
            }
            frontier = std::move(nxt);
        }
        return r;
    }

    bool buildSite(const Npc& n, int& ox, int& oy) const {
        std::vector<char> seen(static_cast<size_t>(w_ * h_), 0);
        seen[static_cast<size_t>(n.y * w_ + n.x)] = 1;
        std::vector<std::pair<int, int>> frontier{{n.x, n.y}};
        for (int step = 0; step < BUILD_SEARCH; ++step) {
            std::vector<std::pair<int, int>> nxt;
            for (const auto& [x, y] : frontier) {
                for (const auto& d : DIRS) {
                    int nx = x + d[0], ny = y + d[1];
                    if (!passable(nx, ny) || seen[static_cast<size_t>(ny * w_ + nx)]) continue;
                    seen[static_cast<size_t>(ny * w_ + nx)] = 1;
                    if (tile(nx, ny) == DIRT && npcAt(nx, ny) == nullptr) {
                        ox = nx; oy = ny;
                        return true;
                    }
                    nxt.emplace_back(nx, ny);
                }
            }
            frontier = std::move(nxt);
        }
        return false;
    }

    bool mateOk(const Npc& a, const Npc& b) const {
        if (a.age < MATURE_AGE || b.age < MATURE_AGE || a.age >= ELDER_AGE || b.age >= ELDER_AGE) return false;
        if (a.social < MATE_SOCIAL || b.social < MATE_SOCIAL || a.rest > 0 || b.rest > 0) return false;
        if (a.hunger < 40 || b.hunger < 40 || a.health < 50 || b.health < 50) return false;
        if (aliveCount() >= POP_CAP) return false;
        for (const auto& o : npcs_)
            if (o.alive && o.id != a.id && o.id != b.id && cheb(o.x, o.y, a.x, a.y) <= MATE_PRIVACY) return false;
        return true;
    }
    static bool tradeOk(const Npc& a, const Npc& b) {
        return (a.wood >= 2 && b.food >= 1) || (a.food >= 1 && b.wood >= 2);
    }
    std::vector<std::string> interactOptions(const Npc& a, const Npc& b) const {
        std::vector<std::string> o{"talk", "fight"};
        if (tradeOk(a, b)) o.emplace_back("trade");
        if (mateOk(a, b)) o.emplace_back("mate");
        return o;
    }

    // --- action generation ------------------------------------------------------
    std::vector<Action> generateActions(const Npc& n) const {
        std::vector<Action> acts;
        acts.push_back({"sleep"});
        if (n.energy == 0) return acts;
        if (n.food > 0) acts.push_back({"eat"});
        int tx, ty;
        if (adjacentTile(n, TREE, tx, ty)) { Action a{"chop"}; a.x = tx; a.y = ty; acts.push_back(a); }
        if (adjacentTile(n, FARM, tx, ty) || adjacentTile(n, BUSH, tx, ty)) {  // crops first
            Action a{"forage"}; a.x = tx; a.y = ty; acts.push_back(a);
        }
        if (n.wood >= MIN_BUILD_COST && buildSite(n, tx, ty)) {
            for (const auto& b : BUILDINGS) {
                if (n.wood < b.cost) continue;
                Action a{"build"}; a.what = b.name; a.x = tx; a.y = ty; acts.push_back(a);
            }
        }
        if (n.wood >= SPEAR_COST && n.spear == 0) acts.push_back({"craft_spear"});

        std::vector<const Npc*> others;
        for (const auto& o : npcs_)
            if (o.alive && o.id != n.id) others.push_back(&o);

        size_t adj = 0;
        for (const Npc* o : others) {
            if (cheb(o->x, o->y, n.x, n.y) > 1) continue;
            if (adj++ >= MAX_TARGETS) break;
            Action a{"interact"};
            a.target = o->id;
            a.options = interactOptions(n, *o);
            acts.push_back(a);
        }

        const std::pair<const char*, char> places[] = {
            {"tree", TREE}, {"bush", BUSH}, {"hut", HUT}, {"farm", FARM}, {"fire", FIRE}};
        for (const auto& [what, kind] : places) {
            char k = kind;
            BfsResult r = bfs(n.x, n.y, [this, k](int x, int y) { return nearTile(x, y, k); }, GOTO_LIMIT);
            if (r.found && r.dist > 0) {
                Action a{"goto"};
                a.what = what; a.dist = r.dist; a.dx = r.dx; a.dy = r.dy;
                acts.push_back(a);
            }
        }

        std::vector<const Npc*> far;
        for (const Npc* o : others)
            if (cheb(o->x, o->y, n.x, n.y) > 1 && manhattan(o->x, o->y, n.x, n.y) <= SIGHT) far.push_back(o);
        std::stable_sort(far.begin(), far.end(), [&n](const Npc* a, const Npc* b) {
            int da = manhattan(a->x, a->y, n.x, n.y), db = manhattan(b->x, b->y, n.x, n.y);
            return da != db ? da < db : a->id < b->id;
        });
        for (size_t i = 0; i < far.size() && i < MAX_TARGETS; ++i) {
            int ox = far[i]->x, oy = far[i]->y;
            BfsResult r = bfs(n.x, n.y, [ox, oy](int x, int y) { return cheb(x, y, ox, oy) <= 1; }, GOTO_LIMIT);
            if (r.found && r.dist > 0) {
                Action a{"goto_npc"};
                a.target = far[i]->id; a.dist = r.dist; a.dx = r.dx; a.dy = r.dy;
                acts.push_back(a);
            }
        }

        for (const auto& d : DIRS) {
            if (passable(n.x + d[0], n.y + d[1])) {
                acts.push_back({"wander"});
                break;
            }
        }
        return acts;
    }

    void generateAllActions() {
        actions_.clear();
        for (const auto& n : npcs_)
            if (n.alive) actions_[n.id] = generateActions(n);
    }

    // --- events -------------------------------------------------------------------
    void event(const std::string& type, int npcId, const std::string& extra = "") {
        std::string e = "{\"type\":\"" + type + "\",\"npc\":" + std::to_string(npcId);
        if (!extra.empty()) e += "," + extra;
        e += "}";
        events_.push_back(e);
    }
    static std::string kvs(const char* k, long long v) { return std::string("\"") + k + "\":" + std::to_string(v); }
    static std::string kvs(const char* k, const std::string& v) { return std::string("\"") + k + "\":\"" + v + "\""; }
    static std::string kvs(const char* k, bool v) { return std::string("\"") + k + "\":" + (v ? "true" : "false"); }

    void kill(Npc& n, const std::string& cause, int killer) {
        n.alive = false;
        n.health = 0;
        n.cause = cause;
        event("death", n.id, kvs("cause", cause) + "," + kvs("killer", static_cast<long long>(killer)));
    }
    void fail(const Npc& n, const std::string& kind) { event("fail", n.id, kvs("kind", kind)); }

    void attack(Npc& a, Npc& b) {
        a.energy = clamp0(a.energy - COST_ATTACK);
        bool hit = rng_.below(100) < 75;
        int dmg = 0;
        if (hit) {
            dmg = 8 + rng_.below(8) + 12 * a.spear;
            b.health = clamp0(b.health - dmg);
        }
        event("attack", a.id, kvs("target", static_cast<long long>(b.id)) + "," + kvs("hit", hit) + "," +
                                  kvs("damage", static_cast<long long>(dmg)) + "," + kvs("retaliation", false));
        if (b.health <= 0) {
            a.wood += b.wood; a.food += b.food;
            b.wood = b.food = 0;
            kill(b, "combat", a.id);
            return;
        }
        if (rng_.below(100) < 50) {
            int rdmg = 5 + rng_.below(6) + 12 * b.spear;
            a.health = clamp0(a.health - rdmg);
            b.energy = clamp0(b.energy - COST_DEFEND);
            event("attack", b.id, kvs("target", static_cast<long long>(a.id)) + "," + kvs("hit", true) + "," +
                                      kvs("damage", static_cast<long long>(rdmg)) + "," + kvs("retaliation", true));
            if (a.health <= 0) {
                b.wood += a.wood; b.food += a.food;
                a.wood = a.food = 0;
                kill(a, "combat", b.id);
            }
        }
    }

    bool move(Npc& n, int dx, int dy) {
        if (!passable(n.x + dx, n.y + dy)) return false;
        n.x += dx; n.y += dy;
        return true;
    }

    void execute(Npc& n, const Action& act, int opt) {
        const std::string& k = act.kind;
        if (k == "sleep") {
            int hx, hy;
            bool nearHut = adjacentTile(n, HUT, hx, hy);
            int gain = nearHut ? SLEEP_HUT_ENERGY : (adjacentTile(n, FIRE, hx, hy) ? SLEEP_FIRE_ENERGY : SLEEP_ENERGY);
            n.energy = capMax(n.energy + gain);
            if (nearHut) n.health = capMax(n.health + SLEEP_HUT_HEAL);
            n.asleep = true;
            event("sleep", n.id, kvs("hut", nearHut));
        } else if (k == "eat") {
            if (n.food <= 0) return fail(n, k);
            n.food -= 1;
            int fx, fy;
            bool cooked = adjacentTile(n, FIRE, fx, fy);
            n.hunger = capMax(n.hunger + EAT_HUNGER + (cooked ? COOK_BONUS : 0));
            event("eat", n.id, kvs("cooked", cooked));
        } else if (k == "chop") {
            if (tile(act.x, act.y) != TREE || manhattan(act.x, act.y, n.x, n.y) != 1) return fail(n, k);
            setTile(act.x, act.y, STUMP);
            regrow_[static_cast<size_t>(act.y * w_ + act.x)] = TREE_REGROW;
            n.wood += CHOP_WOOD;
            n.energy = clamp0(n.energy - COST_CHOP);
            event("chop", n.id, kvs("x", static_cast<long long>(act.x)) + "," + kvs("y", static_cast<long long>(act.y)));
        } else if (k == "forage") {
            char t = tile(act.x, act.y);
            if ((t != BUSH && t != FARM) || manhattan(act.x, act.y, n.x, n.y) != 1) return fail(n, k);
            bool farm = t == FARM;
            setTile(act.x, act.y, farm ? FARM_EMPTY : BUSH_EMPTY);
            regrow_[static_cast<size_t>(act.y * w_ + act.x)] = farm ? FARM_REGROW : BUSH_REGROW;
            n.food += farm ? FARM_FOOD : FORAGE_FOOD;
            n.energy = clamp0(n.energy - COST_FORAGE);
            event("forage", n.id, kvs("x", static_cast<long long>(act.x)) + "," + kvs("y", static_cast<long long>(act.y)));
        } else if (k == "build") {
            const Building* spec = nullptr;
            for (const auto& b : BUILDINGS)
                if (act.what == b.name) spec = &b;
            if (spec == nullptr || n.wood < spec->cost || tile(act.x, act.y) != DIRT || npcAt(act.x, act.y) != nullptr)
                return fail(n, k);
            setTile(act.x, act.y, spec->tile);
            if (spec->tile == FARM_EMPTY) regrow_[static_cast<size_t>(act.y * w_ + act.x)] = FARM_REGROW;
            n.wood -= spec->cost;
            n.energy = clamp0(n.energy - spec->energy);
            event("build", n.id, kvs("what", std::string(spec->name)) + "," + kvs("x", static_cast<long long>(act.x)) +
                                     "," + kvs("y", static_cast<long long>(act.y)));
        } else if (k == "craft_spear") {
            if (n.wood < SPEAR_COST || n.spear) return fail(n, k);
            n.wood -= SPEAR_COST;
            n.spear = 1;
            n.energy = clamp0(n.energy - COST_CRAFT);
            event("craft", n.id);
        } else if (k == "goto" || k == "goto_npc") {
            if (!move(n, act.dx, act.dy)) return fail(n, k);
        } else if (k == "wander") {
            std::vector<int> opts;
            for (int i = 0; i < 4; ++i)
                if (passable(n.x + DIRS[i][0], n.y + DIRS[i][1])) opts.push_back(i);
            if (opts.empty()) return fail(n, k);
            int d = opts[static_cast<size_t>(rng_.below(static_cast<int>(opts.size())))];
            move(n, DIRS[d][0], DIRS[d][1]);
        } else if (k == "interact") {
            Npc* t = npc(act.target);
            if (t == nullptr || !t->alive || cheb(t->x, t->y, n.x, n.y) > 1) return fail(n, k);
            std::string choice = (opt >= 0 && opt < static_cast<int>(act.options.size()))
                                     ? act.options[static_cast<size_t>(opt)] : "talk";
            auto now = interactOptions(n, *t);
            if (std::find(now.begin(), now.end(), choice) == now.end()) return fail(n, k);
            if (choice == "talk") {
                n.social = clamp0(n.social - TALK_SOCIAL);
                t->social = clamp0(t->social - TALK_SOCIAL);
                event("talk", n.id, kvs("target", static_cast<long long>(t->id)));
            } else if (choice == "fight") {
                attack(n, *t);
            } else if (choice == "trade") {
                std::string gave;
                if (n.wood >= 2 && t->food >= 1) {
                    n.wood -= 2; t->wood += 2; t->food -= 1; n.food += 1;
                    gave = "wood";
                } else {
                    n.food -= 1; t->food += 1; t->wood -= 2; n.wood += 2;
                    gave = "food";
                }
                event("trade", n.id, kvs("target", static_cast<long long>(t->id)) + "," + kvs("gave", gave));
            } else if (choice == "mate") {
                int tid = t->id;
                for (Npc* p : {&n, t}) {
                    p->hunger = clamp0(p->hunger - COST_MATE);
                    p->energy = clamp0(p->energy - COST_MATE);
                    p->social = 0;
                    p->rest = MATE_COOLDOWN;
                }
                int px = n.x, py = n.y, pid = n.id;
                spawn(px, py, 60, 70, 70, 0, 0);  // may reallocate npcs_: n and t are invalid after this
                int child = nextId_ - 1;
                event("birth", child, "\"parents\":[" + std::to_string(pid) + "," + std::to_string(tid) + "]");
            }
        }
    }
};

}  // namespace

int main() {
    std::ios::sync_with_stdio(false);
    World* world = nullptr;
    std::string line;
    while (std::getline(std::cin, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.rfind("\xEF\xBB\xBF", 0) == 0) line.erase(0, 3);  // UTF-8 BOM (PowerShell pipes)
        std::istringstream in(line);
        std::string cmd;
        if (!(in >> cmd)) {
            std::cout << "{\"error\":\"empty command\"}\n" << std::flush;
            continue;
        }
        std::transform(cmd.begin(), cmd.end(), cmd.begin(), [](unsigned char c) { return std::toupper(c); });
        if (cmd == "QUIT") break;
        if (cmd == "INIT") {
            unsigned long long seed;
            int w, h, n;
            if (!(in >> seed >> w >> h >> n) || w <= 0 || h <= 0 || n < 0) {
                std::cout << "{\"error\":\"bad command: INIT <seed> <w> <h> <n>\"}\n" << std::flush;
                continue;
            }
            int decayEvery = 1;
            if (!(in >> decayEvery)) decayEvery = 1;  // optional 5th argument
            delete world;
            world = new World(seed, w, h, n, decayEvery);
        } else if (world == nullptr) {
            std::cout << "{\"error\":\"not initialised\"}\n" << std::flush;
            continue;
        } else if (cmd == "STEP") {
            std::map<int, std::pair<int, int>> choices;
            std::string tok;
            bool ok = true;
            while (in >> tok) {
                std::vector<int> f;
                std::stringstream ts(tok);
                std::string part;
                while (std::getline(ts, part, ':')) {
                    char* end = nullptr;
                    long v = std::strtol(part.c_str(), &end, 10);
                    if (part.empty() || *end != '\0') { ok = false; break; }
                    f.push_back(static_cast<int>(v));
                }
                if (!ok || f.size() < 2) { ok = false; break; }
                choices[f[0]] = {f[1], f.size() > 2 ? f[2] : 0};
            }
            if (!ok) {
                std::cout << "{\"error\":\"bad command: STEP <id>:<action>[:<option>]\"}\n" << std::flush;
                continue;
            }
            world->step(choices);
        } else if (cmd != "STATE") {
            std::cout << "{\"error\":\"unknown command " << cmd << "\"}\n" << std::flush;
            continue;
        }
        std::cout << world->state() << "\n" << std::flush;
    }
    delete world;
    return 0;
}
