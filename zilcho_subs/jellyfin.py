"""Optional Jellyfin link (kept for older callers).

The code is shared with Emby and Plex in ``mediaserver.py``; these names are
the Jellyfin versions of its functions. Settings stay in ``jellyfin.json`` and
the ``jellyfin_*`` state keys, as before.
"""
from functools import partial
from . import mediaserver
from .mediaserver import RETRY_AFTER, REFRESH_EVERY, BATCH, TIMEOUT, DEFAULTS, configured  # noqa: F401

KIND = "jellyfin"
FILE = "jellyfin.json"
JellyfinError = mediaserver.ServerError

clean_url = partial(mediaserver.clean_url, KIND)
clean_root = partial(mediaserver.clean_root, KIND)
load = partial(mediaserver.load, KIND)
save = partial(mediaserver.save, KIND)
forget = partial(mediaserver.forget, KIND)
public = partial(mediaserver.public, KIND)
test = partial(mediaserver.test, KIND)
notify_new = partial(mediaserver.notify_new, KIND)
refresh_library = partial(mediaserver.refresh_library, KIND)
jellyfin_path = mediaserver.server_path


def call(config, method, path, body=None):
    return mediaserver.call(KIND, config, method, path, body)
