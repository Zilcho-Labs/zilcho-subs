"""At-a-glance file status for the list pages (Media library, Song lyrics,
Monitor library), plus the fuzzy search they share.

Read-only: it looks at file names next to the media, and reads the start of a
song's .lrc to see whether it has word-by-word timing. It never writes.
"""
import re
from pathlib import Path
from .monitor import subtitle_languages

_WORD_TIME = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")


def video_status(folder, stem, names, targets):
    """Subtitle languages next to a video (any subtitle file counts, ours or not;
    forced and empty files don't), and which target languages have none."""
    present = subtitle_languages(folder, stem, names)
    return {"subs": sorted(present), "missing": [t for t in targets if t not in present]}


def _has_word_timing(path, limit=16384):
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return bool(_WORD_TIME.search(handle.read(limit)))
    except OSError:
        return False


def song_status(folder, stem, names):
    """Lyrics next to a song.

    lyrics: Song.lrc, Song.txt or our Song.AI.lrc/.txt is there.
    enhanced: word-by-word lyrics: Song.words.lrc, or word times inside Song.lrc
    (or Song.AI.lrc).
    """
    names = set(names)
    lrc = [n for n in (stem + ".lrc", stem + ".AI.lrc") if n in names]
    plain = [n for n in names if n == stem + ".txt" or (n.startswith(stem + ".AI.") and n.lower().endswith(".txt"))]
    words = stem + ".words.lrc" in names
    enhanced = words or any(_has_word_timing(Path(folder) / n) for n in lrc)
    files = sorted(set(lrc) | set(plain) | ({stem + ".words.lrc"} if words else set()))
    return {"lyrics": bool(lrc or plain), "enhanced": enhanced, "files": files}


def status_for(path, names, targets, kind):
    path = Path(path)
    if kind == "video":
        return video_status(path.parent, path.stem, names, targets)
    return song_status(path.parent, path.stem, names)


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
