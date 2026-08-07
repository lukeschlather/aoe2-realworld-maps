# Stock map inventory: what is on disk, what the UI shows

Written 2026-08-07 after two agent-side failures that both traced to not
knowing this:

- An agent was asked to compare **Golden Pit** to our maps and reported it
  could not find it. Golden Pit ships as `goldenpit.rms2` - lowercase, no
  space, and a `.rms2` extension nobody had accounted for. The agent was
  globbing `*.rms`.
- An agent (this one) recommended porting `real_world_manchuria.rms` as a
  template without checking whether it is a live map. It is - it ships as
  **"Great Wall"**. The script name and the UI name are different, which is
  why it read as an unfamiliar map.

Both failures are the same failure: treating the filename as the identity of
the map. Check this file before drawing conclusions from a script name.

## Where map scripts live

| location | count | what it is |
|---|---|---|
| `resources\_common\drs\gamedata_x2\` | 196 | **the live stock scripts** - 180 `.rms` + 16 `.rms2` |
| `resources\_common\drs\gamedata_x2.backup.20201109\` | 109 | pre-2021-rework snapshot. Useful for diffing old vs modern; **not** live |
| `modes\Pompeii\resources\_common\drs\gamedata_x2\` | 28 | the Pompeii game mode's own overrides. Separate universe |
| `resources\_common\random-map-scripts\` | 1 | Workshop mods subscribed on this machine. **No stock content.** This is where our own mod installs |

Anything reasoning about "what the game does" must read
`resources\_common\drs\gamedata_x2\`, and must glob `*.rms` **and** `*.rms2`.

### `.rms` vs `.rms2`

16 scripts use `.rms2`. There is **no name collision** - no map exists as
both. The extension does not indicate a different language or feature set;
`goldenpit.rms2` is an ordinary script. It correlates with the
Forgotten-Empires-era community maps (Golden Pit, Acropolis, Budapest,
Cenotes, City of Lakes, Hamburger, Hideout, Hill Fort, Lombardia,
MegaRandom, Steppe, Valley) plus the four `CtR *` Capture-the-Relic maps.
Treat it as a historical accident of packaging, and always glob both.

## Resource-generation systems, by count

Classified by which resource include each script pulls (196 scripts):

| system | count | marker | examples |
|---|---|---|---|
| **A - modern** | 52 | `includes/starting_resources.inc` | Arabia, Arena, Black Forest, Coastal, Baltic, Scandinavia, Team Islands, Loch Ness, Migration, nomad, Yucatan, **real_world_manchuria** |
| **B - classic** | 94 | `GeneratingObjects.inc` (`GNR_*` defines) | Highland, Islands, Archipelago, Mediterranean, Continental, all `.rms2` community maps, **28 of the 29 `real_world_*` maps** |
| **inlined / community** | ~45 | no includes at all; ~6-7k lines, own actor-area numbering | Acclivity, Meadow, Lowland, Shrubland, Karsts … (authored by community mapmakers, headers carry a `BSV:` base-script version) |
| **mode variants** | ~9 | `EM *` (Empire Wars), `BR_*` (Battle Royale), `CtR *` | `EM Arabia`, `BR_FallofRome`, `CtR Spiral` |

Correcting an earlier note in `RESOURCE_TEMPLATES.md`: the claim that only
five maps use System A came from grepping `HERDABLE_STARTING_COUNT_`, which
undercounts badly - sheep are one include of a dozen.
`includes/starting_resources.inc` is the right marker.

### The `F_*.inc` family is essentially dead

`RESOURCE_TEMPLATES.md` earlier implied the flat `F_*.inc` files were part of
the classic resource system. They are not a resource system at all, and most
are unreferenced:

| include | referenced by |
|---|---|
| `F_ResByMap.inc` | **nothing** |
| `F_FarGoldStone.inc` | **nothing** |
| `F_Animals.inc` | **nothing** |
| `F_NearGoldStone.inc` | `real_world_mideast.rms` only |
| `thebr_setup.inc` | 28 scripts - but it is a *setup* include (seasons, terrain), not resources |

So there are two resource systems, not three. Dead includes on disk are not
evidence of a live code path - check the reference count first.

## Real World maps: script name -> UI name

This is the table that would have prevented the Manchuria mistake. Sources:
`RWM_*_ROLLOVER` keys and numeric string ids 30135-30144 in
`resources\en\strings\key-value\key-value-strings-utf8.txt`.

All 29 use **System B (classic)** except `manchuria`, which is the sole
System A port.

| script | UI name | diverges? |
|---|---|---|
| `real_world_amazon` | Amazon | |
| `real_world_antarctica` | Antarctica | |
| `real_world_aralsea` | Aral Sea | |
| `real_world_australia` | Australia | |
| `real_world_blacksea` | Black Sea | |
| `real_world_bohemia` | Bohemia | |
| `real_world_britain` | Britain | |
| `real_world_byzantium` | Byzantium | |
| `real_world_caribbean` | **Central America** | yes |
| `real_world_caucasus` | Caucasus | |
| `real_world_china` | China | |
| `real_world_eastafrica` | **Horn of Africa** | yes (inferred - `RWM_HORNOFAFRICA` is the only unclaimed key) |
| `real_world_france` | France | |
| `real_world_india` | India | |
| `real_world_indochina` | Indochina | |
| `real_world_indonesia` | Indonesia | |
| `real_world_italy` | Italy | |
| `real_world_jutland` | **Norse Lands** | yes |
| `real_world_madagascar` | Madagascar | |
| `real_world_malacca` | **Strait of Malacca** | yes |
| `real_world_manchuria` | **Great Wall** | yes |
| `real_world_mideast` | Mideast | |
| `real_world_nippon` | **Sea of Japan (East Sea)** | yes |
| `real_world_philippines` | Philippines | |
| `real_world_siberia` | Siberia | |
| `real_world_spain` | **Iberia** | yes |
| `real_world_texas` | Texas | |
| `real_world_westafrica` | West Africa | |
| `real_world_world` | **Earth** | yes |

Consequences for our cross-reference table in `RESOURCE_TEMPLATES.md`: our
**Caribbean** is not the stock "Caribbean" a player would recognise (that is
*Central America*), and our **Japan** corresponds to *Sea of Japan (East
Sea)*, which is a naval map framed around the strait rather than around the
Japanese islands. Those are different design intents from ours and the
name match is weaker than it looked.

## How to check this yourself

```sh
cd "/c/Program Files (x86)/Steam/steamapps/common/AoE2DE/resources/_common/drs/gamedata_x2"

# which resource system a script uses
grep -l "includes/starting_resources.inc" *.rms *.rms2   # System A
grep -l "GeneratingObjects.inc"           *.rms *.rms2   # System B

# script -> UI name
grep -n "^RWM_" ../../../../en/strings/key-value/key-value-strings-utf8.txt
awk '$1+0 >= 30125 && $1+0 <= 30165' ../../../../en/strings/key-value/key-value-strings-utf8.txt
```
