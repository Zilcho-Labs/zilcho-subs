"""Search file names across a whole library (the Library page's search box).

Read-only: it walks the library folders with the same rules as the coverage count
(no hidden or symlinked folders, at most 100,000 media files), keeps the list of
names for a minute, and ranks matches with the same fuzzy rules as the list pages
(zilcho_subs.status.fuzzy_rank). It never writes to the media folder and never
returns a file outside the chosen library.
"""
import json
import os
import threading
import time
from pathlib import Path

from . import embedded
from .core import VIDEO, library_folders
from .coverage import MAX_FILES, _walk
from .status import fuzzy_rank, song_status, video_status

CACHE_SECONDS = 60
MAX_CACHED = 8        # one saved name list per library
MAX_RESULTS = 1000    # most results one request returns
_cache = {}
_lock = threading.Lock()


def _names(store, root, library):
    """(files, truncated) for the library: (relative path, name, kind) for every video and song."""
    settings = store.settings()
    folders = library_folders(settings, library)   # unknown library: KeyError, nothing walked
    key = (str(store.config), str(root), json.dumps([library or None, folders]))
    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached["at"] <= CACHE_SECONDS:
            return cached["files"], cached["truncated"]
    root = Path(root).resolve()
    files, truncated = [], False
    for count, (_folder, _names_here, path) in enumerate(_walk(root, folders)):
        if count >= MAX_FILES:
            truncated = True
            break
        files.append((path.relative_to(root).as_posix(), path.name, "video" if path.suffix.lower() in VIDEO else "song"))
    with _lock:
        for old in [k for k, v in _cache.items() if time.time() - v["at"] > CACHE_SECONDS]:
            del _cache[old]
        while len(_cache) >= MAX_CACHED:
            del _cache[next(iter(_cache))]
        _cache[key] = {"at": time.time(), "files": files, "truncated": truncated}
    return files, truncated


def search(store, root, query, kind="", library=None, limit=200, offset=0):
    """Files in the library whose names match, best match first.

    kind: "video", "song" or "" for both. Returns the same entry shape as the
    folder lists (plus kind and folder) with the status of each shown file."""
    if kind not in ("", "video", "song"):
        raise ValueError("Choose video or song")
    limit = max(1, min(int(limit), MAX_RESULTS))
    offset = max(0, int(offset))
    query = " ".join(str(query).split())[:200]
    files, truncated = _names(store, root, library)
    ranked = []
    if query:
        for relative, name, file_kind in files:
            if kind and file_kind != kind:
                continue
            rank = fuzzy_rank(query, name, relative)
            if rank is not None:
                ranked.append((rank, name.casefold(), relative, name, file_kind))
        ranked.sort(key=lambda r: r[:3])
    root = Path(root).resolve()
    settings = store.settings()
    targets = settings.target_languages
    page = ranked[offset:offset + limit]
    inside = embedded.lookup(store, root, [r[2] for r in page])   # saved answers only; nothing is probed here
    listed, entries = {}, []
    for _rank, _key, relative, name, file_kind in page:
        parent = Path(relative).parent
        try:
            if parent not in listed:
                listed[parent] = sorted(os.listdir(root / parent))
            names = listed[parent]
            stem = Path(name).stem
            status = (video_status(root / parent, stem, names, targets, inside.get(relative), settings.skip_existing) if file_kind == "video"
                      else song_status(root / parent, stem, names, inside.get(relative)))
        except OSError:
            continue  # moved or deleted since the list was made
        entries.append({"path": relative, "name": name, "directory": False, "subtitle": False, "kind": file_kind,
                        "folder": "" if str(parent) == "." else parent.as_posix(), "status": status})
    return {"q": query, "kind": kind, "library": library or None, "entries": entries, "total": len(ranked),
            "offset": offset, "limit": limit, "truncated": truncated, "max_files": MAX_FILES, "targets": list(targets)}
