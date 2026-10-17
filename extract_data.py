"""One-time extraction of game data (rooms, collisions, secret zones, district names, completion totals).

Output goes to data/<World>.json. Re-run after a game update (the tool detects a changed
GameAssembly.dll and rebuilds the asset index).

    python extract_data.py [--game "<game folder>"] [--biomes 06_MainHall,01_Streets_Poor] [--jobs 8]

If the Map Hints mod is installed (<game>/BepInEx/plugins/MapHints), the data is also copied there
(skip with --no-plugin-export).
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from multiprocessing import Pool

import UnityPy

import paths

DATA_VERSION = 3  # bump when the data/*.json format changes
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BUNDLES = None  # ustawiane w main()


# ---------- asset path -> bundle index ----------
def _index_one(path):
    try:
        env = UnityPy.load(path)
        return os.path.basename(path), list(env.container.keys())
    except Exception:
        return os.path.basename(path), []


def build_index(jobs):
    files = [os.path.join(BUNDLES, f) for f in os.listdir(BUNDLES) if f.endswith(".bundle")]
    print(f"Indexing {len(files)} bundles (this takes a few minutes)...", flush=True)
    idx = {}
    with Pool(jobs) as pool:
        for n, (name, keys) in enumerate(pool.imap_unordered(_index_one, files, chunksize=8), 1):
            for k in keys:
                idx[k] = name
            if n % 500 == 0:
                print(f"  {n}/{len(files)}", flush=True)
    return idx


def load_index(game, jobs):
    ipath = os.path.join(DATA, "container_index.json")
    spath = os.path.join(DATA, "game_stamp.json")
    stamp = paths.game_stamp(game)
    old = json.load(open(spath)) if os.path.exists(spath) else None
    if os.path.exists(ipath) and old == stamp:
        return json.load(open(ipath))
    if os.path.exists(ipath) and old is None:
        # index without a stamp (e.g. copied over) - assume it matches and record the stamp
        json.dump(stamp, open(spath, "w"))
        return json.load(open(ipath))
    idx = build_index(jobs)
    json.dump(idx, open(ipath, "w"))
    json.dump(stamp, open(spath, "w"))
    return idx


# ---------- helpers ----------
_env_cache = {}


def env_for(idx, path):
    b = idx[path]
    if b not in _env_cache:
        if len(_env_cache) > 6:
            _env_cache.clear()
        _env_cache[b] = UnityPy.load(os.path.join(BUNDLES, b))
    return _env_cache[b]


def typetrees(idx, path):
    env = env_for(idx, path)
    for q, o in env.container.items():
        if q == path:
            yield env, o.read_typetree()


def camel_split(s):
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s.replace("_", " ")).strip()


# ---------- scenes: secret zones and collectibles ----------
# Savable entities worth pointing out (normalized GameObject name -> kind). The game stores
# {"activated": true} for the ones already collected / opened / destroyed.
ITEM_KINDS = {
    "GPE_Chest": "chest",
    "GPE_ChestUniqueLooter": "chest",
    "GPE_UpgradeCollectibleHealth": "health",
    "GPE_UpgradeCollectibleMana": "mana",
    "GPE_ConsumableAtkUpBook": "atk_book",
    "GPE_ConsumableDefUpBook": "def_book",
    "GPE_DestructibleSourceExperience": "xp",
}


def norm_name(n):
    return re.sub(r"[\s_]*\(?\d+\)?$", "", n.replace("(Clone)", "")).strip()


def scene_zones(args):
    path, bundle = args
    out = []
    items = []
    try:
        env = UnityPy.load(bundle)
        objs = {o.path_id: o for o in env.objects}
        tr = {o.path_id: o.read_typetree() for o in env.objects if o.type.name == "Transform"}
        gotr = {t["m_GameObject"]["m_PathID"]: pid for pid, t in tr.items()}

        def wpos(pid):
            x = y = 0.0
            while pid and pid in tr:
                t = tr[pid]
                x += t["m_LocalPosition"]["x"]
                y += t["m_LocalPosition"]["y"]
                pid = t["m_Father"]["m_PathID"]
            return x, y

        for o in env.objects:
            if o.type.name != "MonoBehaviour":
                continue
            t = o.read_typetree()
            if "_savableIDs" in t:  # layer component listing the savable entities of the scene
                for e in t["_savableIDs"]:
                    so = objs.get(e["SavableEntity"]["m_PathID"])
                    if so is None:
                        continue
                    gid = so.read_typetree()["m_GameObject"]["m_PathID"]
                    kind = ITEM_KINDS.get(norm_name(objs[gid].read_typetree()["m_Name"]))
                    if kind and gid in gotr:
                        wx, wy = wpos(gotr[gid])
                        items.append({"guid": e["Id"], "kind": kind, "wx": wx, "wy": wy})
                continue
            if "secretZoneIndex" not in t or "selfEntity" not in t:
                continue
            go = objs[t["m_GameObject"]["m_PathID"]].read_typetree()
            tp = [c["component"]["m_PathID"] for c in go["m_Component"] if c["component"]["m_PathID"] in tr]
            wx, wy = wpos(tp[0]) if tp else (0.0, 0.0)
            out.append({
                "idx": t["secretZoneIndex"],
                "resetable": int(t.get("resetableSecretZone", 0)),
                "nosfx": int(t.get("isSecretZoneNoSfx", 0)),
                "wx": wx, "wy": wy,
                "w": t["size"]["x"], "h": t["size"]["y"],
                "active": bool(go.get("m_IsActive", 1)),
            })
    except Exception as e:  # a single scene must not abort the whole run
        return path, {"zones": out, "items": items}, repr(e)
    return path, {"zones": out, "items": items}, None


def export_to_plugin(game):
    """Copy the data the Map Hints mod reads into its plugin folder (only when the mod is installed)."""
    dest = os.path.join(game, "BepInEx", "plugins", "MapHints")
    if not os.path.isdir(dest):
        return
    dest = os.path.join(dest, "data")
    os.makedirs(dest, exist_ok=True)
    n = 0
    for name in os.listdir(DATA):
        if name.endswith(".json"):
            shutil.copy2(os.path.join(DATA, name), os.path.join(dest, name))
            n += 1
    print(f"Map Hints mod: copied {n} data files to {dest}", flush=True)


def main():
    global BUNDLES
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", help="game install folder (default: auto-detected via Steam)")
    ap.add_argument("--biomes", default="", help="e.g. 06_MainHall,01_Streets_Poor (default: all)")
    ap.add_argument("--jobs", type=int, default=max(2, (os.cpu_count() or 4) - 2))
    ap.add_argument("--no-plugin-export", action="store_true",
                    help="do not copy the data into the Map Hints mod folder")
    a = ap.parse_args()
    game = paths.find_game_dir(a.game)
    if not game:
        sys.exit("Game install not found.\n"
                 "Pass --game \"<folder>\", set BC_GAME_DIR or game_dir in config.json.")
    a.game = game
    print(f"Game: {game}", flush=True)
    BUNDLES = os.path.join(a.game, "Castlevania Belmont's Curse_Data", "StreamingAssets", "aa", "StandaloneWindows64")
    os.makedirs(DATA, exist_ok=True)
    t0 = time.time()
    idx = load_index(a.game, a.jobs)
    print(f"Index: {len(idx)} assets", flush=True)

    # English names from all localization tables
    names = {}
    for p in idx:
        if p.startswith("Assets/Data/Localization/StringTableCollections/") and p.endswith("_en.asset"):
            for _, t in typetrees(idx, p):
                for e in t.get("m_TableData", []):
                    names[e["m_Id"]] = e["m_Localized"]

    # "completion" totals (what the game compares currentCompletionItemCount from the save against)
    totals = {}
    cp = "Assets/Data/GameManagement/Logic/CompletionAggregator.asset"
    if cp in idx:
        for _, t in typetrees(idx, cp):
            for w in t.get("worldCompletionCounts", []):
                totals[w["worldSaveKey"]] = w["totalCompletionCount"]
    with open(os.path.join(DATA, "completion.json"), "w", encoding="utf-8") as f:
        json.dump(totals, f)
    print(f"Completion: {len(totals)} worlds, total {sum(totals.values())}", flush=True)

    # districts: unique room ID -> name
    room2name = {}
    for p in sorted(idx):
        m = re.match(r"Assets/Data/DistrictData/(DistrictData_\w+)/(\w+_District\d+)\.asset$", p)
        if not m or "GymUI" in m.group(1):
            continue
        disp = p.replace(".asset", "_Display.asset")
        if disp not in idx:
            continue
        rooms = []
        for _, t in typetrees(idx, p):
            rooms += [r["worldRoomUniqueID"] for r in t.get("districtRooms", [])]
        title = None
        for _, t in typetrees(idx, disp):
            if "districtDisplayName" in t:
                title = names.get(t["districtDisplayName"]["m_TableEntryReference"]["m_KeyId"])
        for r in rooms:
            room2name[r] = title or m.group(2)
    print(f"Districts: {len(room2name)} rooms assigned", flush=True)

    want = {w.strip() for w in a.biomes.split(",") if w.strip()}
    worlds = [p for p in sorted(idx) if re.match(r"Assets/Data/RoomDesigner/Worlds/World_Biome_\d+_\w+\.asset$", p)]
    if want:
        worlds = [p for p in worlds if any(w in p for w in want)]

    # rooms + collisions
    biomes = {}
    tasks = []
    for p in worlds:
        wname = os.path.basename(p)[:-6]
        env = env_for(idx, p)
        objs = {o.path_id: o for o in env.objects}
        rooms_raw = None
        for _, t in typetrees(idx, p):
            if "_rooms" in t:
                rooms_raw = t["_rooms"]
        rooms = {}
        for r in rooms_raw:
            scene_path = r["sceneAddressableEntryAddress"]
            scene = scene_path.split("/")[-1][:-6]
            rd = objs.get(r["roomData"]["m_PathID"])
            if rd is None:
                continue
            t = rd.read_typetree()
            rooms[scene] = {
                "cx": r["positionCoord"]["x"], "cy": r["positionCoord"]["y"],
                "w": t["_width"], "h": t["_height"],
                "cs": [t["chunkSize"]["x"], t["chunkSize"]["y"]],
                "off": [t["editionOffsetCoord"]["x"], t["editionOffsetCoord"]["y"]],
                "col": ["".join(chr(48 + min(v, 70)) for v in c["_collisions"]) for c in t["chunks"]],
                "path": scene_path,
            }
            if scene_path in idx:
                tasks.append((scene_path, os.path.join(BUNDLES, idx[scene_path])))
        biomes[wname] = {"world": wname, "title": camel_split(wname.split("_", 3)[-1]), "rooms": rooms, "zones": [], "items": []}
        print(f"{wname}: {len(rooms)} rooms", flush=True)

    # scenes -> zones (in parallel)
    print(f"Scanning {len(tasks)} scenes...", flush=True)
    res = {}
    with Pool(a.jobs) as pool:
        for n, (path, found, err) in enumerate(pool.imap_unordered(scene_zones, tasks, chunksize=2), 1):
            res[path] = found
            if err:
                print("  warning:", path.split("/")[-1], err, flush=True)
            if n % 50 == 0:
                print(f"  {n}/{len(tasks)}", flush=True)

    for wname, b in biomes.items():
        for scene, r in b["rooms"].items():
            # scene origin = room position + edition offset (in chunks)
            ox = (r["cx"] + r["off"][0]) * r["cs"][0]
            oy = (r["cy"] + r["off"][1]) * r["cs"][1]
            found = res.get(r["path"], {"zones": [], "items": []})
            for z in found["zones"]:
                b["zones"].append({
                    "room": scene, "idx": z["idx"], "resetable": z["resetable"], "nosfx": z["nosfx"],
                    "x": int(round(z["wx"] - 0.5 - ox)), "y": int(round(z["wy"] - 0.5 - oy)),
                    "w": z["w"], "h": z["h"], "active": z["active"],
                })
            for it in found["items"]:
                b["items"].append({
                    "room": scene, "guid": it["guid"], "kind": it["kind"],
                    "x": round(it["wx"] - ox, 1), "y": round(it["wy"] - oy, 1),
                })
        b["room_district"] = {s: room2name.get(f"{wname}_{s}") for s in b["rooms"]}
        for r in b["rooms"].values():
            r.pop("path", None)
        json.dump(b, open(os.path.join(DATA, wname + ".json"), "w"))
        n_all = len(b["zones"])
        n_cnt = sum(1 for z in b["zones"] if not z["resetable"])
        print(f"  {wname}: zones {n_all} (counted {n_cnt}), collectibles {len(b['items'])}", flush=True)
    with open(os.path.join(DATA, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"data_version": DATA_VERSION, "game": paths.game_stamp(a.game),
                   "extracted_at": int(time.time())}, f)
    if not a.no_plugin_export:
        export_to_plugin(a.game)
    print(f"Done in {time.time() - t0:.0f}s", flush=True)
    sys.stdout.flush()
    os._exit(0)  # UnityPy can crash during interpreter shutdown - the data is already written


if __name__ == "__main__":
    main()
