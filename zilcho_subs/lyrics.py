"""Synced song lyrics (.lrc) made locally from the singing in a song.

Speech models hear singing far less reliably than talking, so every line here
comes from words the model actually heard, with the times it heard them.
Nothing is spread out by text length, and nothing is made up to fill gaps.
Lyric files the user (or another tool) already has are never overwritten.
"""
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path

from .core import AUDIO, JobPaused, Settings, media_path
from .timing import display_width, is_hallucination, low_confidence

LYRICS_VERSION = 'lyrics-v1'
SAMPLE_RATE = 16000
LINE_WIDTH = 40      # longest line before it wraps to a new one (CJK counts double)
LINE_GAP = 0.6       # a pause this long between two words starts a new line (shorter ones are breaths)
SHORT_LINE = 12      # a fragment narrower than this joins its neighbour...
MERGED_WIDTH = 52    # ...as long as the joined line stays this narrow
MERGE_GAP = 1.0      # ...and the two were sung within this many seconds
MAX_LINE = 7.0       # no single line lasts longer than this
STANZA_GAP = 2.5     # a pause this long starts a new verse in the plain lyrics
CLEAR_AFTER = 3.0    # a break this long clears the lyrics display (instrumentals)
PHRASE_BREAK = 2.5   # once a line is half full, a new phrase this likely (_break_score) starts a new line
SUFFIXES = {'lrc': '.lrc', 'txt': '.txt', 'words': '.words.lrc'}

_MUSIC = re.compile(r"[♪♫♬♩🎵🎶]+")
_NOISE = re.compile(r"^[\[(（【]?\s*(?:music|instrumental|music playing|applause|laughs?|laughter|singing|humming|outro|intro)\s*[\])）】]?$", re.I)
_CLAUSE = re.compile(r"[,;:.!?…、。！？][\"'”’)\]」』]*$")


def clean(text):
    """Drops music symbols and squeezes spaces, keeping a leading space if there was one."""
    text = _MUSIC.sub('', str(text))
    lead = ' ' if text[:1].isspace() else ''
    body = ' '.join(text.split())
    return lead + body if body else ''


def recognize_lyrics(model, audio, language, progress=lambda _at: None):
    """Word-timed transcription of a whole song.

    Returns {'language', 'words': [[start, end, text], ...]}. Word text keeps the
    model's own spacing, so CJK lyrics (no spaces) and English both join back
    correctly.
    """
    duration = len(audio) / SAMPLE_RATE
    if language == 'auto':
        # Songs often open with an instrumental, so listen to the middle instead.
        middle = max(0, len(audio) // 2 - 15 * SAMPLE_RATE)
        language = model.detect_language(audio=audio[middle:middle + 30 * SAMPLE_RATE])[0]
    segments, _info = model.transcribe(audio, language=language, task='transcribe', word_timestamps=True,
                                       vad_filter=False, condition_on_previous_text=False, beam_size=5,
                                       hallucination_silence_threshold=2.0)
    words = []
    for segment in segments:
        progress(min(duration, float(segment.end)))
        text = clean(segment.text).strip()
        if not text or low_confidence(segment) or is_hallucination(text) or _NOISE.match(text):
            continue
        for word in getattr(segment, 'words', None) or []:
            piece = clean(word.word)
            a, b = float(word.start), float(word.end)
            if not piece.strip() or not (math.isfinite(a) and math.isfinite(b)) or b < a:
                continue
            if words and a < words[-1][0]:
                continue  # time ran backwards; keep the earlier, already placed word
            words.append([round(a, 3), round(max(b, a + 0.05), 3), piece])
    return {'language': language, 'words': words, 'strategy': LYRICS_VERSION}


def lyric_lines(words):
    """Groups timed words into lyric lines: at pauses, phrase ends, or when a line gets long."""
    lines = []
    group = []

    def flush():
        text = ' '.join(''.join(w[2] for w in group).split())
        if text:
            lines.append({'start': group[0][0], 'end': group[-1][1], 'text': text,
                          'words': [[w[0], w[1], w[2]] for w in group]})

    def width(ws):
        return display_width(''.join(w[2] for w in ws).strip())

    def too_long(ws):
        return width(ws) > MERGED_WIDTH or ws[-1][1] - ws[0][0] > MAX_LINE

    def best_split(ws, shortest):
        """Where to split: the likeliest phrase boundary, else as near the usual width as possible."""
        options = [k for k in range(1, len(ws)) if width(ws[:k]) >= shortest and width(ws[:k]) <= LINE_WIDTH]
        options = options or list(range(1, len(ws)))
        k = max(options, key=lambda k: _break_score(ws[k - 1], ws[k]) - 0.5 * abs(LINE_WIDTH - width(ws[:k])) / LINE_WIDTH)
        return k, _break_score(ws[k - 1], ws[k])

    def soft_split(ws):
        # Past the usual width, split early only at a real phrase hint that leaves a half-full line.
        if width(ws) <= LINE_WIDTH:
            return None
        k, score = best_split(ws, LINE_WIDTH // 2)
        return k if score >= 1 and width(ws[:k]) >= LINE_WIDTH // 2 else None

    for word in words:
        if group:
            gap = word[0] - group[-1][1]
            # Half full and a new phrase clearly starts here.
            if gap >= LINE_GAP or (width(group) >= LINE_WIDTH // 2 and _break_score(group[-1], word) >= PHRASE_BREAK):
                flush()
                group = []
        group.append(word)
        # Longer than usual: split at the most likely phrase boundary in the line, if there's a hint of
        # one; with no hint, let the line run a little longer (up to MERGED_WIDTH) before splitting anyway.
        while len(group) > 1:
            k = best_split(group, SHORT_LINE)[0] if too_long(group) else soft_split(group)
            if k is None:
                break
            remainder = group[k:]
            group = group[:k]
            flush()
            group = remainder
    if group:
        flush()
    lines = _merge_short(lines)
    # A lone "Thank you (very much)" long after the singing stopped is the model filling the silence.
    while len(lines) > 1 and _THANKS.match(lines[-1]['text']) and lines[-1]['start'] - lines[-2]['end'] >= OUTRO_GAP:
        lines.pop()
    return lines


OUTRO_GAP = 10.0
_THANKS = re.compile(r"^\W*(?:thank you|thanks)(?: (?:so|very) much)?(?: for listening)?\W*$", re.I)


# Words a lyric line shouldn't end on ("...on the" / "Chevy").
_NO_END = {'a', 'an', 'the', 'my', 'your', 'his', 'her', 'its', 'our', 'their', 'this', 'that', 'these', 'those',
           'to', 'of', 'in', 'on', 'at', 'for', 'with', 'from', 'by', 'into', 'like', 'and', 'or', 'but', 'so',
           'if', 'as', 'than', 'i', "i'm", "i'll", "i've", "i'd", "you're", 'gonna', 'wanna', 'gotta'}
_I_WORD = re.compile(r"I(?:'[a-z]+)?$")


def _break_score(before, after):
    """How likely a new lyric phrase starts between two words (higher = better place for a line break).

    The speech model starts each phrase it hears with a capital letter ("...my bones" / "But this..."),
    so a capital is a strong hint unless the word before it can't end a line ("on the" / "Chevy").
    """
    prev, nxt = before[2].strip(), after[2].strip()
    score = 2 * min(max(after[0] - before[1], 0.0), 1.0)
    if _CLAUSE.search(prev):
        score += 3 if re.search(r"[.!?…。！？][\"'”’)\]」』]*$", prev) else 2
    if re.sub(r"[^\w']", '', prev).lower() in _NO_END:
        score -= 3
    if nxt[:1].isupper():
        score += 1 if _I_WORD.match(re.sub(r"[^\w']", '', nxt)) else 2.5
    return score


def _merge_short(lines):
    """Joins one- or two-word leftovers ("rain.") onto the line they belong to."""
    def join(a, b):
        words = a['words'] + b['words']
        return {'start': a['start'], 'end': b['end'], 'text': ' '.join(''.join(w[2] for w in words).split()), 'words': words}

    def fits(a, b):
        return b['start'] - a['end'] < MERGE_GAP and display_width(a['text'] + ' ' + b['text']) <= MERGED_WIDTH

    result = []
    for line in lines:
        if result and display_width(line['text']) < SHORT_LINE and fits(result[-1], line):
            result[-1] = join(result[-1], line)
        elif result and display_width(result[-1]['text']) < SHORT_LINE and fits(result[-1], line):
            result[-1] = join(result[-1], line)
        else:
            result.append(line)
    return result


def lrc_time(seconds):
    hundredths = max(0, round(float(seconds) * 100))
    minutes, hundredths = divmod(hundredths, 6000)
    secs, hundredths = divmod(hundredths, 100)
    return f'{minutes:02}:{secs:02}.{hundredths:02}'


def _tag(value):
    return ' '.join(str(value or '').split()).replace('[', '(').replace(']', ')')[:200]


def render_lrc(lines, meta=None, duration=None, words=False, tool='Zilcho Subs'):
    """Standard line-synced LRC; with words=True, enhanced LRC (a time before every word)."""
    meta = meta or {}
    out = []
    for tag, key in (('ti', 'title'), ('ar', 'artist'), ('al', 'album')):
        value = _tag(meta.get(key))
        if value:
            out.append(f'[{tag}:{value}]')
    if duration:
        out.append(f'[length:{lrc_time(duration)[:5]}]')
    out.append(f'[re:{_tag(tool)}]')
    for i, line in enumerate(lines):
        if words and line.get('words'):
            parts = []
            for j, (start, _end, text) in enumerate(line['words']):
                space = ' ' if j and text[:1].isspace() else ''
                parts.append(f'{space}<{lrc_time(start)}>{text.strip()}')
            body = ''.join(parts) + f"<{lrc_time(line['words'][-1][1])}>"
        else:
            body = line['text']
        out.append(f"[{lrc_time(line['start'])}]{body}")
        following = lines[i + 1]['start'] if i + 1 < len(lines) else None
        if following is None or following - line['end'] > CLEAR_AFTER:
            # An empty timed line clears the display during a long instrumental.
            clear = line['end'] + 0.5
            if following is None or clear < following:
                out.append(f'[{lrc_time(clear)}]')
    return '\n'.join(out) + '\n'


def render_text(lines):
    """Plain lyrics, with a blank line between verses."""
    out = []
    for i, line in enumerate(lines):
        if i and line['start'] - lines[i - 1]['end'] >= STANZA_GAP:
            out.append('')
        out.append(line['text'])
    return '\n'.join(out) + '\n'


_LRC_LINE = re.compile(r'^\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)$')
_LRC_TAG = re.compile(r'^\[[a-z]{2,8}:[^\]]*\]$', re.I)
_WORD_TAG = re.compile(r'<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>')


def parse_lrc(text):
    """Reads a line-synced LRC (as edited by the user). Returns [(seconds, text), ...]."""
    if len(text.encode('utf-8')) > 500_000:
        raise ValueError('Lyrics file is too large')
    result = []
    for raw in text.replace('\r\n', '\n').split('\n'):
        line = raw.strip()
        if not line or _LRC_TAG.match(line) and not _LRC_LINE.match(line):
            continue
        match = _LRC_LINE.match(line)
        if not match:
            raise ValueError('Each lyric line must start with a time like [01:23.45]: ' + line[:60])
        minutes, secs, fraction, body = match.groups()
        if int(secs) >= 60:
            raise ValueError('Seconds must be below 60: ' + line[:60])
        # Word times (<01:23.45>) may be present when Song.lrc holds word timing; the text
        # is what's edited, and unchanged lines keep their saved word times.
        body = _WORD_TAG.sub('', body)
        if '<' in body or '\x00' in body:
            raise ValueError('Use plain lyric text here, without < > tags: ' + line[:60])
        seconds = int(minutes) * 60 + int(secs) + (int(fraction) / 10 ** len(fraction) if fraction else 0)
        if result and seconds < result[-1][0]:
            raise ValueError('Lyric times must not go backwards: ' + line[:60])
        result.append((seconds, ' '.join(body.split())))
    if not any(text for _t, text in result):
        raise ValueError('No lyric lines found')
    return result


def lines_from_lrc(parsed, old_lines=(), duration=None):
    """Turns edited LRC lines back into timed lines, keeping word times for unchanged lines."""
    unused = list(old_lines)
    lines = []
    for i, (start, text) in enumerate(parsed):
        if not text:
            continue
        following = next((t for t, x in parsed[i + 1:]), None)
        end = following if following is not None else (duration or start + 4)
        line = {'start': start, 'end': max(start + 0.1, end), 'text': text}
        match = next((o for o in unused if o['text'] == text and abs(o['start'] - start) < 0.05), None)
        if match:
            unused.remove(match)
            line['end'] = match['end']
            if match.get('words'):
                line['words'] = match['words']
        lines.append(line)
    return lines


def song_tags(info):
    tags = {str(k).lower(): v for k, v in (info.get('format', {}).get('tags') or {}).items()}
    return {'title': tags.get('title', ''), 'artist': tags.get('artist') or tags.get('album_artist', ''), 'album': tags.get('album', '')}


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def publish(store, media_root, song, suffix, text):
    """Saves a lyric file next to the song without ever overwriting someone else's file.

    Song.lrc is used when it's free or when Zilcho Subs wrote the copy that's there.
    If the user (or another tool) owns Song.lrc, this writes Song.AI.lrc instead,
    then Song.AI.2.lrc and so on.
    """
    root = Path(media_root).resolve()
    data = text.encode('utf-8')
    digest = hashlib.sha256(data).hexdigest()
    temp = song.parent / ('.' + song.name + '.zilchosubs-' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        names = [song.stem + suffix] + [f"{song.stem}.AI{'' if i == 1 else '.' + str(i)}{suffix}" for i in range(1, 101)]
        for name in names:
            target = song.with_name(name)
            relative = target.relative_to(root).as_posix()
            try:
                os.link(temp, target)  # new file: published whole, never overwriting
            except FileExistsError:
                if target.is_symlink() or not target.is_file() or not store.owns_lyrics(relative, file_digest(target)):
                    continue
                os.replace(temp, target)  # our own earlier file: safe to update
            store.own_lyrics(relative, digest)
            return target
        raise ValueError('Too many lyric file versions next to this song')
    finally:
        temp.unlink(missing_ok=True)


def lyrics_data_path(store, job_id):
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Invalid job')
    return store.config / 'lyrics' / (job_id + '.json')


def process_lyrics(store, job, media_root, engine=None, probe_fn=None, extract_fn=None):
    from .pipeline import LocalEngine, extract, local_environment, model_ready, probe, select_audio
    probe_fn = probe_fn or probe
    extract_fn = extract_fn or extract
    spec = job['spec']
    settings = Settings.model_validate({**spec['settings'], 'cpu_threads': store.settings().cpu_threads})
    local_environment(store.config, threads=settings.cpu_threads)
    root = Path(media_root).resolve()
    song = media_path(media_root, spec['path'], file=True, kinds=AUDIO)
    fingerprint = (song.stat().st_size, song.stat().st_mtime_ns)
    regenerate = bool(spec.get('regenerate'))

    def progress(stage, value):
        latest = store.job(job['id'])
        if latest['cancel']:
            raise InterruptedError('Cancelled')
        if latest['pause_requested']:
            raise JobPaused()
        store.update(job['id'], stage=stage, progress=value)

    progress('Checking for existing lyrics', 2)
    existing = song.with_name(song.stem + '.lrc')
    if existing.is_file() and not existing.is_symlink() and not regenerate and settings.skip_existing:
        mine = store.owns_lyrics(existing.relative_to(root).as_posix(), file_digest(existing))
        store.update(job['id'], status='skipped', progress=100,
                     stage='Lyrics already made by Zilcho Subs; tick Regenerate to make them again' if mine else 'This song already has a lyrics file (.lrc)')
        return
    info = probe_fn(song)
    stream = select_audio(info)
    engine = engine or LocalEngine(store.config)
    if isinstance(engine, LocalEngine) and not model_ready(store.config, settings.model):
        raise ValueError('Download the selected speech model from Speech models first')
    language = settings.lyrics_language
    marker = store.config / 'models' / settings.model / 'ready.json'
    key = hashlib.sha256(json.dumps([LYRICS_VERSION, str(song), fingerprint, stream['index'], settings.model,
                                     marker.read_text() if marker.exists() else '', language]).encode()).hexdigest()
    cache = store.config / 'transcripts' / (key + '.lyrics.json')
    if cache.exists():
        result = json.loads(cache.read_text(encoding='utf-8'))
        progress('Reusing earlier recognition of this song', 80)
    else:
        with tempfile.TemporaryDirectory(prefix=job['id'] + '-', dir=store.config / 'work') as work:
            audio = Path(work) / 'audio.wav'
            progress('Extracting audio locally', 5)
            extract_fn(song, stream['index'], audio, info['duration'])
            progress('Listening to the singing', 10)
            result = engine.lyrics(audio, settings, language,
                                   lambda at: progress('Listening to the singing', 10 + 70 * min(1, at / info['duration'])))
        if result['words']:
            # Never cache "heard nothing": a retry (other model or language) should listen again.
            temp = cache.with_suffix('.tmp')
            temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            temp.replace(cache)
    offset = float(stream.get('start_time') or 0) - float(info.get('format', {}).get('start_time') or 0)
    words = [[max(0.0, a + offset), b + offset, text] for a, b, text in result['words'] if b + offset > 0]
    lines = lyric_lines(words)
    if not lines:
        raise ValueError('No singing was recognized in this song, so no lyrics file was written')
    progress('Saving lyrics', 90)
    song = media_path(media_root, spec['path'], file=True, kinds=AUDIO)
    if (song.stat().st_size, song.stat().st_mtime_ns) != fingerprint:
        raise ValueError('The song file changed while it was being processed; try again')
    meta = song_tags(info)
    files = {'lrc': render_lrc(lines, meta, info['duration'], words=settings.lyrics_lrc_words),
             'txt': render_text(lines)}
    if settings.lyrics_words:
        files['words'] = render_lrc(lines, meta, info['duration'], words=True)
    outputs = {}
    for name, text in files.items():
        outputs[name] = publish(store, root, song, SUFFIXES[name], text).relative_to(root).as_posix()
    data = {'song': spec['path'], 'title': meta['title'] or song.stem, 'artist': meta['artist'], 'album': meta['album'],
            'language': result['language'], 'duration': info['duration'], 'lines': lines}
    path = lyrics_data_path(store, job['id'])
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)
    aside = not outputs['lrc'].endswith('/' + song.stem + '.lrc') and outputs['lrc'] != song.stem + '.lrc'
    store.update(job['id'], outputs=outputs, status='completed', progress=100,
                 stage='Lyrics saved as ' + Path(outputs['lrc']).name + ' because the song already had its own .lrc' if aside else 'Lyrics ready')
