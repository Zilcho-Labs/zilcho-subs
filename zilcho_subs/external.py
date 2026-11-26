"""The API key and the little it allows: another app (Zilcho Forge) may test the connection,
ask for subtitles for a video in a collection, and read how that request is going.

The key is shown once, kept only as a hash, accepted only in the X-Api-Key header, and opens
nothing else.
"""
import hashlib
import json
import os
import secrets
import time

from .core import ALIASES, Settings, in_folders, media_path

PREFIX = "zsk_"
_FILE = "api_key.json"
MAX_LANGUAGES = 8


def _path(store):
    return store.config / _FILE


def _saved(store):
    try:
        data = json.loads(_path(store).read_text(encoding="utf-8"))
        return data if isinstance(data.get("hash"), str) else None
    except (OSError, ValueError, AttributeError):
        return None


def _hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def info(store):
    saved = _saved(store)
    return {"exists": bool(saved), "created": saved.get("created") if saved else None}


def create(store):
    """A new key, replacing any earlier one. This is the only time it can be read."""
    key = PREFIX + secrets.token_urlsafe(32)
    temp = _path(store).with_suffix(".tmp")
    temp.write_text(json.dumps({"hash": _hash(key), "created": time.time()}), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(_path(store))
    return key


def remove(store):
    _path(store).unlink(missing_ok=True)


def matches(store, key):
    saved = _saved(store)
    if not saved or not key or len(key) > 200:
        return False
    return secrets.compare_digest(_hash(key), saved["hash"])


def find_video(store, root, path):
    """The collection video meant by another app's path. The two apps may see the library
    under different top folders, so leading folders are dropped until a file is found; at
    least its folder and name must match."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise ValueError("Choose a video inside a collection")
    folders = store.settings().scan_dirs
    shortest = min(len(parts), 2)
    for start in range(0, len(parts) - shortest + 1):
        relative = "/".join(parts[start:])
        if relative.startswith(".") or not in_folders(relative, folders):
            continue
        try:
            found = media_path(root, relative, file=True)
        except (ValueError, OSError):
            continue
        if found.is_file():
            return relative
    raise FileNotFoundError(path)


def request_subtitles(store, root, path, languages):
    """Queue one ordinary subtitle job for these languages, with the other saved settings."""
    if not languages or len(languages) > MAX_LANGUAGES:
        raise ValueError("Choose between 1 and 8 subtitle languages")
    wanted = [ALIASES.get(str(x).strip().lower(), str(x).strip()) for x in languages]
    settings = Settings.model_validate({**store.settings().model_dump(),
                                        "target_languages": wanted, "skip_existing": True})
    relative = find_video(store, root, path)
    spec = {"path": relative, "audio_stream": None, "regenerate": False, "external": True,
            "settings": settings.model_dump()}
    return {"id": store.enqueue("subtitle", spec), "path": relative}


def job_state(store, job_id):
    """Progress of a request made this way; other jobs are not visible."""
    job = store.job(job_id)
    if not job["spec"].get("external"):
        raise KeyError(job_id)
    return {"id": job["id"], "status": job["status"], "stage": job["stage"],
            "progress": job["progress"], "error": job.get("error", ""),
            "languages": sorted(job["outputs"]),
            "files": [str(v).rsplit("/", 1)[-1] for v in job["outputs"].values()]}
