"""Subtitle & lyrics coverage: what share of the library already has subtitles in
each language, lyrics, and word-by-word ("enhanced") lyrics.

Read-only: it walks the Monitor library folders (Settings: folders to watch) and
looks at file names next to each video and song, using the same rules as the
list pages (zilcho_subs.status). It only reads the start of a song's .lrc to
spot word timing. It never writes to the media folder.

"Generate missing" queues ordinary jobs through Store.enqueue, exactly like the
Queue buttons on the other pages: regenerate stays off for subtitles, so a file
that's already there is never replaced, and the normal pipeline checks again
when the job runs.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

from . import embedded
from .core import ACTIVE, AUDIO, VIDEO, library_folders, media_path
from .status import song_status, video_status

MAX_FILES = 100_000   # stop counting after this many media files (truncated=True)
MAX_QUEUE = 5_000     # at most this many jobs per Generate click (the queue holds 500 anyway)
CACHE_SECONDS = 60
MAX_CACHED = 16       # one saved result per library view
_cache = {}
_lock = threading.Lock()


def _walk(root, folders):
    """Yields (folder, names, path) for every video and song in the library folders."""
    seen = set()
    for relative in folders:
        try:
            folder = media_path(root, relative)
        except ValueError:
            continue
        if not folder.is_dir():
            continue
        for parent, dirs, files in os.walk(folder, followlinks=False):
            parent = Path(parent)
            if parent in seen:
                dirs[:] = []
                continue  # folders listed twice (e.g. "tv" and "tv/Show") count once
            seen.add(parent)
            dirs[:] = [d for d in sorted(dirs) if not d.startswith(".") and not (parent / d).is_symlink()]
            names = sorted(files)
            for name in names:
                if name.startswith("."):
                    continue
                suffix = os.path.splitext(name)[1].lower()
                if suffix in VIDEO or suffix in AUDIO:
                    path = parent / name
                    if not path.is_symlink():
                        yield parent, names, path


def scan(store, root, library=None):
    """Counts coverage now. Returns the public summary plus private missing lists.
    library: one library's id, or None for every library."""
    settings = store.settings()
    folders = library_folders(settings, library)
    whole = any(not f for f in folders) or any(l.whole_folder for l in settings.libraries if library in (None, "", l.id))
    root = Path(root).resolve()
    targets = list(settings.target_languages)
    videos, songs = [], []
    have = {t: 0 for t in targets}
    truncated = False
    # What's inside files comes from the saved answers only: counting never probes.
    # A file nobody has checked inside yet counts by its files alone, and a failed
    # check does too, so a bad file is never skipped.
    saved, now = embedded.snapshot(store), time.time()
    inside = {"checked": 0, "unchecked": 0, "failed": 0}
    for count, (folder, names, path) in enumerate(_walk(root, folders)):
        if count >= MAX_FILES:
            truncated = True
            break
        relative = path.relative_to(root).as_posix()
        answer = embedded.known(saved, relative, path, now)
        inside["unchecked" if answer is None else "failed" if answer.get("error") else "checked"] += 1
        if path.suffix.lower() in VIDEO:
            subs = video_status(folder, path.stem, names, [], answer, settings.skip_existing)["subs"]
            videos.append((relative, set(subs)))
            for language in subs:
                have[language] = have.get(language, 0) + 1
        else:
            status = song_status(folder, path.stem, names, answer)
            songs.append((relative, status["lyrics"], status["enhanced"], path.stem + ".lrc" in names))

    def share(n, total):
        return {"have": n, "missing": total - n, "total": total, "percent": round(100 * n / total, 1) if total else None}

    order = targets + sorted((l for l in have if l not in targets), key=lambda l: (-have[l], l))
    subtitles = [{"language": l, "target": l in targets, **share(have[l], len(videos))} for l in order]
    return {
        "checked": time.time(),
        "library": library or None,
        "folders": folders,
        "whole_folder": whole,      # the UI warns: this walks the user's main media folder
        "max_files": MAX_FILES,
        "truncated": truncated,
        "videos": len(videos),
        "songs": len(songs),
        "targets": targets,
        "inside": {**inside, "total": inside["checked"] + inside["unchecked"] + inside["failed"], "used": settings.skip_existing},
        "subtitles": subtitles,
        "lyrics": share(sum(1 for s in songs if s[1]), len(songs)),
        "enhanced": share(sum(1 for s in songs if s[2]), len(songs)),
        "_videos": videos,
        "_songs": songs,
    }


def report(store, root, refresh=False, library=None):
    """The coverage summary (cached for a minute; refresh=True counts again)."""
    settings = store.settings()
    folders = library_folders(settings, library)   # unknown library: KeyError, nothing counted
    key = (str(store.config), str(root), json.dumps([library or None, folders, settings.target_languages, settings.skip_existing]))
    with _lock:
        cached = _cache.get(key)
        if refresh or not cached or time.time() - cached["checked"] > CACHE_SECONDS:
            cached = scan(store, root, library)
            for old in [k for k, v in _cache.items() if time.time() - v["checked"] > CACHE_SECONDS]:
                del _cache[old]
            while len(_cache) >= MAX_CACHED:
                del _cache[next(iter(_cache))]
            _cache[key] = cached
    return {k: v for k, v in cached.items() if not k.startswith("_")}, cached


_inside_lock = threading.Lock()
_inside = {"running": False, "done": 0, "total": 0, "failed": 0, "library": None, "error": ""}


def inside_progress():
    with _inside_lock:
        return dict(_inside)


def _inside_pass(store, root, library):
    try:
        folders = library_folders(store.settings(), library)
        root = Path(root).resolve()
        saved, now = embedded.snapshot(store), time.time()
        todo = []
        for count, (_folder, _names, path) in enumerate(_walk(root, folders)):
            if count >= MAX_FILES:
                break
            relative = path.relative_to(root).as_posix()
            if embedded.known(saved, relative, path, now) is None:   # failed ones wait for their retry window
                todo.append(relative)
        with _inside_lock:
            _inside["total"] = len(todo)
        for start in range(0, len(todo), embedded.MAX_PATHS):
            results = embedded.check_many(store, root, todo[start:start + embedded.MAX_PATHS])
            with _inside_lock:
                _inside["done"] += len(results)
                _inside["failed"] += sum(1 for r in results.values() if r.get("error"))
    except Exception as error:  # a bad share must not leave the button stuck
        with _inside_lock:
            _inside["error"] = str(error)[:200] or "The check stopped"
    finally:
        with _inside_lock:
            _inside["running"] = False
        with _lock:
            _cache.clear()      # coverage counts again with the new answers


def start_inside_check(store, root, library=None):
    """Starts a background pass that looks inside every file not yet checked (progress:
    inside_progress). Capped at MAX_FILES; a second click while it runs does nothing."""
    library_folders(store.settings(), library)   # unknown library: KeyError
    with _inside_lock:
        if _inside["running"]:
            return dict(_inside)
        _inside.update(running=True, done=0, total=0, failed=0, library=library or None, error="")
        snapshot = dict(_inside)
    threading.Thread(target=_inside_pass, args=(store, root, library), name="zilcho-inside-check", daemon=True).start()
    return snapshot


def _active(store, kind):
    """Paths with an active job of this kind, and the spec each one was queued with."""
    marks = ",".join("?" * len(ACTIVE))
    result = {}
    with store.db() as db:
        for row in db.execute(f"SELECT spec FROM jobs WHERE kind=? AND status IN ({marks})", (kind, *ACTIVE)):
            spec = json.loads(row[0])
            result.setdefault(spec.get("path"), []).append(spec)
    return result


def generate(store, root, what, language=None, library=None):
    """Queues one ordinary job per file that's missing `what`.

    what: "subtitles" (needs language), "lyrics" or "enhanced".
    Files that already have a waiting or running job that will make it are left
    alone. Stops at MAX_QUEUE or when the queue is full.
    """
    _summary, data = report(store, root, refresh=True, library=library)
    settings = store.settings().model_dump()
    jobs = []
    if what == "subtitles":
        if not language or not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", language):
            raise ValueError("Choose a subtitle language")
        active = _active(store, "subtitle")
        spec_settings = {**settings, "target_languages": [language]}
        for path, langs in data["_videos"]:
            if language in langs:
                continue
            if any(language in s.get("settings", {}).get("target_languages", []) for s in active.get(path, [])):
                jobs.append(None)
                continue
            jobs.append(("subtitle", {"path": path, "audio_stream": None, "regenerate": False, "settings": spec_settings}))
    elif what in ("lyrics", "enhanced"):
        active = _active(store, "lyrics")
        for path, lyrics, enhanced, has_lrc in data["_songs"]:
            if (lyrics if what == "lyrics" else enhanced):
                continue
            pending = active.get(path, [])
            if what == "lyrics":
                if pending:
                    jobs.append(None)
                    continue
                jobs.append(("lyrics", {"path": path, "regenerate": False, "settings": settings}))
            else:
                if any(s.get("settings", {}).get("lyrics_words") for s in pending):
                    jobs.append(None)
                    continue
                # Word-by-word lyrics go in Song.words.lrc. A song that already has a
                # Song.lrc needs "make again", or the job stops at "already has lyrics";
                # publishing never overwrites a file Zilcho Subs didn't write (it saves
                # Song.AI.lrc beside it instead).
                jobs.append(("lyrics", {"path": path, "regenerate": has_lrc, "settings": {**settings, "lyrics_words": True}}))
    else:
        raise ValueError("Choose subtitles, lyrics or enhanced")

    queued, already, errors, full = 0, 0, [], False
    for job in jobs:
        if job is None:
            already += 1
            continue
        if queued >= MAX_QUEUE:
            break
        kind, spec = job
        try:
            store.enqueue(kind, spec)
            queued += 1
        except ValueError as error:
            if "Queue is full" in str(error):
                full = True
                break
            errors.append({"path": spec["path"], "error": str(error)})
    wanted = len(jobs) - already
    return {"queued": queued, "already_queued": already, "missing": len(jobs),
            "left": max(0, wanted - queued - len(errors)), "queue_full": full, "errors": errors[:50]}
