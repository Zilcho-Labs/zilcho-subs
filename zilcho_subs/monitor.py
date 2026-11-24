"""Monitor library: watches the media folder, audits each video and song for
missing subtitles or lyrics, and queues the missing ones as ordinary jobs.

- It only reads the media folder. Everything it records goes in app data
  (``audit`` and ``monitor_state`` in queue.sqlite3, plus a cursor file), so it
  works in a read-only container.
- It never sets ``regenerate``, so the jobs it queues can't replace or version
  an existing file. The normal pipeline still checks again when the job runs.
- Jobs go through the normal queue, so the one-job-at-a-time worker, pausing,
  processing hours and job controls all apply.
- It runs in its own thread, so scanning carries on while a long job runs.
"""
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from .core import ACTIVE, ALIASES, AUDIO, VIDEO, allowed_hour, in_folders, library_folders, media_path
from . import embedded, jellyfin, mediaserver

SUBTITLE_SUFFIXES = (".srt", ".ass", ".ssa", ".vtt", ".sub")
KINDS = ("video", "song")
STATES = ("waiting", "missing", "queued", "complete", "failed")
FINISHED = ("completed", "failed", "skipped", "cancelled")
_VIDEO_KEYS = ("model", "source_language", "target_languages", "direct_english", "skip_existing", "prefer_existing")
_SONG_KEYS = ("model", "lyrics_language", "lyrics_words", "lyrics_lrc_words")
log = logging.getLogger(__name__)


def kind_of(path):
    suffix = Path(path).suffix.lower()
    return "video" if suffix in VIDEO else "song" if suffix in AUDIO else None


def signature(settings, kind):
    """Changes when a setting that changes this kind of job's result changes."""
    keys = _VIDEO_KEYS if kind == "video" else _SONG_KEYS
    data = {k: v for k, v in settings.model_dump().items() if k in keys}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _legacy_signature(settings):
    # What the old scanner stored in the `seen` table, so files it already queued
    # aren't queued a second time after an upgrade.
    keys = ("model", "source_language", "target_languages", "direct_english", "skip_existing", "prefer_existing")
    return hashlib.sha256(json.dumps({k: v for k, v in settings.model_dump().items() if k in keys}, sort_keys=True).encode()).hexdigest()


def subtitle_languages(folder, stem, names, own_only=False):
    """Languages with a subtitle file next to a video (same rules as the pipeline:
    forced and empty files don't count). With own_only, only Zilcho Subs' own
    ``.AI`` files count."""
    result = {}
    prefix = stem + "."
    for name in names:
        if not name.startswith(prefix) or os.path.splitext(name)[1].lower() not in SUBTITLE_SUFFIXES:
            continue
        tags = name[len(prefix):].split(".")[:-1]
        if not tags or "forced" in [t.lower() for t in tags] or (own_only and "AI" not in tags):
            continue
        path = Path(folder) / name
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                continue
        except OSError:
            continue
        language = tags[0].split("-")[0]
        result.setdefault(ALIASES.get(language.lower(), language), name)
    return result


def lyric_files(stem, names):
    """Lyrics already next to a song: Song.lrc, Song.txt, Song.words.lrc or our Song.AI.lrc."""
    wanted = {stem + ".lrc", stem + ".txt", stem + ".words.lrc"}
    return sorted(n for n in names if n in wanted or (n.startswith(stem + ".AI.") and n.lower().endswith((".lrc", ".txt"))))


def check(path, names, settings, inside=None):
    """What's there and what's missing for one file. Reads names only (no ffprobe).

    inside: what's embedded in the file (zilcho_subs.embedded.check), or None. Embedded
    subtitle tracks count only with skip_existing on (like the pipeline); embedded
    lyrics always count. A failed look counts as nothing inside."""
    kind = kind_of(path)
    if kind == "video":
        present = subtitle_languages(path.parent, path.stem, names, own_only=not settings.skip_existing)
        have = set(present) | (embedded.languages(inside) if settings.skip_existing else set())
        missing = [t for t in settings.target_languages if t not in have]
        return kind, missing, sorted(set(present.values()))
    found = lyric_files(path.stem, names)
    return kind, ([] if found or embedded.has_lyrics(inside) else ["lyrics"]), found


def _get(store, key, default=None):
    with store.db() as db:
        row = db.execute("SELECT value FROM monitor_state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _put(store, **values):
    with store.db() as db:
        for key, value in values.items():
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES(?,?)", (key, json.dumps(value)))


def _job_status(store, job_id):
    if not job_id:
        return None
    try:
        return store.job(job_id)["status"]
    except KeyError:
        return None


def audit_file(store, root, path, names, settings, now, generate=(), retry=False, skip_wait=False):
    """Audits one media file and, if asked, queues what's missing.

    generate: the kinds ("video", "song") that may be queued.
    retry: the person asked (a button), so a failed or cancelled attempt is tried again.
    skip_wait: don't wait for the file to stop changing (one file someone picked).
    Returns the new job id or None.
    """
    root = Path(root).resolve()
    relative = path.relative_to(root).as_posix()
    kind = kind_of(path)
    stat = path.stat()
    with store.db() as db:
        row = db.execute("SELECT * FROM audit WHERE path=?", (relative,)).fetchone()
        if not row or row["size"] != stat.st_size or row["mtime"] != stat.st_mtime_ns:
            # New or changed. A file that's still being written has a fresh modified
            # time (cp -p and rsync set the old time only once the data is in), so
            # one last modified longer ago than the settle time counts as settled.
            stable, job_id, sig = min(now, stat.st_mtime), "", ""
            if not row and kind == "video":
                seen = db.execute("SELECT * FROM seen WHERE path=?", (relative,)).fetchone()
                if seen and seen["size"] == stat.st_size and seen["mtime"] == stat.st_mtime_ns and seen["signature"] == _legacy_signature(settings):
                    stable, sig = min(stable, seen["stable"]), signature(settings, kind)
            if now - stable < settings.stable_seconds and not skip_wait:
                # Wait until it stops changing (still copying, still importing).
                db.execute("INSERT OR REPLACE INTO audit(path,kind,size,mtime,stable,checked,state,missing,found,job_id,signature) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (relative, kind, stat.st_size, stat.st_mtime_ns, now, now, "waiting", "[]", "[]", "", ""))
                return None
        else:
            stable, job_id, sig = row["stable"], row["job_id"], row["signature"]
            if now - stable < settings.stable_seconds and not skip_wait:
                db.execute("UPDATE audit SET checked=?,state='waiting' WHERE path=?", (now, relative))
                return None
    # One light ffprobe per new or changed file (the answer is kept in app data).
    kind, missing, found = check(path, names, settings, embedded.check(store, root, relative))
    status = _job_status(store, job_id)
    wanted = signature(settings, kind)
    queued = None
    if status in ACTIVE:
        state = "queued"
    elif not missing:
        state = "complete"
    elif sig == wanted and status in ("completed", "skipped"):
        # Already tried with these settings; the job found a track inside the file
        # (or a language could not be made). Don't loop on it.
        state = "complete" if status == "skipped" else "missing"
    else:
        again = retry and status in ("failed", "cancelled", None)
        if kind in generate and (sig != wanted or again):
            if kind == "video":
                spec = {"path": relative, "settings": settings.model_dump(), "audio_stream": None, "regenerate": False}
                queued = store.enqueue("subtitle", spec)
            else:
                queued = store.enqueue("lyrics", {"path": relative, "regenerate": False, "settings": settings.model_dump()})
            job_id, sig, state = queued, wanted, "queued"
        else:
            state = "failed" if status == "failed" and sig == wanted else "missing"
    with store.db() as db:
        db.execute("INSERT OR REPLACE INTO audit(path,kind,size,mtime,stable,checked,state,missing,found,job_id,signature) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (relative, kind, stat.st_size, stat.st_mtime_ns, stable, now, state, json.dumps(missing), json.dumps(found), job_id, sig))
    return queued


def audit_library(store, root, now=None, limit=5000, generate=(), retry=False):
    """One pass over the library folders (Settings: library subfolders).

    Big libraries are done in slices of `limit` files; a cursor in app data
    remembers where the next pass starts. When a full cycle finishes, rows for
    files that are gone (or are outside the chosen folders now) are dropped.
    Returns {"visited", "queued": [job ids], "cycle_done"}.
    """
    now = time.time() if now is None else now
    settings = store.settings()
    root = Path(root).resolve()
    cursor_file = store.config / "monitor-cursor.json"
    try:
        cursor = json.loads(cursor_file.read_text())
        offset, cycle_start = int(cursor["offset"]), float(cursor["cycle_start"])
    except (OSError, ValueError, KeyError, TypeError):
        offset, cycle_start = 0, now
    if offset == 0:
        cycle_start = now
    skipped = visited = 0
    queued = []
    for relative in settings.scan_dirs:
        folder = media_path(root, relative)
        if not folder.is_dir():
            continue
        for parent, dirs, files in os.walk(folder, followlinks=False):
            parent = Path(parent)
            dirs[:] = [d for d in sorted(dirs) if not d.startswith(".") and not (parent / d).is_symlink()]
            names = sorted(files)
            for name in names:
                if skipped < offset:
                    skipped += 1
                    continue
                visited += 1
                if visited > limit:
                    cursor_file.write_text(json.dumps({"offset": offset + visited - 1, "cycle_start": cycle_start}))
                    return {"visited": visited - 1, "queued": queued, "cycle_done": False}
                path = parent / name
                if name.startswith(".") or path.is_symlink() or not kind_of(path):
                    continue
                try:
                    job = audit_file(store, root, path, names, settings, now, generate, retry=retry)
                except FileNotFoundError:
                    continue  # moved or renamed while scanning; the next pass sees it
                if job:
                    queued.append(job)
    with store.db() as db:
        db.execute("DELETE FROM audit WHERE checked<?", (cycle_start,))
    cursor_file.write_text(json.dumps({"offset": 0, "cycle_start": now}))
    return {"visited": visited, "queued": queued, "cycle_done": True}


def audit_path(store, root, relative, generate=(), now=None):
    """Audits one file now (no stability wait), e.g. from the API."""
    path = media_path(root, relative, file=True, kinds=VIDEO | AUDIO)
    settings = store.settings()
    if not in_folders(path.relative_to(Path(root).resolve()).as_posix(), settings.scan_dirs):
        raise ValueError("That file is not in a collection")
    names = sorted(os.listdir(path.parent))
    queued = audit_file(store, root, path, names, settings, time.time() if now is None else now, generate, retry=True, skip_wait=True)
    return {"queued": [queued] if queued else [], "item": item(store, path.relative_to(Path(root).resolve()).as_posix())}


def scan_once(store, media_root, now=None, limit=5000):
    """The old watcher entry point: audit and queue missing video subtitles."""
    return audit_library(store, media_root, now=now, limit=limit, generate=("video",))["queued"]


def auto_kinds(settings):
    if not settings.watcher_enabled:
        return ()
    return ("video", "song") if settings.auto_lyrics else ("video",)


# The state a row shows now: a queued file follows its job until the next check
# records the result, so the page and the status card always agree.
_LIVE = ("CASE WHEN a.state='queued' THEN CASE WHEN j.status IN ('queued','running','cancelling','pausing','paused') THEN 'queued' "
         "WHEN j.status IN ('completed','skipped') THEN 'complete' WHEN j.status='failed' THEN 'failed' ELSE 'missing' END ELSE a.state END")
_FROM = " FROM audit a LEFT JOIN jobs j ON j.id=a.job_id"


def _row(row):
    value = {k: row[k] for k in ("path", "kind", "size", "mtime", "stable", "checked", "job_id", "signature")}
    value["state"] = row["live"]
    value["missing"] = json.loads(row["missing"])
    value["found"] = json.loads(row["found"])
    value["job_status"] = row["job_status"]
    return value


def _in_folders_sql(folders, column="a.path"):
    """(sql, args): rows whose path is inside one of the library folders. Files outside
    every library stay in app data for a while but are never shown or counted."""
    if any(not f for f in folders):
        return "1", []
    if not folders:
        return "0", []
    sql = "(" + " OR ".join(f"substr({column},1,?)=?" for _ in folders) + ")"
    args = [x for f in folders for x in (len(f) + 1, f + "/")]
    return sql, args


def _select(where=""):
    return "SELECT a.*," + _LIVE + " AS live,j.status AS job_status" + _FROM + where


def item(store, relative):
    with store.db() as db:
        row = db.execute(_select(" WHERE a.path=?"), (relative,)).fetchone()
    return _row(row) if row else None


_ORDER = {
    "status": "CASE live WHEN 'failed' THEN 0 WHEN 'missing' THEN 1 WHEN 'queued' THEN 2 WHEN 'waiting' THEN 3 ELSE 4 END, a.path",
    "name": "a.path COLLATE NOCASE",
    "name_desc": "a.path COLLATE NOCASE DESC",
}


def items(store, state=None, kind=None, limit=200, offset=0, query="", missing=None, sort="status", folders=None):
    """One page of the audit list.

    query: fuzzy search on the file name (and folder names); best matches first.
    missing: "lyrics" for songs without lyrics, or a language code for videos
    without subtitles in it (still missing now, so finished jobs don't count).
    limit: 0 means everything (up to 10,000 rows).
    folders: only files inside these folders; default is every library.
    """
    from .status import fuzzy_rank
    folders = store.settings().scan_dirs if folders is None else folders
    clause, clause_args = _in_folders_sql(folders)
    where, args = [clause], list(clause_args)
    if state:
        where.append(_LIVE + "=?"); args.append(state)
    if kind:
        where.append("a.kind=?"); args.append(kind)
    if missing:
        where.append("a.kind=? AND a.missing LIKE ? AND " + _LIVE + " NOT IN ('complete','waiting')")
        args += ["song", '%"lyrics"%'] if missing == "lyrics" else ["video", '%' + json.dumps(missing) + '%']
    sql = (" WHERE " + " AND ".join(where)) if where else ""
    order = " ORDER BY " + _ORDER.get(sort, _ORDER["status"])
    limit = 10000 if limit <= 0 else limit
    with store.db() as db:
        if not query.strip():
            total = db.execute("SELECT COUNT(*)" + _FROM + sql, args).fetchone()[0]
            rows = db.execute(_select(sql) + order + " LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
            return total, [_row(r) for r in rows]
        # Search: rank every matching path in Python (names only), then load the page.
        paths = [r[0] for r in db.execute("SELECT a.path," + _LIVE + " AS live" + _FROM + sql + order, args)]
        ranked = []
        for i, path in enumerate(paths):
            rank = fuzzy_rank(query, path.rsplit("/", 1)[-1], path)
            if rank is not None:
                ranked.append((rank, i, path))
        ranked.sort()
        page = [p for _, _, p in ranked[offset:offset + limit]]
        rows = {}
        for start in range(0, len(page), 500):
            chunk = page[start:start + 500]
            for r in db.execute(_select(" WHERE a.path IN (" + ",".join("?" * len(chunk)) + ")"), chunk):
                rows[r["path"]] = r
    return len(ranked), [_row(rows[p]) for p in page if p in rows]


def summary(store, monitor=None, now=None, folders=None):
    """Counts and the status line behind the on-screen indicator (files inside the libraries)."""
    now = time.time() if now is None else now
    settings = store.settings()
    clause, clause_args = _in_folders_sql(settings.scan_dirs if folders is None else folders)
    counts = {k: {s: 0 for s in STATES} for k in KINDS}
    dismissed = float(_get(store, "dismissed", 0) or 0)
    marks = ",".join("?" * len(ACTIVE))
    with store.db() as db:
        for row in db.execute("SELECT a.kind," + _LIVE + " AS live,COUNT(*)" + _FROM + " WHERE " + clause + " GROUP BY a.kind,live", clause_args):
            if row[0] in counts and row[1] in counts[row[0]]:
                counts[row[0]][row[1]] = row[2]
        results = {r[0]: r[1] for r in db.execute(
            "SELECT j.status,COUNT(*) FROM audit a JOIN jobs j ON j.id=a.job_id WHERE j.updated>? AND j.status IN ('completed','failed') AND " + clause + " GROUP BY j.status", (dismissed, *clause_args))}
        active = db.execute("SELECT COUNT(*) FROM audit a JOIN jobs j ON j.id=a.job_id WHERE j.status IN (" + marks + ") AND " + clause, (*ACTIVE, *clause_args)).fetchone()[0]
        pending = db.execute("SELECT COUNT(*) FROM jobs WHERE archived=0 AND status IN (" + marks + ")", ACTIVE).fetchone()[0]
    scanning = bool(monitor and monitor.scanning)
    if scanning:
        status = "scanning"
    elif not settings.watcher_enabled:
        status = "off"
    elif settings.paused:
        status = "paused"
    elif not allowed_hour(settings, time.localtime(now).tm_hour):
        status = "quiet"
    else:
        status = "watching"
    last = _get(store, "last_scan")
    link = jellyfin.public(store)
    return {
        "status": status,
        "watcher_enabled": settings.watcher_enabled,
        "auto_lyrics": settings.auto_lyrics,
        "scan_interval": settings.scan_interval,
        "last_scan": last,
        "last_full_scan": _get(store, "last_full_scan"),
        "next_scan": (last or now) + settings.scan_interval if settings.watcher_enabled else None,
        "last_error": _get(store, "last_error", ""),
        "counts": counts,
        "generated": results.get("completed", 0),
        "failed": results.get("failed", 0),
        "in_queue": active,
        "missing": sum(counts[k]["missing"] + counts[k]["failed"] for k in KINDS),
        "pending_jobs": pending,
        "jellyfin": {"enabled": link["enabled"], "error": link["last_error"], "last_ok": link["last_ok"],
                     "last_notified": link["last_notified"]},
        "servers": mediaserver.summary(store),
        "dismissed": dismissed,
    }


def dismiss(store, now=None):
    _put(store, dismissed=time.time() if now is None else now)


class Monitor:
    """Background thread: a scheduled pass every scan_interval while watching is
    on, plus a full audit whenever someone presses Scan now / Generate missing."""

    def __init__(self, store, root):
        self.store, self.root = store, root
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.pending = None
        self.scanning = False
        self.thread = None
        self.last_scan = 0
        self.last_notify = 0

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="zilcho-monitor", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set(); self.wake.set()
        if self.thread:
            self.thread.join(timeout=8)

    @property
    def alive(self):
        return bool(self.thread and self.thread.is_alive())

    def request(self, generate=()):
        """Asks for a full audit soon; generate adds kinds to queue."""
        with self.lock:
            self.pending = tuple(sorted(set(self.pending or ()) | set(generate)))
        self.wake.set()

    def full_audit(self, generate=()):
        """Runs passes until a whole cycle is done (bounded, in case files keep appearing)."""
        queued = []
        for _ in range(1000):
            result = audit_library(self.store, self.root, generate=generate, retry=True)
            queued += result["queued"]
            if result["cycle_done"]:
                break
        return queued

    def run(self, generate, explicit):
        self.scanning = True
        try:
            if explicit:
                self.full_audit(generate)
                full = True
            else:
                full = audit_library(self.store, self.root, generate=generate)["cycle_done"]
            now = time.time()
            _put(self.store, last_scan=now, last_error="")
            if full:
                _put(self.store, last_full_scan=now)
                mediaserver.refresh_all(self.store, explicit=explicit)
        except Exception as error:  # a bad share must not stop the monitor
            log.exception("Library audit failed")
            _put(self.store, last_scan=time.time(), last_error=str(error)[:300])
        finally:
            self.scanning = False

    def loop(self):
        while not self.stop_event.is_set():
            with self.lock:
                pending, self.pending = self.pending, None
            try:
                settings = self.store.settings()
                if pending is not None:
                    self.run(pending, explicit=True)
                    self.last_scan = time.time()
                elif settings.watcher_enabled and time.time() - self.last_scan >= settings.scan_interval:
                    self.run(auto_kinds(settings), explicit=False)
                    self.last_scan = time.time()
            except Exception:
                log.exception("Monitor loop failed")
            if time.time() - self.last_notify >= 5:
                self.last_notify = time.time()
                # Jellyfin / Emby / Plex are optional; each catches its own errors.
                mediaserver.notify_all(self.store)
            self.wake.wait(1)
            self.wake.clear()
