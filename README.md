# Secret hints for Castlevania: Belmont's Curse

A **read-only** tool: it never modifies the game or your save. It reads `BuildSave_*.sav`, compares it with data
extracted from the game files and tells you which secret zones you missed, with hints that get more specific the
further you expand them (district → room and position on the map → where to enter from).
The result is a self-contained `report.html`.

## Install (Windows)
1. Install Python 3.9+ from [python.org](https://www.python.org/downloads/) (keep the `py` launcher or add Python to PATH).
2. Run **`setup.bat`** – creates a local `.venv` and installs `requirements.txt`.
3. Run **`run_extract.bat`** – extracts the game data into `data/` (about a minute the first time, ~20 s afterwards).
   Repeat after every game update – the tool warns you when the data is out of date.
4. Run **`run_hints.bat`** – generates and opens `report.html`.

The game install and the save folder are auto-detected (Steam registry + `libraryfolders.vdf`, best effort for
Linux/Proton). To override, pass `--game` / `--save`, set `BC_GAME_DIR` / `BC_SAVE_DIR`, or copy
`config.example.json` to `config.json`.

## Options (`hints.py`, also via `run_hints.bat`)
| option | meaning |
|---|---|
| `--min-progress 90` | in-game completion (%) needed to unlock hints (default 90) |
| `--force` | ignore the threshold |
| `--level 1\|2\|3` | how much detail is printed in the console (the HTML report always has all three) |
| `--biome MainHall` | one area only |
| `--save <file>` | a specific save instead of the newest `BuildSave_*.sav` |
| `--json missing.json` | machine-readable list of missing zones |

## How it works
* The "game completion" threshold uses the game's own counter: `worldSaves[...].currentCompletionItemCount`
  divided by `totalCompletionCount` from the game's `CompletionAggregator`, summed over all worlds.
  If that data is missing, the tool falls back to the share of fully explored map chunks.
* A secret zone counts as found if its index is in
  `worldRoomExplorationStates[<world>_<room>].discoveredSecretZones`.
* "Resettable" zones (`resetableSecretZone`) are never recorded as discovered by the game, so they are not counted.
* A room's scene origin is `(cx + offX)·24, (cy + offY)·13` tiles (`editionOffsetCoord` from the room data).
* The "entrance" hint is a heuristic from the collision grid: which edges of the zone touch open space.
  It can be off by 1–2 tiles.
* The game only writes the save at save points, so the report shows the state of the last save.

## Known limitations
* Secret zones only – not chests, upgrades or keys (the report shows how many completion items are still missing).
* Verified in game for Main Hall (Boudoirs, Ballrooms, Dungeon); other areas use the same method.
* The game data in `data/` is generated locally from your install and is not part of this repository.

## Layout
```
extract_data.py   extracts rooms, collisions, zones, district names and completion totals (UnityPy)
hints.py          reads the save, finds missed zones, draws maps and the HTML report
paths.py          finds the game install and save folder
setup.bat         creates .venv and installs dependencies
run_extract.bat / run_hints.bat   run the scripts from .venv
```

MIT licensed (see `LICENSE`). Not affiliated with Konami or the game's developers.
