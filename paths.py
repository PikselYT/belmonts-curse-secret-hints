"""Path detection: game install and save folder (Windows/Steam, best-effort Linux/Proton).

Order: explicit argument -> environment variables BC_GAME_DIR / BC_SAVE_DIR -> config.json next to the
scripts -> auto-detection through the Steam libraries.
"""
import glob
import json
import os
import re
import sys

GAME_NAME = "Castlevania Belmont's Curse"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def steam_roots():
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(winreg.QueryValueEx(k, name)[0])
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(os.path.join(os.environ[env], "Steam"))
    else:
        home = os.path.expanduser("~")
        roots += [os.path.join(home, ".steam", "steam"),
                  os.path.join(home, ".local", "share", "Steam"),
                  os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam")]
    return [os.path.normpath(r) for r in dict.fromkeys(roots) if r and os.path.isdir(r)]


def steam_libraries():
    """Steam library folders (each contains a steamapps subfolder)."""
    libs = []
    for root in steam_roots():
        libs.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            text = open(vdf, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libs.append(m.group(1).replace("\\\\", "\\"))
    seen, out = set(), []
    for lib in libs:
        lib = os.path.normpath(lib)
        if os.path.isdir(lib) and os.path.normcase(lib) not in seen:
            seen.add(os.path.normcase(lib))
            out.append(lib)
    return out


def _is_game_dir(p):
    return bool(p) and os.path.isfile(os.path.join(p, "GameAssembly.dll"))


def find_game_dir(explicit=None):
    for cand in (explicit, os.environ.get("BC_GAME_DIR"), load_config().get("game_dir")):
        if cand:
            return cand if _is_game_dir(cand) else None
    for lib in steam_libraries():
        p = os.path.join(lib, "steamapps", "common", GAME_NAME)
        if _is_game_dir(p):
            return p
    return None


def save_dirs(explicit=None):
    """Candidate save folders (the first one containing save files wins)."""
    cands = [explicit, os.environ.get("BC_SAVE_DIR"), load_config().get("save_dir")]
    if sys.platform == "win32":
        cands.append(os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "KONAMI", GAME_NAME))
    else:  # Proton: prefix gry w compatdata
        for lib in steam_libraries():
            cands += glob.glob(os.path.join(lib, "steamapps", "compatdata", "*", "pfx", "drive_c", "users",
                                            "steamuser", "AppData", "LocalLow", "KONAMI", GAME_NAME))
    return [c for c in cands if c]


def find_save_file(explicit_file=None, explicit_dir=None):
    if explicit_file:
        return explicit_file
    files = []
    for d in save_dirs(explicit_dir):
        files += glob.glob(os.path.join(d, "BuildSave_*.sav"))
    return max(files, key=os.path.getmtime) if files else None


def game_stamp(game_dir):
    st = os.stat(os.path.join(game_dir, "GameAssembly.dll"))
    return {"size": st.st_size, "mtime": int(st.st_mtime)}
