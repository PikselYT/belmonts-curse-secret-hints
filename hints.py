"""Hints for missed secret zones and collectibles (Castlevania: Belmont's Curse).

Reads the game save (read-only), compares it with data extracted from the game
(extract_data.py) and writes an HTML report with three levels of hints:
  1) district, 2) room + position on the map, 3) where to enter from.

    python hints.py                      # default: hints unlock at 90% completion, writes report.html
    python hints.py --force              # skip the completion threshold
    python hints.py --level 2            # also print room and position in the console
    python hints.py --biome MainHall     # one area only
    python hints.py --items chest,health # only some collectible kinds ('none' = secret zones only)
"""
import argparse
import base64
import glob
import html
import io
import json
import os
import re
import sys
import webbrowser

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import paths

__version__ = "1.1.0"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DATA_VERSION = 3  # must match extract_data.py

COL_BG = (14, 11, 6)
COL_ROOM = (52, 40, 22)
COL_EDGE = (190, 150, 70)
COL_MISS = (235, 40, 40)
COL_ITEM = (240, 140, 20)


# ---------- loading ----------
def load_biomes(filter_text=""):
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, "World_Biome_*.json"))):
        with open(p, encoding="utf-8") as f:
            b = json.load(f)
        if filter_text and filter_text.lower() not in b["world"].lower():
            continue
        out.append(b)
    return out


def load_json(name):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_save(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    ps = d["playerSave"]
    w = ps["worldRoomExplorationStates"]
    states = {k: json.loads(v) for k, v in zip(w["m_keys"], w["m_values"])}
    ws = d.get("worldSaves", {})
    world_cur = {k: v.get("currentCompletionItemCount", 0) for k, v in zip(ws.get("m_keys", []), ws.get("m_values", []))}
    world_ents = {}
    for k, v in zip(ws.get("m_keys", []), ws.get("m_values", [])):
        ent = v.get("savableEntityStates", {})
        world_ents[k] = dict(zip(ent.get("m_keys", []), ent.get("m_values", [])))
    return ps, states, world_cur, world_ents


def data_warnings(game_dir):
    """Warnings about stale data (after a game or tool update)."""
    meta = load_json("meta.json")
    if not meta:
        return ["WARNING: data/meta.json is missing - cannot check whether the data is current. Re-run extract_data.py."]
    out = []
    if meta.get("data_version") != DATA_VERSION:
        out.append("WARNING: data/ was created by an older version of this tool. Re-run extract_data.py (run_extract.bat).")
    if game_dir:
        try:
            if meta.get("game") != paths.game_stamp(game_dir):
                out.append("WARNING: the game was updated after the last data extraction. Re-run extract_data.py (run_extract.bat).")
        except OSError:
            pass
    return out


# ---------- analysis ----------
def room_grid(r):
    """Open-tile grid of a room: [y from the bottom][x]."""
    cw, ch = r["cs"]
    g = np.zeros((r["h"] * ch, r["w"] * cw), bool)
    for i, col in enumerate(r["col"]):
        cx, cy = i % r["w"], i // r["w"]
        a = np.frombuffer(col.encode(), np.uint8).reshape(ch, cw) == 48
        g[cy * ch:(cy + 1) * ch, cx * cw:(cx + 1) * cw] = a
    return g


def _ranges(vals):
    vals = sorted(vals)
    out, start, prev = [], None, None
    for v in vals:
        if start is None:
            start = prev = v
        elif v == prev + 1:
            prev = v
        else:
            out.append((start, prev))
            start = prev = v
    if start is not None:
        out.append((start, prev))
    return ", ".join(f"{a}" if a == b else f"{a}–{b}" for a, b in out)


def entry_sides(r, z):
    g = room_grid(r)
    H, W = g.shape
    x0, y0, w, h = z["x"], z["y"], z["w"], z["h"]

    def op(x, y):
        return 0 <= x < W and 0 <= y < H and g[y, x]

    sides = []
    top = [x for x in range(x0, x0 + w) if op(x, y0 + h)]
    bottom = [x for x in range(x0, x0 + w) if op(x, y0 - 1)]
    left = [y for y in range(y0, y0 + h) if op(x0 - 1, y)]
    right = [y for y in range(y0, y0 + h) if op(x0 + w, y)]
    if top:
        sides.append(("T", f"x {_ranges(top)}"))
    if bottom:
        sides.append(("B", f"x {_ranges(bottom)}"))
    if left:
        sides.append(("L", f"y {_ranges(left)}"))
    if right:
        sides.append(("R", f"y {_ranges(right)}"))
    inside_open = int(sum(op(x, y) for x in range(x0, x0 + w) for y in range(y0, y0 + h)))
    return sides, inside_open


def where_in_room(r, z):
    W, H = r["w"] * r["cs"][0], r["h"] * r["cs"][1]
    fx, fy = (z["x"] + z["w"] / 2) / W, (z["y"] + z["h"] / 2) / H
    hi = 0 if fx < 0.33 else (1 if fx < 0.66 else 2)
    vi = 0 if fy < 0.33 else (1 if fy < 0.66 else 2)
    v = ["lower", "middle", "upper"][vi]
    h = ["on the left side of the room", "in the middle of the room", "on the right side of the room"][hi]
    return f"{v} part, {h}"


ITEM_LABELS = {
    "chest": "Chest",
    "health": "Health upgrade",
    "mana": "Mana upgrade",
    "atk_book": "Attack book",
    "def_book": "Defense book",
    "xp": "Experience source",
}


def item_collected(raw):
    """The game stores {"activated": true} for collected / opened / destroyed entities."""
    if raw is None:
        return False
    try:
        return bool(json.loads(raw).get("activated"))
    except (ValueError, AttributeError):
        return False


def analyse(biomes, states, world_cur, totals, world_ents, kinds):
    res = []
    tot_chunks = exp_chunks = 0
    for b in biomes:
        for scene, r in b["rooms"].items():
            st = states.get(f"{b['world']}_{scene}")
            tot_chunks += r["w"] * r["h"]
            if st and st.get("chunksExplorationState"):
                exp_chunks += sum(1 for v in st["chunksExplorationState"] if v == 3)
        zones = []
        for z in b["zones"]:
            if z["resetable"]:
                continue  # resetable zones are never saved as discovered - they do not count
            st = states.get(f"{b['world']}_{z['room']}")
            found = bool(st) and z["idx"] in st.get("discoveredSecretZones", [])
            zones.append({**z, "found": found, "visited": st is not None})
        w = b["world"]
        ents = world_ents.get(w, {})
        items = []
        for it in b.get("items", []):
            if it["kind"] not in kinds:
                continue
            raw = ents.get(it["guid"])
            items.append({**it, "found": item_collected(raw), "known": raw is not None,
                          "visited": f"{w}_{it['room']}" in states})
        comp = (world_cur.get(w, 0), totals[w]) if totals and w in totals else None
        res.append({"biome": b, "zones": zones, "items": items, "completion": comp})
    # the game's own counter: sum over all worlds (like its CompletionAggregator); falls back to an approximation
    if totals:
        cur = sum(min(world_cur.get(w, 0), n) for w, n in totals.items())
        progress, proxy = 100.0 * cur / sum(totals.values()), False
    else:
        progress, proxy = (100.0 * exp_chunks / tot_chunks if tot_chunks else 0.0), True
    return res, progress, proxy


# ---------- drawing ----------
def _erode(m):
    r = m.copy()
    r[1:, :] &= m[:-1, :]
    r[:-1, :] &= m[1:, :]
    r[:, 1:] &= m[:, :-1]
    r[:, :-1] &= m[:, 1:]
    return r


class BiomeCanvas:
    def __init__(self, b):
        self.b = b
        rs = list(b["rooms"].values())
        self.X0 = min(r["cx"] * r["cs"][0] for r in rs)
        self.Y0 = min(r["cy"] * r["cs"][1] for r in rs)
        X1 = max((r["cx"] + r["w"]) * r["cs"][0] for r in rs)
        Y1 = max((r["cy"] + r["h"]) * r["cs"][1] for r in rs)
        W, H = X1 - self.X0, Y1 - self.Y0
        open_ = np.zeros((H, W), bool)
        for r in rs:
            g = room_grid(r)
            x = r["cx"] * r["cs"][0] - self.X0
            y = r["cy"] * r["cs"][1] - self.Y0
            open_[y:y + g.shape[0], x:x + g.shape[1]] |= g
        self.S = int(max(2, min(8, 3000 // max(W, H))))
        S = self.S
        big = np.kron(open_, np.ones((S, S), bool))[::-1]
        edge = big & ~_erode(_erode(big))
        img = np.zeros(big.shape + (3,), np.uint8)
        img[:] = COL_BG
        img[big] = COL_ROOM
        img[edge] = COL_EDGE
        self.base = Image.fromarray(img)
        self.Hs = big.shape[0]

    def px(self, scene, x, y):
        r = self.b["rooms"][scene]
        return ((r["cx"] * r["cs"][0] + x - self.X0) * self.S,
                self.Hs - (r["cy"] * r["cs"][1] + y - self.Y0) * self.S)

    def zone_box(self, z, pad=0):
        x0, y1 = self.px(z["room"], z["x"], z["y"])
        x1, y0 = self.px(z["room"], z["x"] + z["w"], z["y"] + z["h"])
        return [x0 - pad, y0 - pad, x1 + pad, y1 + pad]

    def room_box(self, scene):
        r = self.b["rooms"][scene]
        x0, y1 = self.px(scene, 0, 0)
        x1, y0 = self.px(scene, r["w"] * r["cs"][0], r["h"] * r["cs"][1])
        return [x0, y0, x1, y1]


def _font(sz):
    for f in (r"C:\Windows\Fonts\arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, sz)
        except OSError:
            pass
    return ImageFont.load_default()


def _badge(d, x, y, text, sz, color=COL_MISS):
    f = _font(sz)
    w = d.textlength(text, font=f)
    r = max(sz * 0.8, w / 2 + 6)
    d.ellipse([x - r, y - r, x + r, y + r], fill=color + (255,), outline=(255, 255, 255, 255), width=3)
    d.text((x - w / 2, y - sz * 0.6), text, font=f, fill=(255, 255, 255, 255))


def item_xy(cv, it):
    return cv.px(it["room"], it["x"], it["y"])


def _diamond(d, x, y, r, fill, outline=(255, 255, 255, 255)):
    d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], fill=fill, outline=outline)


def overview_image(cv, missing, found, items=()):
    im = cv.base.convert("RGBA")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for z in found:
        d.rectangle(cv.zone_box(z, 2), outline=(80, 200, 120, 160), width=2)
    for z in missing:
        d.rectangle(cv.zone_box(z, 6), fill=COL_MISS + (110,), outline=(255, 70, 70, 255), width=4)
    for it in items:
        x, y = item_xy(cv, it)
        _diamond(d, x, y, max(7, cv.S * 2), COL_ITEM + (255,))
    im = Image.alpha_composite(im, ov)
    d = ImageDraw.Draw(im)
    sz = max(22, cv.S * 5)
    for n, z in enumerate(missing, 1):
        bx = cv.zone_box(z, 6)
        _badge(d, (bx[0] + bx[2]) / 2, bx[1] - sz, str(n), sz)
    for n, it in enumerate(items, 1):
        x, y = item_xy(cv, it)
        _badge(d, x, y - sz * 1.3, f"C{n}", int(sz * 0.8), COL_ITEM)
    return _fit(im.convert("RGB"), 1700, 1300)


def item_room_image(cv, it, level3):
    scene = it["room"]
    r = cv.b["rooms"][scene]
    rb = cv.room_box(scene)
    mx, my = r["cs"][0] * cv.S, r["cs"][1] * cv.S
    box = [max(0, int(rb[0] - mx)), max(0, int(rb[1] - my)),
           min(cv.base.width, int(rb[2] + mx)), min(cv.base.height, int(rb[3] + my))]
    im = cv.base.convert("RGBA")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle(rb, outline=(120, 160, 255, 200), width=2)
    x, y = item_xy(cv, it)
    if level3:  # exact pin
        R = max(6, cv.S * 1.5)
        d.line([x - 4 * R, y, x + 4 * R, y], fill=(255, 255, 255, 255), width=3)
        d.line([x, y - 4 * R, x, y + 4 * R], fill=(255, 255, 255, 255), width=3)
        _diamond(d, x, y, R * 1.6, COL_ITEM + (255,))
    else:  # fuzzy area: about 6 tiles around
        R = 6 * cv.S
        d.ellipse([x - R, y - R, x + R, y + R], fill=COL_ITEM + (90,), outline=COL_ITEM + (255,), width=4)
    im = Image.alpha_composite(im, ov).convert("RGB").crop(box)
    return _fit(im, 1000, 800, upscale=True)


def room_image(cv, z, level3):
    scene = z["room"]
    r = cv.b["rooms"][scene]
    rb = cv.room_box(scene)
    mx, my = r["cs"][0] * cv.S, r["cs"][1] * cv.S
    box = [max(0, int(rb[0] - mx)), max(0, int(rb[1] - my)),
           min(cv.base.width, int(rb[2] + mx)), min(cv.base.height, int(rb[3] + my))]
    im = cv.base.convert("RGBA")
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle(rb, outline=(120, 160, 255, 200), width=2)
    zb = cv.zone_box(z, 3)
    d.rectangle(zb, fill=COL_MISS + (90 if level3 else 130,), outline=(255, 70, 70, 255), width=4)
    if level3:
        g = room_grid(r)
        for yy in range(z["y"], z["y"] + z["h"]):
            for xx in range(z["x"], z["x"] + z["w"]):
                if 0 <= yy < g.shape[0] and 0 <= xx < g.shape[1] and g[yy, xx]:
                    x0, y1 = cv.px(scene, xx, yy)
                    d.rectangle([x0, y1 - cv.S, x0 + cv.S, y1], fill=(255, 230, 0, 150))
        sides, _ = entry_sides(r, z)
        cx, cy = (zb[0] + zb[2]) / 2, (zb[1] + zb[3]) / 2
        L = 7 * cv.S
        for k, _ in sides:
            if k == "T":
                d.line([cx, zb[1] - L, cx, zb[1] - 4], fill=(255, 255, 255, 255), width=5)
            elif k == "B":
                d.line([cx, zb[3] + L, cx, zb[3] + 4], fill=(255, 255, 255, 255), width=5)
            elif k == "L":
                d.line([zb[0] - L, cy, zb[0] - 4, cy], fill=(255, 255, 255, 255), width=5)
            elif k == "R":
                d.line([zb[2] + L, cy, zb[2] + 4, cy], fill=(255, 255, 255, 255), width=5)
    im = Image.alpha_composite(im, ov).convert("RGB").crop(box)
    return _fit(im, 1000, 800, upscale=True)


def _fit(im, mw, mh, upscale=False):
    k = min(mw / im.width, mh / im.height)
    if k < 1 or upscale:
        k = min(k, 3.0)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
    return im


def b64(im):
    buf = io.BytesIO()
    # flat colors: a small palette makes the PNG several times smaller
    im.convert("P", palette=Image.ADAPTIVE, colors=64).save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------- texts ----------
def clean_name(s):
    return "".join(ch for ch in (s or "") if ch.isprintable() and ch not in "\u200b\u200c\u200d\ufeff").replace("  ", " ").strip()


def room_label(scene):
    s = re.sub(r"_uid\d+$", "", scene)
    if "MVP" in s:
        s = re.sub(r"^(\w+?)_(MVP_)?", "", s, count=1)
    return s.replace("_", " ").strip()


def zone_texts(b, z):
    r = b["rooms"][z["room"]]
    dist = clean_name(b["room_district"].get(z["room"])) or "unknown district"
    sides, inside_open = entry_sides(r, z)
    if sides:
        names = {"T": "the top", "B": "the bottom", "L": "the left side", "R": "the right side"}
        entry = "Most likely entrance from: " + "; ".join(f"{names[k]} ({c})" for k, c in sides) + "."
    else:
        entry = "No edge of the zone touches open space - walk into this wall from any side."
    if inside_open:
        entry += f" Inside there are about {inside_open} empty tiles (yellow in the picture)."
    entry += f" Zone size: {z['w']}x{z['h']} tiles, from the room's bottom-left corner: x={z['x']}, y={z['y']}."
    return {
        "dist": dist,
        "h1": f"District: {dist}{'' if z['visited'] else ' (you have not visited this room yet)'}.",
        "h2": f"Room \"{room_label(z['room'])}\", the zone is in the {where_in_room(r, z)}.",
        "h3": entry,
    }


def item_texts(b, it, mz=()):
    """mz: the missing secret zones in listed order, to tell when an item sits inside one."""
    r = b["rooms"][it["room"]]
    dist = clean_name(b["room_district"].get(it["room"])) or "unknown district"
    label = ITEM_LABELS[it["kind"]]
    note = "" if it["visited"] else " (you have not visited this room yet)"
    pos = where_in_room(r, {"x": it["x"], "y": it["y"], "w": 0, "h": 0})
    h3 = (f"Exact spot: about x={it['x']:.0f}, y={it['y']:.0f} tiles from the room's bottom-left corner "
          "(the pin in the picture).")
    if it["visited"] and not it["known"]:
        h3 += " The game has no record of this one yet."
    h2 = f"Room \"{room_label(it['room'])}\", in the {pos}."
    inside = [n for n, z in enumerate(mz, 1)
              if z["room"] == it["room"] and z["x"] <= it["x"] < z["x"] + z["w"] and z["y"] <= it["y"] < z["y"] + z["h"]]
    if inside:
        h2 += f" It is inside secret zone #{inside[0]} from the list above."
    return {
        "dist": dist, "label": label,
        "h1": f"{label} in district {dist}{note}.",
        "h2": h2,
        "h3": h3,
    }


def sort_key(b, z):
    return (clean_name(b["room_district"].get(z["room"])), z["room"], z["idx"])


def item_sort_key(b, it):
    return (clean_name(b["room_district"].get(it["room"])), it["room"], it["kind"], it["x"], it["y"])


# ---------- report ----------
CSS = """
:root{--bg:#14110b;--fg:#e8dcc0;--mut:#a89a78;--card:#1e1a12;--acc:#c9a24a;--red:#e84c3d;--item:#f08c14}
@media (prefers-color-scheme: light){:root{--bg:#f4efe3;--fg:#2b2418;--mut:#6b5f45;--card:#fffaf0;--acc:#8a6a1f;--red:#c0392b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{margin:0 0 4px;font-size:1.6rem}h2{margin:36px 0 6px;color:var(--acc);font-size:1.3rem}
.sub{color:var(--mut);margin:0 0 18px}.bar{height:8px;background:#0003;border-radius:4px;overflow:hidden;margin:8px 0 12px}
.bar i{display:block;height:100%;background:var(--acc)}
.card{background:var(--card);border:1px solid #8884;border-radius:10px;padding:14px 16px;margin:12px 0}
.card h3{margin:0 0 6px;font-size:1.05rem}.n{display:inline-block;min-width:26px;height:26px;border-radius:13px;background:var(--red);color:#fff;text-align:center;font-weight:700;line-height:26px;margin-right:8px}
details{margin:8px 0 0;border-top:1px dashed #8886;padding-top:8px}summary{cursor:pointer;color:var(--acc);font-weight:600}
img{max-width:100%;height:auto;border-radius:6px;margin-top:8px;display:block}.warn{color:var(--mut);font-size:.9rem}
.n.c{background:var(--item);border-radius:13px;padding:0 6px}.sec{margin:18px 0 4px;font-size:1.05rem}
.ok{color:#5ac879}.alert{background:#e84c3d22;border:1px solid var(--red);border-radius:8px;padding:8px 12px;margin:8px 0}
"""


def _card(label, css, tx, img2, img3, hint3_title, warn3):
    return (f"<div class='card'><h3><span class='n {css}'>{html.escape(label)}</span>{html.escape(tx['h1'])}</h3>"
            f"<details><summary>Hint 2 - room and position</summary><p>{html.escape(tx['h2'])}</p>"
            f"<img alt='room' src='{img2}'>"
            f"<details><summary>{html.escape(hint3_title)}</summary><p>{html.escape(tx['h3'])}</p>"
            f"<img alt='detail' src='{img3}'><p class='warn'>{html.escape(warn3)}</p></details></details></div>")


def build_report(sections, progress, proxy, save_info, warnings, out_path):
    prog = f"Map exploration: {progress:.1f}%." if proxy else f"Game completion: {progress:.1f}%."
    sub = f"State from the save: {save_info}. {prog} Hints get more specific as you expand them - you do not have to read them all."
    parts = ["<!doctype html><html lang='en'><meta charset='utf-8'>"
             "<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Secret hints</title><style>{CSS}</style><body><main>"
             "<h1>Missed secrets and collectibles</h1>"
             f"<p class='sub'>{html.escape(sub)}</p>"]
    for w in warnings:
        parts.append(f"<p class='alert'>{html.escape(w)}</p>")
    for s in sections:
        b, zones, items = s["biome"], s["zones"], s["items"]
        if not zones and not items:
            continue
        mz = sorted((z for z in zones if not z["found"]), key=lambda z: sort_key(b, z))
        fz = [z for z in zones if z["found"]]
        mi = sorted((i for i in items if not i["found"]), key=lambda i: item_sort_key(b, i))
        total = len(zones) + len(items)
        done = len(fz) + (len(items) - len(mi))
        pct = 100 * done / total if total else 100
        heading = f"{b['title']} - {len(fz)}/{len(zones)} secrets"
        if items:
            heading += f", {len(items) - len(mi)}/{len(items)} collectibles"
        parts.append(f"<h2>{html.escape(heading)}</h2><div class='bar'><i style='width:{pct:.0f}%'></i></div>")
        comp_txt = ""
        if s["completion"]:
            c, tot = s["completion"]
            comp_txt = f"In-game completion: {c}/{tot} ({100 * c / tot if tot else 100:.0f}%)."
        if not mz and not mi:
            extra = ""
            if s["completion"] and s["completion"][0] < s["completion"][1]:
                extra = (f" {s['completion'][1] - s['completion'][0]} more completion item(s) are still missing - "
                         "they are not secret zones or the collectibles tracked here (e.g. unexplored spots).")
            parts.append("<p class='ok'>Everything tracked here is found!</p>"
                         f"<p class='warn'>{html.escape(comp_txt + extra)}</p>")
            continue
        if comp_txt:
            parts.append(f"<p class='warn'>{html.escape(comp_txt)}</p>")
        cv = BiomeCanvas(b)
        parts.append("<details><summary>Map of the whole area with numbers</summary>"
                     f"<img alt='map' src='{b64(overview_image(cv, mz, fz, mi))}'></details>")
        if mz:
            parts.append("<h3 class='sec'>Secret zones</h3>")
            for n, z in enumerate(mz, 1):
                parts.append(_card(str(n), "", zone_texts(b, z), b64(room_image(cv, z, False)), b64(room_image(cv, z, True)),
                                   "Hint 3 - how to get in",
                                   "The entrance is derived from room geometry and may be off by 1-2 tiles. "
                                   "Red = zone, yellow = empty tiles inside, white lines = open edges."))
        elif zones:
            parts.append("<p class='ok'>All secret zones found.</p>")
        if mi:
            parts.append("<h3 class='sec'>Collectibles</h3>")
            for n, it in enumerate(mi, 1):
                parts.append(_card(f"C{n}", "c", item_texts(b, it, mz), b64(item_room_image(cv, it, False)),
                                   b64(item_room_image(cv, it, True)), "Hint 3 - exact spot",
                                   "Position comes from the room data and can be off by a tile or two. "
                                   "Orange = the collectible."))
        elif items:
            parts.append("<p class='ok'>All tracked collectibles found.</p>")
    parts.append("<p class='warn'>The game only saves at save points, so this report shows the state of the last save. "
                 "'Resettable' secret zones are never recorded as discovered by the game, so they are not counted here.</p>"
                 "</main></body></html>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--save", help="save file (default: newest BuildSave_*.sav, auto-detected)")
    ap.add_argument("--min-progress", type=float, default=90.0, help="completion %% needed to unlock hints (default 90)")
    ap.add_argument("--force", action="store_true", help="ignore the completion threshold")
    ap.add_argument("--level", type=int, default=1, choices=(1, 2, 3), help="detail level printed in the console")
    ap.add_argument("--biome", default="")
    ap.add_argument("--items", default=",".join(ITEM_LABELS),
                    help="collectible kinds to track, comma separated, or 'none' (default: all: " + ",".join(ITEM_LABELS) + ")")
    ap.add_argument("--out", help="report file (default: report.html, or report_<biome>.html when --biome is used)")
    ap.add_argument("--json", help="write the list of missing secrets and collectibles as JSON")
    ap.add_argument("--open", action="store_true", help="open the report in the browser")
    a = ap.parse_args()
    if not a.out:  # a filtered run must never overwrite the full report
        suffix = "_" + re.sub(r"[^A-Za-z0-9]+", "", a.biome) if a.biome else ""
        a.out = os.path.join(HERE, f"report{suffix}.html")

    kinds = set() if a.items.strip().lower() == "none" else {k.strip() for k in a.items.split(",") if k.strip()}
    unknown = kinds - set(ITEM_LABELS)
    if unknown:
        sys.exit(f"Unknown --items value(s): {', '.join(sorted(unknown))}. Valid: {', '.join(ITEM_LABELS)}")
    biomes = load_biomes(a.biome)
    if not biomes:
        sys.exit("No data in data/. Run first: python extract_data.py")
    save_path = paths.find_save_file(a.save)
    if not save_path or not os.path.isfile(save_path):
        sys.exit("Save file not found. Use --save, BC_SAVE_DIR or save_dir in config.json.")
    ps, states, world_cur, world_ents = load_save(save_path)
    totals = load_json("completion.json")
    sections, progress, proxy = analyse(biomes, states, world_cur, totals, world_ents, kinds)
    warnings = data_warnings(paths.find_game_dir())
    info = f'{ps.get("saveDate", "?")}, level {ps.get("level", "?")}, playtime {ps.get("playTimeInSeconds", 0) / 3600:.1f} h'

    print(f"Save: {save_path}")
    print(info)
    for w in warnings:
        print(w)
    if proxy:
        print(f"Map exploration: {progress:.1f}% (approximation, counter data missing; threshold {a.min_progress:.0f}%)")
    else:
        print(f"Game completion: {progress:.1f}% (in-game counter; hint threshold {a.min_progress:.0f}%)")
    for s in sections:
        z, it = s["zones"], s["items"]
        if not z and not it:
            continue
        line = f"  {s['biome']['title']:<20} secrets {sum(1 for q in z if q['found'])}/{len(z)}"
        if it:
            line += f"   collectibles {sum(1 for q in it if q['found'])}/{len(it)}"
        if s["completion"]:
            c, tot = s["completion"]
            line += f"   completion {c}/{tot} ({100 * c / tot if tot else 100:.0f}%)"
        print(line)
    if progress < a.min_progress and not a.force:
        print(f"\nHints locked: completion {progress:.1f}% < {a.min_progress:.0f}%. Explore more of the world or use --force.")
        return

    print()
    miss_all = []
    for s in sections:
        b = s["biome"]
        mz = sorted((z for z in s["zones"] if not z["found"]), key=lambda z: sort_key(b, z))
        mi = sorted((i for i in s["items"] if not i["found"]), key=lambda i: item_sort_key(b, i))
        for z in mz:
            tx = zone_texts(b, z)
            miss_all.append({"type": "secret_zone", "world": b["world"], "room": z["room"], "idx": z["idx"],
                             "district": tx["dist"], "x": z["x"], "y": z["y"], "w": z["w"], "h": z["h"]})
        for i in mi:
            tx = item_texts(b, i, mz)
            miss_all.append({"type": i["kind"], "world": b["world"], "room": i["room"], "guid": i["guid"],
                             "district": tx["dist"], "x": i["x"], "y": i["y"]})
        if not mz and not mi:
            continue
        print(f"{b['title']}: {len(mz)} secret zone(s), {len(mi)} collectible(s) missing")
        for z in mz:
            tx = zone_texts(b, z)
            print(f"   - {tx['h1']}")
            if a.level >= 2:
                print(f"       {tx['h2']}")
            if a.level >= 3:
                print(f"       {tx['h3']}")
        for i in mi:
            tx = item_texts(b, i, mz)
            print(f"   * {tx['h1']}")
            if a.level >= 2:
                print(f"       {tx['h2']}")
            if a.level >= 3:
                print(f"       {tx['h3']}")
    build_report(sections, progress, proxy, info, warnings, a.out)
    print(f"\nReport: {a.out}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(miss_all, f, ensure_ascii=False, indent=1)
    if a.open:
        webbrowser.open("file:///" + a.out.replace("\\", "/"))


if __name__ == "__main__":
    main()
