"""Optional links to media servers: Jellyfin, Emby and Plex.

Zilcho Subs writes subtitle and lyric files next to the media, where every
media server already looks for them. These links only tell the server that a
folder changed, so new files show up without waiting for the server's own
scheduled scan:

- Jellyfin and Emby: ``POST /Library/Media/Updated`` with the changed folders
  (Emby under ``/emby``). The server then picks up the new subtitle and lyric
  files, and any other new files in that folder (cover art, for example).
- Plex: a partial scan of just that folder in the Plex library that contains
  it (``GET /library/sections/<id>/refresh?path=<folder>``). If one library
  has many changed folders, that library is scanned once instead.

Optionally, after a full Monitor check, the server is asked to rescan all its
libraries (Jellyfin/Emby ``POST /Library/Refresh``, Plex
``/library/sections/all/refresh``).

Safety, the same for all three:
- Off until set up. Without an address and key/token nothing is sent anywhere.
- The key is kept in app data (``<server>.json``, owner-only permissions where
  the file system supports it). It's never sent to the browser or written to a
  log; the API only says whether one is saved.
- Only the configured server is contacted, redirects are not followed (so the
  key can't be passed on to another address), and every call has a short
  timeout. A slow or missing server never holds up the monitor or the queue.
- Standard library only (no new dependency).
"""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import PurePosixPath
from . import __version__

KINDS = ("jellyfin", "emby", "plex")
NAMES = {"jellyfin": "Jellyfin", "emby": "Emby", "plex": "Plex"}
PORTS = {"jellyfin": 8096, "emby": 8096, "plex": 32400}
KEY_WORD = {"jellyfin": "API key", "emby": "API key", "plex": "token"}
KEY_HELP = {"jellyfin": "check it in Jellyfin: Dashboard → API Keys",
            "emby": "check it in Emby: Settings → Advanced → API Keys",
            "plex": "check the X-Plex-Token: Plex Web → any item → ⋯ → Get Info → View XML"}
TIMEOUT = 8
RETRY_AFTER = 120          # seconds to wait after a failed call
REFRESH_EVERY = 3600       # automatic full rescans at most once an hour
BATCH = 200                # folders per Jellyfin/Emby notification
PLEX_SECTION_SCAN = 25     # more changed folders than this in one Plex library: scan that library once
DEFAULTS = {"url": "", "api_key": "", "media_root": "/media", "notify": True, "refresh_after_audit": False}
log = logging.getLogger(__name__)


class ServerError(Exception):
    pass


def _check(kind):
    if kind not in KINDS:
        raise ValueError("Unknown media server")
    return kind


def clean_url(kind, value):
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Enter the %s address like http://192.168.1.10:%d" % (NAMES[kind], PORTS[kind]))
    return value


def clean_root(kind, value):
    value = (value or "").strip() or "/media"
    message = "Enter the media folder as %s sees it, like /media or /data/media" % NAMES[kind]
    if "\x00" in value or len(value) > 1024:
        raise ValueError(message)
    if not value.startswith("/") and not (len(value) > 2 and value[1] == ":" and value[2] in "\\/"):
        raise ValueError(message)
    return value.rstrip("/\\") or "/"


def _file(store, kind):
    return store.config / ("%s.json" % _check(kind))


def load(kind, store):
    try:
        data = json.loads(_file(store, kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {k: data.get(k, v) for k, v in DEFAULTS.items()}


def save(kind, store, url, api_key=None, media_root="/media", notify=True, refresh_after_audit=False):
    """Saves the link. api_key None or "" keeps the saved key; forget() removes it."""
    current = load(kind, store)
    data = {"url": clean_url(kind, url), "media_root": clean_root(kind, media_root),
            "notify": bool(notify), "refresh_after_audit": bool(refresh_after_audit),
            "api_key": (api_key or "").strip() or current["api_key"]}
    if len(data["api_key"]) > 256 or any(c.isspace() or ord(c) < 32 for c in data["api_key"]):
        raise ValueError("That doesn't look like a %s %s" % (NAMES[kind], KEY_WORD[kind]))
    _write(_file(store, kind), data)
    if data != current:
        _set(store, kind, error="")   # the old message was about the old settings (the retry wait stays)
    return public(kind, store)


def forget(kind, store):
    """Turns the link off: removes the address and key."""
    _file(store, kind).unlink(missing_ok=True)
    _set(store, kind, error="", retry=0)


def _write(path, data):
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(data, out)
    os.replace(temp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def configured(config):
    return bool(config["url"] and config["api_key"])


# State lives in monitor_state as "<kind>_<name>" (jellyfin_cursor, plex_error, ...).
def _get(store, kind, name, default=None):
    with store.db() as db:
        row = db.execute("SELECT value FROM monitor_state WHERE key=?", ("%s_%s" % (kind, name),)).fetchone()
    return json.loads(row[0]) if row else default


def _set(store, kind, **values):
    with store.db() as db:
        for name, value in values.items():
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES(?,?)", ("%s_%s" % (kind, name), json.dumps(value)))


def public(kind, store):
    """What the web page may see. Never the key."""
    config = load(kind, store)
    on = configured(config)
    return {
        "kind": kind, "name": NAMES[kind],
        "url": config["url"], "media_root": config["media_root"], "notify": config["notify"],
        "refresh_after_audit": config["refresh_after_audit"], "has_key": bool(config["api_key"]),
        "enabled": on,
        "server": _get(store, kind, "server", None),
        "last_ok": _get(store, kind, "ok", None),
        "last_error": _get(store, kind, "error", "") if on else "",
        "last_notified": _get(store, kind, "notified", None),
        "last_refresh": _get(store, kind, "refresh", None),
    }


def summary(store):
    """Short status of every link for the sidebar card."""
    out = []
    for kind in KINDS:
        link = public(kind, store)
        out.append({"kind": kind, "name": link["name"], "enabled": link["enabled"], "error": link["last_error"],
                    "last_ok": link["last_ok"], "last_notified": link["last_notified"]})
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # a 3xx becomes an error instead of sending the key elsewhere


_opener = urllib.request.build_opener(_NoRedirect)


def _headers(kind, token):
    if kind == "plex":
        return {"X-Plex-Token": token, "X-Plex-Product": "Zilcho Subs", "X-Plex-Version": __version__,
                "X-Plex-Client-Identifier": "zilcho-subs", "X-Plex-Device-Name": "Zilcho Subs"}
    auth = 'MediaBrowser Client="Zilcho Subs", Device="Zilcho Subs", DeviceId="zilcho-subs", Version="%s"' % __version__
    if kind == "jellyfin":
        return {"Authorization": auth + ', Token="%s"' % token, "X-Emby-Token": token}   # X-Emby-Token: older Jellyfin
    return {"X-Emby-Authorization": auth, "X-Emby-Token": token}


def call(kind, config, method, path, body=None, query=None):
    """One request to the saved server. Raises ServerError with a plain message."""
    name = NAMES[kind]
    if not configured(config):
        raise ServerError("%s isn't set up" % name)
    url = config["url"] + ("/emby" if kind == "emby" else "") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    for header, value in _headers(kind, config["api_key"]).items():
        request.add_header(header, value)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with _opener.open(request, timeout=TIMEOUT) as response:
            raw = response.read(4_000_000)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ServerError("%s refused the %s (%s)" % (name, KEY_WORD[kind], KEY_HELP[kind])) from None
        if 300 <= error.code < 400:
            raise ServerError("%s answered with a redirect; enter the exact address (http or https, with the port)" % name) from None
        raise ServerError("%s answered with error %d" % (name, error.code)) from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        raise ServerError("Can't reach %s at %s (%s)" % (name, config["url"], reason)) from None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _record(store, kind, error=None, now=None):
    now = time.time() if now is None else now
    if error:
        _set(store, kind, error=str(error)[:300], retry=now + RETRY_AFTER)
    else:
        _set(store, kind, error="", ok=now, retry=0)


def test(kind, store, url=None, api_key=None):
    """Checks the address and key (saved ones unless new ones are given)."""
    config = load(kind, store)
    saved = dict(config)
    if url is not None:
        config["url"] = clean_url(kind, url)
    if api_key:
        config["api_key"] = api_key.strip()
    if not configured(config):
        raise ValueError("Enter the %s address and %s first" % (NAMES[kind], "a token" if kind == "plex" else "an API key"))
    try:
        if kind == "plex":
            info = call(kind, config, "GET", "/")
            info = info.get("MediaContainer", {}) if isinstance(info, dict) else {}
            server = {"name": str(info.get("friendlyName", ""))[:120], "version": str(info.get("version", ""))[:40]}
        else:
            info = call(kind, config, "GET", "/System/Info")
            info = info if isinstance(info, dict) else {}
            server = {"name": str(info.get("ServerName", ""))[:120], "version": str(info.get("Version", ""))[:40]}
    except ServerError as error:
        if config == saved:
            _set(store, kind, error=str(error)[:300])
        raise ValueError(str(error)) from None
    if config == saved:   # testing what's saved: remember the result for the status line
        _set(store, kind, server=server)
        _record(store, kind)
    return server


def server_path(config, relative):
    """The folder as the server sees it: its media root plus our relative path."""
    root = config["media_root"]
    sep = "\\" if "\\" in root or (len(root) > 1 and root[1] == ":") else "/"
    parts = [p for p in PurePosixPath(relative).parts if p not in ("", ".")]
    return root.rstrip("/\\") + (sep + sep.join(parts) if parts else "")


def _changed_folders(store, since, until):
    """Folders of media files whose subtitle or lyrics jobs finished with new files."""
    folders, newest = [], since
    with store.db() as db:
        rows = db.execute("SELECT kind,spec,outputs,updated FROM jobs WHERE status='completed' AND kind IN ('subtitle','lyrics') "
                          "AND updated>? AND updated<=? ORDER BY updated LIMIT 1000", (since, until)).fetchall()
    for row in rows:
        newest = max(newest, row["updated"])
        try:
            spec, outputs = json.loads(row["spec"]), json.loads(row["outputs"])
        except ValueError:
            continue
        written = [v for v in outputs.values() if isinstance(v, str) and v and not v.startswith("config:")]
        path = spec.get("path")
        if not written or not isinstance(path, str):
            continue
        folder = PurePosixPath(path).parent.as_posix()
        folder = "" if folder == "." else folder
        if folder not in folders:
            folders.append(folder)
    return folders, newest, len(rows) == 1000


def _inside(path, location):
    norm = lambda p: p.replace("\\", "/").rstrip("/").casefold() if len(p) > 1 and p[1] == ":" else p.rstrip("/")
    path, location = norm(path), norm(location)
    return bool(location) and (path == location or path.startswith(location + "/"))


def plex_sections(config):
    """[(library key, folder)] for every folder of every Plex library."""
    data = call("plex", config, "GET", "/library/sections")
    directories = (data or {}).get("MediaContainer", {}).get("Directory", []) if isinstance(data, dict) else []
    out = []
    for directory in directories if isinstance(directories, list) else []:
        key = str(directory.get("key", ""))
        for location in directory.get("Location", []) or []:
            path = location.get("path") if isinstance(location, dict) else None
            if key and isinstance(path, str) and path:
                out.append((key, path))
    return out


def _notify_plex(config, paths):
    """Partial scans. Returns how many folders were inside a Plex library."""
    sections = plex_sections(config)
    by_section = {}
    for path in paths:
        matches = [(len(loc), key) for key, loc in sections if _inside(path, loc)]
        if matches:
            by_section.setdefault(max(matches)[1], []).append(path)
    for key, folders in by_section.items():
        if len(folders) > PLEX_SECTION_SCAN:
            call("plex", config, "GET", "/library/sections/%s/refresh" % urllib.parse.quote(key, safe=""))
            continue
        for folder in folders:
            call("plex", config, "GET", "/library/sections/%s/refresh" % urllib.parse.quote(key, safe=""), query={"path": folder})
    return sum(len(v) for v in by_section.values())


def notify_new(kind, store, now=None):
    """Tells the server about folders with newly made subtitles or lyrics.

    Picks up where it left off (a cursor in app data), so nothing is lost while
    the server is down; after a failure it waits RETRY_AFTER seconds. Returns
    the number of folders sent (0 when off or nothing new)."""
    now = time.time() if now is None else now
    config = load(kind, store)
    cursor = _get(store, kind, "cursor", None)
    if cursor is None:
        # First time: start from now, don't announce the whole history.
        _set(store, kind, cursor=now)
        return 0
    if not configured(config) or not config["notify"]:
        _set(store, kind, cursor=now)   # off: don't pile up a backlog
        return 0
    if now < float(_get(store, kind, "retry", 0) or 0):
        return 0
    folders, newest, more = _changed_folders(store, float(cursor), now)
    if not folders:
        if newest > float(cursor):
            _set(store, kind, cursor=newest)
        return 0
    paths = [server_path(config, f) for f in folders]
    sent = 0
    try:
        if kind == "plex":
            sent = _notify_plex(config, paths)
        else:
            for start in range(0, len(paths), BATCH):
                chunk = paths[start:start + BATCH]
                call(kind, config, "POST", "/Library/Media/Updated",
                     {"Updates": [{"Path": p, "UpdateType": "Modified"} for p in chunk]})
                sent += len(chunk)
    except ServerError as error:
        log.warning("%s update failed: %s", NAMES[kind], error)
        _record(store, kind, error, now)
        return sent
    _set(store, kind, cursor=newest, notified=now)
    if kind == "plex" and not sent:
        # Nothing to retry: the folders simply aren't in any Plex library as entered.
        _set(store, kind, error="None of the new files are inside a Plex library. Check \"Media folder as Plex sees it\" "
                                "(example: %s)" % paths[0][:150])
        return 0
    _record(store, kind, now=now)
    return sent


def refresh_library(kind, store, explicit=False, now=None):
    """Asks the server to rescan its libraries after a full Monitor check, if
    that option is on. Automatic checks do it at most once an hour."""
    now = time.time() if now is None else now
    config = load(kind, store)
    if not configured(config) or not config["refresh_after_audit"]:
        return False
    last = float(_get(store, kind, "refresh", 0) or 0)
    if not explicit and now - last < REFRESH_EVERY:
        return False
    if not explicit and now < float(_get(store, kind, "retry", 0) or 0):
        return False
    try:
        if kind == "plex":
            call(kind, config, "GET", "/library/sections/all/refresh")
        else:
            call(kind, config, "POST", "/Library/Refresh")
    except ServerError as error:
        log.warning("%s library refresh failed: %s", NAMES[kind], error)
        _record(store, kind, error, now)
        return False
    _set(store, kind, refresh=now)
    _record(store, kind, now=now)
    return True


def notify_all(store):
    """Monitor loop: every configured server, each on its own (one failing never stops the others)."""
    for kind in KINDS:
        try:
            notify_new(kind, store)
        except Exception:
            log.exception("%s notification failed", NAMES[kind])


def refresh_all(store, explicit=False):
    for kind in KINDS:
        try:
            refresh_library(kind, store, explicit=explicit)
        except Exception:
            log.exception("%s library refresh failed", NAMES[kind])
