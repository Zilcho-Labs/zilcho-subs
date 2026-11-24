"""Embedded subtitles and lyrics: what's inside a media file, not next to it.

One light ffprobe call per file reads the subtitle tracks (MKV, MP4 and friends:
language, codec, forced flag) and the lyrics tags of songs (ID3 USLT in mp3, LYRICS
in flac/ogg/opus, the (c)lyr atom in m4a, WM/Lyrics in wma). Unlike pipeline.probe it
doesn't need a duration, so it works for any file ffprobe can open.

Results are kept in app data (the ``embedded`` table in queue.sqlite3), keyed by path
and valid while the file's size and modified time are unchanged. A failed probe is
kept too, with a short retry window, so one bad file isn't probed on every page
load. The media folder is only ever read. Nothing here probes a whole library on its
own: callers ask for specific files (check) or read what's already known (lookup).
"""
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .core import ALIASES

PROBE_TIMEOUT = 15      # seconds per file; a slow share must not hang a page
RETRY_SECONDS = 600     # a failed probe is tried again after this long
MAX_PATHS = 100         # most files one request may check
WORKERS = 4
BITMAP = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "dvdsub", "pgssub"}
_ENTRIES = "stream=index,codec_type,codec_name:stream_tags:stream_disposition=forced:format_tags"
_SYNCED = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")


def _language(tags):
    code = str((tags or {}).get("language", "") or (tags or {}).get("LANGUAGE", "")).strip().lower()
    code = ALIASES.get(code, code)
    return code if code and code not in ("und", "unk", "zxx", "mis", "mul") else "und"


def _lyric_value(tags):
    """The lyrics text if any tag looks like lyrics, else None."""
    for key, value in (tags or {}).items():
        name = str(key).lower().split("/")[-1].strip()
        if (name in ("lyrics", "unsyncedlyrics", "syncedlyrics", "unsynced lyrics", "uslt", "sylt") or name.startswith("lyrics-") or name.startswith("lyrics:")) and str(value).strip():
            return str(value)
    return None


def parse(data):
    """Turns ffprobe JSON into {"subs": [...], "lyrics": bool, "synced": bool, "error": None}."""
    subs, lyrics = [], None
    tag_sets = [(data.get("format") or {}).get("tags")]
    for stream in data.get("streams") or []:
        tag_sets.append(stream.get("tags"))
        if stream.get("codec_type") == "subtitle":
            codec = str(stream.get("codec_name") or "")
            subs.append({"lang": _language(stream.get("tags")), "codec": codec,
                         "forced": bool((stream.get("disposition") or {}).get("forced")), "bitmap": codec in BITMAP})
    for tags in tag_sets:
        lyrics = lyrics or _lyric_value(tags)
    return {"subs": subs, "lyrics": lyrics is not None, "synced": bool(lyrics and _SYNCED.search(lyrics)), "error": None}


def probe_embedded(path):
    """Reads one file with ffprobe. Never raises: a failure is {"error": "..."}."""
    failed = {"subs": [], "lyrics": False, "synced": False}
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", _ENTRIES, "-of", "json", str(path)],
                                capture_output=True, timeout=PROBE_TIMEOUT, check=True)
    except FileNotFoundError:
        return {**failed, "error": "ffprobe is not installed"}
    except subprocess.TimeoutExpired:
        return {**failed, "error": "ffprobe took too long"}
    except (subprocess.SubprocessError, OSError):
        return {**failed, "error": "ffprobe could not read this file"}
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError
        return parse(data)
    except (ValueError, TypeError, AttributeError):
        return {**failed, "error": "ffprobe gave an unreadable answer"}


def languages(result):
    """Known languages of the usable (not forced) subtitle tracks. Untagged ones don't count."""
    if not result or result.get("error"):
        return set()
    return {s["lang"] for s in result.get("subs", []) if not s.get("forced") and s["lang"] != "und"}


def bitmap_languages(result):
    """Languages whose embedded track is a picture (PGS, DVD), which can't be read as text."""
    if not result or result.get("error"):
        return set()
    return {s["lang"] for s in result.get("subs", []) if s.get("bitmap") and not s.get("forced") and s["lang"] != "und"}


def unknown_tracks(result):
    """How many usable subtitle tracks have no language tag."""
    if not result or result.get("error"):
        return 0
    return sum(1 for s in result.get("subs", []) if not s.get("forced") and s["lang"] == "und")


def has_lyrics(result):
    return bool(result and not result.get("error") and result.get("lyrics"))


# ----- the cache -----
def _stat(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def _valid(row, size, mtime, now):
    if not row or row["size"] != size or row["mtime_ns"] != mtime:
        return None
    try:
        result = json.loads(row["result"])
    except ValueError:
        return None
    if result.get("error") and now - row["checked"] > RETRY_SECONDS:
        return None
    return result


def lookup(store, root, relatives, now=None):
    """{relative path: result} for files whose answer is already known and still valid.
    Files not checked yet (or changed since) are left out. Nothing is probed."""
    now = time.time() if now is None else now
    root, found = Path(root).resolve(), {}
    relatives = list(dict.fromkeys(relatives))
    with store.db() as db:
        for start in range(0, len(relatives), 500):
            chunk = relatives[start:start + 500]
            rows = {r["path"]: r for r in db.execute("SELECT * FROM embedded WHERE path IN (" + ",".join("?" * len(chunk)) + ")", chunk)}
            for relative in chunk:
                if relative not in rows:
                    continue
                try:
                    size, mtime = _stat(root / relative)
                except OSError:
                    continue
                result = _valid(rows[relative], size, mtime, now)
                if result is not None:
                    found[relative] = result
    return found


def snapshot(store):
    """Every saved row, for a pass over a whole library (use known() per file)."""
    with store.db() as db:
        return {r["path"]: r for r in db.execute("SELECT * FROM embedded")}


def known(rows, relative, path, now=None):
    """The still-valid saved answer for one file from snapshot(), or None."""
    row = rows.get(relative)
    if row is None:
        return None
    try:
        size, mtime = _stat(path)
    except OSError:
        return None
    return _valid(row, size, mtime, time.time() if now is None else now)


def check(store, root, relative, force=False, now=None):
    """The answer for one file: from the cache, or one ffprobe call (saved). Never raises."""
    now = time.time() if now is None else now
    path = Path(root).resolve() / relative
    try:
        size, mtime = _stat(path)
    except OSError:
        return {"subs": [], "lyrics": False, "synced": False, "error": "the file is not readable"}
    if not force:
        with store.db() as db:
            known = _valid(db.execute("SELECT * FROM embedded WHERE path=?", (relative,)).fetchone(), size, mtime, now)
        if known is not None:
            return known
    result = probe_embedded(path)
    with store.db() as db:
        db.execute("INSERT OR REPLACE INTO embedded(path,size,mtime_ns,result,checked) VALUES(?,?,?,?,?)",
                   (relative, size, mtime, json.dumps(result), now))
    return result


def check_many(store, root, relatives, force=False):
    """Checks several files with a few worker threads. At most MAX_PATHS per call."""
    relatives = list(dict.fromkeys(relatives))[:MAX_PATHS]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return dict(zip(relatives, pool.map(lambda r: check(store, root, r, force), relatives)))
