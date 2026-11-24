"""At-a-glance file status for the list pages (Media library, Song lyrics,
Monitor library), plus the fuzzy search they share.

Read-only: it looks at file names next to the media, and reads the start of a
song's .lrc to see whether it has word-by-word timing. It never writes.
"""
import re
from pathlib import Path
from . import embedded as _embedded
from .monitor import subtitle_languages

_WORD_TIME = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")


def _source(has_embedded, has_file, result):
    """Where subtitles or lyrics come from: embedded, external, both, missing,
    unchecked (inside of the file not looked at yet) or failed (the look failed)."""
    if result is None:
        return "unchecked"
    if result.get("error"):
        return "failed"
    return "both" if has_embedded and has_file else "embedded" if has_embedded else "external" if has_file else "missing"


def video_status(folder, stem, names, targets, embedded=None, use_embedded=True):
    """Subtitle languages next to a video (any subtitle file counts, ours or not;
    forced and empty files don't), and which target languages have none.

    embedded: what's inside the file (zilcho_subs.embedded), or None if not checked.
    A language counts as present if a file or a usable embedded track has it; tracks
    inside only count when use_embedded is on (the skip-existing setting), like the
    pipeline. A failed look falls back to the files alone.

    Returns subs/missing (what counts), external and embedded language lists,
    unknown (embedded tracks without a language), source and checked."""
    return video_status_from(sorted(subtitle_languages(folder, stem, names)), targets, embedded, use_embedded)


def video_status_from(external, targets, embedded=None, use_embedded=True):
    """video_status when the languages of the files next to the video are already known."""
    inside = sorted(_embedded.languages(embedded))
    present = set(external) | (set(inside) if use_embedded else set())
    return {"subs": sorted(present), "missing": [t for t in targets if t not in present],
            "external": external, "embedded": inside, "unknown": _embedded.unknown_tracks(embedded),
            "bitmap": sorted(_embedded.bitmap_languages(embedded)),
            "source": _source(bool(inside or _embedded.unknown_tracks(embedded)), bool(external), embedded),
            "checked": embedded is not None, "error": (embedded or {}).get("error")}


def _has_word_timing(path, limit=16384):
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return bool(_WORD_TIME.search(handle.read(limit)))
    except OSError:
        return False


def song_status(folder, stem, names, embedded=None):
    """Lyrics next to a song, or inside it.

    lyrics: Song.lrc, Song.txt or our Song.AI.lrc/.txt is there, or the file has a
    lyrics tag (embedded; None means not looked at yet, a failed look counts as no tag).
    enhanced: word-by-word lyrics: Song.words.lrc, or word times inside Song.lrc
    (or Song.AI.lrc). Tags inside the file never count as enhanced.
    Also: lyrics_file, lyrics_embedded, lyrics_source, checked and error.
    """
    names = set(names)
    lrc = [n for n in (stem + ".lrc", stem + ".AI.lrc") if n in names]
    plain = [n for n in names if n == stem + ".txt" or (n.startswith(stem + ".AI.") and n.lower().endswith(".txt"))]
    words = stem + ".words.lrc" in names
    enhanced = words or any(_has_word_timing(Path(folder) / n) for n in lrc)
    files = sorted(set(lrc) | set(plain) | ({stem + ".words.lrc"} if words else set()))
    in_file, inside = bool(lrc or plain), _embedded.has_lyrics(embedded)
    return {"lyrics": in_file or inside, "enhanced": enhanced, "files": files,
            "lyrics_file": in_file, "lyrics_embedded": inside, "lyrics_source": _source(inside, in_file, embedded),
            "checked": embedded is not None, "error": (embedded or {}).get("error")}


def status_for(path, names, targets, kind, embedded=None, use_embedded=True):
    path = Path(path)
    if kind == "video":
        return video_status(path.parent, path.stem, names, targets, embedded, use_embedded)
    return song_status(path.parent, path.stem, names, embedded)


def fuzzy_rank(query, name, path=""):
    """How well a search matches, lower is better; None means no match.

    0: the name starts with it ("Co" -> "Core")
    1: the name contains it
    2: a folder in the path contains it (2+ letters), e.g. a show's name
    3: the letters appear in order in the name ("cr" -> "Core")
    """
    q = " ".join(query.casefold().split())
    if not q:
        return 0
    n = name.casefold()
    if n.startswith(q):
        return 0
    if q in n:
        return 1
    folders = path.casefold().split("/")[:-1]
    if len(q) >= 2 and any(q in f for f in folders):
        return 2
    letters = iter(n)
    if all(ch in letters for ch in q.replace(" ", "")):
        return 3
    return None
