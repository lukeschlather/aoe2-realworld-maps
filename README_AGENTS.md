# rwmaps: the concepts

What the project is optimising, how it decides whether it got there, and
how to change things without breaking them.

- **Mechanism** — what runs in what order: `GENERATION.md`
- **Conventions** — how to work here: `CLAUDE.md`
- **Stock reference** — the two stock resource systems and their measured
  budgets: `RESOURCE_TEMPLATES.md`
- **UI automation** — driving the editor: `EDITOR_AUTOMATION.md`

---

## 1. Aesthetics: the actual optimisation target

**A map succeeds when a human recognises the place.** Does a strait look
like a strait; does an island stay an island. Everything else is a
constraint, not the goal.

This matters because it is not what the metrics naturally measure. **IoU
against the true coastline is the headline number and it is easy to
misread.** Every Great Lakes window scores 0.88–0.91 whether the lakes come
out as five distinct lakes or one merged blob, because on an 85%-land map
IoU is dominated by land that is trivially correct. The feature you care
about is the negative space, and the metric cannot see it.

So: **pick the measurement that can see the thing being judged.**

| what you're judging | what to measure |
|---|---|
| coastline fidelity overall | `iou_10m` vs the 10m truth mask |
| do the lakes/bays read separately | enclosed water bodies, with sizes |
| do the islands survive | `preserved_fraction`, deleted vs merged |
| is it speckled | `pockmark`, `bnd_ratio` |

And two shape rules learned the hard way:

- **A narrow landmass cannot seat 8 players.** A long thin island chain has
  no interior, so every start is on the coastline and the per-player land
  distribution collapses however starts are arranged. New Zealand's worst
  player got 300 buildable tiles against Arabia's 2,676. No tuning fixes
  this.
- **Narrow archipelagos also look alike.** At 240 tiles they read as a
  generic scatter of slivers whichever real place they are — which forfeits
  the target above. Japan and New Zealand were visually interchangeable.
  Both were retired. Prefer chunky continental and peninsular windows.

Screen a window for **width, not just area**, before spending engine time:
`automation/window_candidates.py` renders any window's truth mask and
disc-cover in seconds.

---

## 2. Fairness: what it means here

Not the optimisation target — a real archipelago puts players on separate
islands and that is geography. It is a *constraint*: a window that starves
a player is unusable however good it looks.

### The model

`src/rwmaps/fairness.py`, `profile_capture()`. Three ideas:

1. **Distance, not counts.** Eight gold 12 tiles away and eight gold 38
   tiles away are different games.
2. **Exclusive / contested / unclaimed, not owned.** On tight maps most of
   what a player can reach a neighbour can reach too, so a contested
   resource counts for **both**. Unclaimed is the neutral pool nobody can
   reach — mostly a *good* thing, the supply players leave home to contest.
3. **Walked distances** on the walkable mask: water and forest are
   barriers, a ford is a route.

Two constants govern everything: `OWNERSHIP_RADIUS` (30 tiles) and
`CONTEST_MARGIN` (8 tiles).

> The other block in a capture record, `legacy_resources_nearest_tc`, is
> the superseded model — nearest-TC ownership, straight-line distances,
> ties broken by player index. It survives only so pre-2026-08 runs stay
> comparable, and it disagrees with the current model in both directions.

### Reading the numbers

**Stock is the yardstick.** Those maps define "reasonable"; this project
has no better definition. Arabia is held out as the reference rather than
averaged into a stock mean, because a mean over Arabia, Black Forest and
Team Islands describes no real map.

**Count resources, not objects** (`rwmaps/resource_value.py`). Six bushes
and six gold mines are both "6"; a boar is worth 3+ sheep *and* gathers
fastest because it is lured to the TC. Keep the per-kind breakdown beside
any food total.

Confirmed in game: gold 800/tile, stone 350, berries 125, deer 140 (the
whole `HUNTABLE_A` role, all nine skins), boar 340, one forest tile 100
wood. Sheep 100, small game 30 and the fish are still assumed.

**Quote the radius.** Arabia gives a player 9 stone within 30 walked tiles
and 16 within 50 — same map. `profile_capture` stamps `ownership_radius`
into its own output so a count can't travel without one.

**The unit is a player, not a map.** Pool per-player observations; map-wide
totals hide the only thing that matters.

**"Zero of a kind" is not a verdict.** Stock Arabia rolls
`percent_chance 50 GAME_HUNTABLE`, so half of all Arabia games contain no
deer anywhere. Read any such rate against a comparable stock map.

### Land is a resource

The one that cannot be topped up — a player can walk further for gold, not
for somewhere to put a farm. Measured the same exclusive/contested way;
buildable means dry and unforested.

Read the **worst-off player as a fraction of that map's own median**. Every
stock map holds **0.79–0.96**. Below ~0.7 the window, not the tuning, is
the problem.

### Where things stand (157 archived captures, all one model)

Per player, within 30 walked tiles. Full tables in the candidate report.

| | food | wood | gold | stone | land min/med |
|---|---|---|---|---|---|
| Arabia (reference) | 2,230 | 20,050 | 12,000 | 3,150 | 0.82 |
| stock range | 2,230–6,160 | 11,200–32,300 | 8,800–21,600 | 3,150–5,775 | 0.79–0.96 |
| shipped range | 2,590–2,990 | 8,250–26,250 | 8,800 | 3,150 | **0.23–0.84** |
| retired (Japan) | 2,590 | 6,700 | 5,600 | **875** | 0.60 |

Three things to know from that:

- **Food is nearly flat everywhere** (2,230–2,990 outside Yucatan), so it
  barely distinguishes maps. Gold, stone, wood and land are where maps
  differ.
- **The retired three failed on three independent axes** — stone, wood and
  land — which is why they were retired rather than tuned.
- **Italy (0.23) and Greece (0.42) are outside the stock land band and
  currently ship.** Open issue.

---

## 3. How to make changes

### The one rule

**The lobby Map Size must match the size baked into the script.** Land
areas are absolute tile counts while `land_position` is a percentage, so a
mismatch breaks the map rather than degrading it. Every shipped map is
240×240 and wants `Huge [240]`.

### The loop

```bash
uv run python automation/window_candidates.py          # screen windows, ~5s each, no engine
uv run python automation/preset_cli.py new LABEL "Map Name" -- --center=.. --span-km ..
uv run python automation/mod_capture.py --run-id <id> --presets LABEL --n-samples 2
uv run python automation/preset_import.py              # fold the run into the registry
uv run python automation/preset_report.py --presets LABEL <others>   # no engine time
uv run python automation/preset_cli.py promote LABEL --why "..."     # ships it into mod/ too
uv run python automation/install_mod.py --all
uv run pytest tests -q
```

`mod_capture.py --region-set <json>` drives windows that don't ship yet
(see `automation/candidate_set.py`), so a candidate can be captured without
adding it to `MOD_REGIONS`.

A full `build_mod.py` is ~70s of `choose_starts` annealing per region.
`choose_starts` uses a fixed RNG seed, so rebuilds are byte-identical and a
diff shows only what you actually changed.

### Non-obvious things that are load-bearing

Every one was a bug found by running the maps.

| | Why |
|---|---|
| Discs must **overlap** (`overlap=0.85`) | Circles don't tile the plane. Clearing exactly the disc you placed leaves a sliver between every neighbouring pair, and each becomes a 1-tile pond — 7.3% of the land as speckle, landmass unreadable. Overlapping drops it to 2.5% and IoU 0.92 → 0.97. |
| One shared `land_id` for the whole coastline | Per-blob ids create hundreds of zones, which breaks `max_distance_to_other_zones` — the thing keeping fish off the shore. With 700 ids, **no fish spawned at all**. |
| Script must create its own water depths | `base_terrain` is `WATER`, so `DEEP_WATER` doesn't exist until we make it. The shipped real-world scripts chain `MED_WATER` off `DEEP_WATER` because their `.scx` already supplies deep ocean; copying that gives a flat sea and deep-water fish nowhere legal to sit. |
| `base_size` is a half-width | It alone covers `(2b+1)²` tiles. Setting it to the disc radius floods the map with land. |
| Tile budget needs rescaling (`FILL_FACTOR` 1.18) | Overlapping discs' areas sum to more than their union. Budget exactly and lands stop just shy of touching. |
| Placeholder→land cleanup must be **repeated** (16×) | `create_terrain` grows clumps from random seeds until the budget is spent; nothing makes that walk visit every tile. One pass left 2–81 stranded tiles on **all 110** captures of a full pass, drawn in game as a black "placeholder" texture. Stock `includes/forest.inc` repeats the identical block 16× for this reason. |
| Water ids read backwards until 2026-08-16 | `MED_WATER` was id 22 and `DEEP_WATER` id 23 in Python, the reverse of both `constants.inc` and the tokens our own scripts emit — measured, a script's `DEEP_WATER` comes back as id 22, a mean 7.9 tiles from land against id 23's 2.8. Now `WATER_SHALLOW`/`WATER_MEDIUM`/`WATER_DEEP`, matching the engine. |
| A resource **role** has many skins; list them all | `themes.inc` re-skins every animal per biome, and `object_groups.inc` can redefine a role as a *group* of ids on top of that. Miss one and a whole role reads as zero — boar (2026-08-08) and small game (2026-08-16, 103 wild chickens counted as none). Check both files. |
| `place_on_specific_land_id` at a real island kills generation | Measured: Britain 0/5 generated with it against 5/5 without. Land ids are still emitted; only the object clause is off. |
| `ai_info_map_type` keyed on **dock-worthy water**, not land fraction | `ARABIA` tells the AI there are no fish, so it never fish-booms. Great Lakes is 88% *land* but every start is on a lake — it must be `COASTAL`. Keying off land fraction also flipped the type just by rotating the same geography. |

### Orientation

`--north` is **screen space**: where north points in game, clockwise from
straight up. `0` (the default) is north-up; `-45` is the engine's
uncorrected view with north toward the upper left, which is what all eight
shipped maps use. Nothing outside `projection.py` deals in grid space.
