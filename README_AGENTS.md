# rwmaps

Generates AoE2 DE random map scripts whose land/water outline is a real place,
under any map projection. Output is one self-contained `.rms` per map — no
companion files, nothing in the game install — so the engine ships it to other
players automatically and the maps work in multiplayer.

```bash
uv run rwmaps-batch --sizes 220 240 --install      # the candidate set
uv run rwmaps "Great Lakes" --region greatlakes --rotate 45 --players 8 --install
uv run pytest tests -q
```

Then restart AoE2 DE → Skirmish → Map Style **Custom**.

## The one rule

**The lobby Map Size must match the number in the map name.** `rw_great_lakes_240`
needs `Huge [240]`.

Land areas are absolute tile counts while positions are percentages, so a size
mismatch breaks the map rather than degrading it: at `Large [220]` a 240-tuned
Great Lakes asks for more land tiles than the map has and the lakes vanish
entirely. That's why each map is generated once per size.

Lobby sizes: `Tiny 120 · Small 144 · Medium 168 · Normal 200 · Large 220 · Huge
240 · Ludicrous 480`. We generate 200 and up (6 players and up).

## How it works

1. **`projection.py`** — a square window in any projected CRS (`--proj` takes a
   name, a PROJ string, or an EPSG code); each tile centre is inverse-transformed
   to lon/lat.
2. **`raster.py`** — point-in-polygon against Natural Earth coastlines *in
   geographic space*, so limited-domain projections and the antimeridian can't
   produce garbage. Downloads and caches to `data/` on first use.
3. **`rms_land.py`** — greedily covers the land mask with discs, emitted as
   `create_land` blocks at absolute `land_position`. This is the whole trick:
   it's what lets a coastline live in plain script text.
4. **`analysis.py`** — picks start positions, clusters them into teams, and
   chooses `ai_info_map_type`.
5. **`rms.py`** — wraps it in water depths, forests, resources and fish.

Grid convention is `[y][x]`, row 0 north, column 0 west. The 45° tilt in game is
just the isometric camera — `--rotate 45` cancels it, so the landmass reads
north-up on screen. Much easier to recognise in play.

## Non-obvious things that are load-bearing

Every one of these was a bug found by running the maps, not by reading docs.

| | Why |
|---|---|
| Discs must **overlap** (`overlap=0.72`) | Circles don't tile the plane. Clearing exactly the disc you placed leaves a sliver between every pair of neighbours, and each sliver becomes a 1-tile pond. Cost 7.3% of the land as speckle and made the landmass unreadable; overlapping drops it to 2.5% and IoU 0.92 → 0.97. |
| One shared `land_id` for the whole coastline | Per-blob ids create hundreds of zones, which breaks `max_distance_to_other_zones` — the thing that keeps fish off the shore. With 700 ids, **no fish spawned at all**. |
| Script must create its own water depths | `base_terrain` is `WATER`, so `DEEP_WATER` doesn't exist until we make it. The shipped real-world scripts chain `MED_WATER` off `DEEP_WATER` because their `.scx` already supplies deep ocean; copying that gives a flat sea and deep-water fish have nowhere legal to sit. |
| `base_size` is a half-width | It alone covers `(2b+1)²` tiles. Setting it to the disc radius overshoots the budget and floods the map with land. |
| Tile budget needs rescaling (`FILL_FACTOR` 1.18) | Overlapping discs' areas sum to more than their union. Budget exactly and lands stop just shy of touching; slightly over and they meet. |
| `ai_info_map_type` keyed on **dock-worthy water**, not land fraction | `ARABIA` tells the AI there are no fish, so it never fish-booms. Great Lakes is 88% *land* but every start is on a lake — it must be `COASTAL`. Keying off land fraction also made the same geography flip type just by rotating it. |

## Candidates (8 players, 2 teams)

Land % first — on a team map, land area is what decides whether 8 players have
room. `ally`/`enemy` are mean start distances; allies closer than enemies is the
shape you want.

| map | land% | IoU | min sep | ally | enemy | ai |
|---|---|---|---|---|---|---|
| great_lakes_northup | 88.9 | 0.97 | 44 | 99 | 155 | COASTAL |
| great_lakes | 88.1 | 0.96 | 48 | 96 | 141 | COASTAL |
| black_sea | 76.8 | 0.97 | 52 | 97 | 152 | COASTAL |
| black_sea_northup | 76.4 | 0.97 | 60 | 112 | 163 | COASTAL |
| chesapeake | 63.8 | 0.96 | 40 | 88 | 91 | COASTAL |
| anatolia | 56.1 | 0.97 | 40 | 77 | 122 | COASTAL |
| iberia | 50.7 | 0.98 | 36 | 60 | 89 | MEDITERRANEAN |
| italy | 50.5 | 0.97 | 40 | 53 | 118 | MEDITERRANEAN |

Britain and Denmark are omitted: at 8 players their starts land ~20–26 tiles
apart and the analyzer flags them unfair.

Every run writes to `out/<UTC timestamp>/` with a 3-panel PNG per map: the real
coastline, what the script actually builds (olive = spill into sea, red =
missed land), and how the game draws it.

## What's missing

- **Town centre placement.** `analysis.choose_starts` picks a start tile from
  the land mask alone; where the engine actually drops the Town Centre within
  that assigned land isn't independently verified, and it's visibly not always
  great. See [`RENDER_PIPELINE.md`](RENDER_PIPELINE.md) for the real-render
  loop this needs and the plan to close it.
- **Resource fairness.** Fish exist and starts are placed deliberately, but
  nothing checks that each player has boar, berries, gold and stone at a sane
  distance and actually reachable. Depends on town centre placement being
  solid first, since resource placement is relative to the TC.
- **Real elevation.** Terrain height is procedural; a DEM could be draped.
- **A real multiplayer game.** Scripts are self-contained and `.rms`
  auto-transfer is documented behaviour, but only verified in single player.

## Verifying against the real engine

Coastline fidelity (IoU) and fairness are computed from the Python land mask
alone, before the game ever sees the script — good for fast iteration, but the
engine's random-map interpreter has its own behaviour that doesn't always
match. [`RENDER_PIPELINE.md`](RENDER_PIPELINE.md) documents a working,
UI-automation-driven pipeline that generates real `.aoe2scenario` files from
candidate scripts at scale, for tuning against actual engine output instead of
an approximation.
