"""Subtitle Quality page: words, lines and how sure the speech model was.

Read-only towards media. Confidence is recorded while a job recognizes speech
(timing.confidence_totals); files made before that, and translated SRT jobs,
only have counts, which are read once from the file and saved in app data.
"""
import re
from pathlib import Path

from .core import media_path

MAX_READ = 2_000_000
_TIMES = re.compile(r'\d+:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d+:\d{2}:\d{2}[,.]\d{1,3}')
_WORD = re.compile(r'\w')
_TAG = re.compile(r'<[^>]*>|\{\\[^}]*\}')

# (lowest confidence %, name). Confidence below the last line is Poor.
RATINGS = ((95, 'Excellent'), (90, 'Good'), (85, 'Fair'))


def srt_counts(text):
    """(words, segments) of an SRT. A segment is a cue with a timing line; words are
    whitespace-separated, so languages written without spaces count fewer."""
    words = segments = 0
    for block in re.split(r'\n\s*\n', text.lstrip('﻿').replace('\r\n', '\n').strip()):
        lines = block.split('\n')
        at = next((i for i, line in enumerate(lines[:3]) if _TIMES.search(line)), None)
        if at is None:
            continue
        segments += 1
        words += sum(1 for line in lines[at + 1:] for token in _TAG.sub('', line).split() if _WORD.search(token))
    return words, segments


def rating(confidence):
    """Status name for a confidence between 0 and 1, or None when not recorded."""
    if confidence is None:
        return 'Not recorded'
    percent = confidence * 100
    return next((name for low, name in RATINGS if percent >= low), 'Poor')


def output_file(store, root, relative):
    if relative.startswith('config:exports/'):
        return media_path(store.config / 'exports', relative[len('config:exports/'):])
    return media_path(root, relative)


def job_report(store, root, job, recorded=None):
    """Quality of each subtitle file a finished job wrote. Files without a saved row
    are counted from disk once (the row then has no confidence)."""
    if recorded is None:
        recorded = store.quality([job['id']]).get(job['id'], {})
    files = []
    for language, relative in sorted(job['outputs'].items()):
        if not isinstance(relative, str) or not relative.endswith('.srt'):
            continue
        row = recorded.get(language)
        if row is None or row['path'] != relative:
            try:
                path = output_file(store, root, relative)
                if not path.is_file() or path.stat().st_size > MAX_READ:
                    raise FileNotFoundError(relative)
                words, segments = srt_counts(path.read_text(encoding='utf-8', errors='replace'))
            except (OSError, ValueError):
                files.append({'language': language, 'file': Path(relative).name, 'path': relative,
                              'words': None, 'segments': None, 'confidence': None, 'status': 'File missing'})
                continue
            # A changed path means the file was replaced (edited), so its old confidence still applies.
            store.record_quality(job['id'], language, relative, words, segments, keep_confidence=row is not None)
            row = {'words': words, 'segments': segments, 'confidence': row['confidence'] if row else None}
        confidence = row['confidence']
        files.append({'language': language, 'file': Path(relative).name, 'path': relative,
                      'words': row['words'], 'segments': row['segments'],
                      'confidence': None if confidence is None else round(confidence * 100, 1),
                      'status': rating(confidence)})
    spec = job['spec']
    source = spec.get('path') or spec.get('video_path') or spec.get('source_path') or spec.get('base') or ''
    return {'id': job['id'], 'kind': job['kind'], 'source': source, 'finished': job['updated'], 'files': files}


def overview(store, root, limit=50, offset=0, folders=None):
    """folders: only subtitles made for videos inside these folders (default: every library)."""
    if folders is None:
        folders = store.settings().scan_dirs
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    total, jobs = store.quality_jobs(limit, offset, folders)
    recorded = store.quality([j['id'] for j in jobs])
    reports = [job_report(store, root, j, recorded.get(j['id'], {})) for j in jobs]
    counts = {}
    for report in reports:
        for f in report['files']:
            counts[f['status']] = counts.get(f['status'], 0) + 1
    return {'total': total, 'limit': limit, 'offset': offset, 'counts': counts, 'jobs': reports}
