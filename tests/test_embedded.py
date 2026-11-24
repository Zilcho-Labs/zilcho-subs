"""Embedded subtitles and lyrics (1.5.0): detection from ffprobe output, the cache,
the status shown in Library and Library status, coverage and Make missing.

ffprobe is faked with canned JSON (tests never need real media or ffprobe), so what
this proves is how answers are read and used, not how a particular container reports
its tags. Synthetic files only; no media file is changed."""
import json
import os
import subprocess
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from zilcho_subs import embedded, monitor
from zilcho_subs.app import create_app
from zilcho_subs.core import Store
from zilcho_subs.status import song_status, video_status

OLD = 1_600_000_000


def write(path, data=b'synthetic'):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); os.utime(path, (OLD, OLD)); return path


# ----- canned ffprobe answers -----
def sub(lang=None, codec='subrip', forced=False):
    s = {'index': 2, 'codec_type': 'subtitle', 'codec_name': codec, 'disposition': {'forced': int(forced)}}
    if lang:
        s['tags'] = {'language': lang}
    return s


def video(*subs):
    return {'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'h264'}, *subs], 'format': {'tags': {'title': 'x'}}}


def song(tags=None, stream_tags=None):
    stream = {'index': 0, 'codec_type': 'audio', 'codec_name': 'mp3'}
    if stream_tags:
        stream['tags'] = stream_tags
    return {'streams': [stream], 'format': {'tags': tags or {'title': 'Song'}}}


class Done:
    def __init__(self, out):
        self.stdout = out


@pytest.fixture
def ffprobe(monkeypatch):
    """answers: file name -> dict (JSON), bytes (raw output), or an exception. calls: files probed."""
    answers, calls = {}, []

    def run(cmd, **kwargs):
        assert cmd[0] == 'ffprobe'
        name = Path(cmd[-1]).name
        calls.append(name)
        answer = answers.get(name, subprocess.CalledProcessError(1, cmd))
        if isinstance(answer, BaseException):
            raise answer
        return Done(answer if isinstance(answer, bytes) else json.dumps(answer).encode())

    monkeypatch.setattr(embedded.subprocess, 'run', run)
    return answers, calls


# ----- reading ffprobe's answer -----
def test_subtitle_tracks_languages_forced_unknown_and_picture_tracks():
    result = embedded.parse(video(sub('eng'), sub('spa', forced=True), sub(None), sub('und'), sub('jpn', 'hdmv_pgs_subtitle'), sub('fre', 'ass')))
    assert result['error'] is None and not result['lyrics']
    assert embedded.languages(result) == {'en', 'ja', 'fr'}      # forced tracks don't count; ass and srt are text tracks
    assert embedded.unknown_tracks(result) == 2         # untagged and "und" never satisfy a language
    assert embedded.bitmap_languages(result) == {'ja'}   # PGS is a picture, still a track in that language


@pytest.mark.parametrize('tags, stream_tags, found, synced', [
    ({'lyrics-eng': 'la la'}, None, True, False),                  # mp3 USLT
    ({'LYRICS': 'la la'}, None, True, False),                      # flac / ogg
    ({'lyrics': '[00:01.00]la la'}, None, True, True),             # m4a (c)lyr with times
    ({'WM/Lyrics': 'la la'}, None, True, False),                   # wma
    ({'title': 'Song'}, {'LYRICS': 'la la'}, True, False),         # ogg / opus keep tags on the stream
    ({'UNSYNCEDLYRICS': 'la la'}, None, True, False),
    ({'title': 'Song', 'artist': 'A'}, None, False, False),        # no lyrics tag
    ({'lyrics': '   '}, None, False, False),                       # an empty tag is not lyrics
])
def test_lyrics_tags(tags, stream_tags, found, synced):
    result = embedded.parse(song(tags, stream_tags))
    assert (result['lyrics'], result['synced']) == (found, synced)
    assert embedded.has_lyrics(result) is found


def test_failed_probes_report_an_error_and_never_raise(ffprobe, tmp_path):
    answers, _calls = ffprobe
    path = write(tmp_path / 'a.mkv')
    assert 'could not read' in embedded.probe_embedded(path)['error']                      # non-zero exit
    answers['a.mkv'] = b'not json'
    assert 'unreadable' in embedded.probe_embedded(path)['error']
    answers['a.mkv'] = b'[1, 2]'
    assert embedded.probe_embedded(path)['error']                                          # JSON, but not an object
    answers['a.mkv'] = FileNotFoundError()
    assert 'not installed' in embedded.probe_embedded(path)['error']
    answers['a.mkv'] = subprocess.TimeoutExpired('ffprobe', 15)
    assert 'too long' in embedded.probe_embedded(path)['error']
    failed = embedded.probe_embedded(path)
    assert embedded.languages(failed) == set() and not embedded.has_lyrics(failed)


# ----- what the list pages show: embedded, external, both, neither, failed, unchecked -----
def folder_with(tmp_path, srt=()):
    folder = tmp_path / 'media'
    write(folder / 'a.mkv')
    for name in srt:
        write(folder / name, b'x')
    return folder, sorted(os.listdir(folder))


def test_video_status_sources(tmp_path):
    folder, names = folder_with(tmp_path)
    targets = ['en', 'es']
    only_inside = video_status(folder, 'a', names, targets, embedded.parse(video(sub('eng'))))
    assert (only_inside['source'], only_inside['embedded'], only_inside['external']) == ('embedded', ['en'], [])
    assert (only_inside['subs'], only_inside['missing'], only_inside['checked']) == (['en'], ['es'], True)
    neither = video_status(folder, 'a', names, targets, embedded.parse(video()))
    assert (neither['source'], neither['subs'], neither['missing']) == ('missing', [], ['en', 'es'])
    unchecked = video_status(folder, 'a', names, targets, None)
    assert (unchecked['source'], unchecked['checked']) == ('unchecked', False)

    folder, names = folder_with(tmp_path / 'two', ['a.en.srt'])
    only_file = video_status(folder, 'a', names, targets, embedded.parse(video()))
    assert (only_file['source'], only_file['external'], only_file['embedded']) == ('external', ['en'], [])
    both = video_status(folder, 'a', names, targets, embedded.parse(video(sub('eng'), sub('spa'))))
    assert (both['source'], both['subs'], both['missing']) == ('both', ['en', 'es'], [])
    failed = video_status(folder, 'a', names, targets, {'subs': [], 'lyrics': False, 'synced': False, 'error': 'ffprobe could not read this file'})
    # A failed scan falls back to the files next to the video: the file's English still counts.
    assert (failed['source'], failed['subs'], failed['missing'], failed['error']) == ('failed', ['en'], ['es'], 'ffprobe could not read this file')


def test_embedded_languages_count_only_when_skip_existing_is_on(tmp_path):
    folder, names = folder_with(tmp_path)
    result = embedded.parse(video(sub('eng'), sub(None)))
    off = video_status(folder, 'a', names, ['en'], result, use_embedded=False)
    assert (off['subs'], off['missing'], off['embedded']) == ([], ['en'], ['en'])        # still shown, not counted
    assert off['unknown'] == 1                                                            # "language unknown" never satisfies a target
    on = video_status(folder, 'a', names, ['en', 'fr'], result)
    assert (on['subs'], on['missing']) == (['en'], ['fr'])


def test_song_status_sources(tmp_path):
    folder = tmp_path / 'album'
    write(folder / 'S.mp3'); write(folder / 'S.lrc', b'[00:01.00]hi\n')
    tagged = embedded.parse(song({'lyrics': 'la la'}))
    bare = embedded.parse(song())
    inside = song_status(folder, 'T', [], tagged)
    assert (inside['lyrics'], inside['enhanced'], inside['lyrics_source'], inside['lyrics_embedded']) == (True, False, 'embedded', True)
    both = song_status(folder, 'S', ['S.lrc'], tagged)
    assert (both['lyrics_source'], both['lyrics_file'], both['lyrics_embedded']) == ('both', True, True)
    assert song_status(folder, 'S', ['S.lrc'], bare)['lyrics_source'] == 'external'
    assert song_status(folder, 'T', [], bare)['lyrics_source'] == 'missing' and not song_status(folder, 'T', [], bare)['lyrics']
    assert song_status(folder, 'T', [], None)['lyrics_source'] == 'unchecked'
    failed = song_status(folder, 'T', [], {'subs': [], 'lyrics': False, 'synced': False, 'error': 'x'})
    assert (failed['lyrics'], failed['lyrics_source'], failed['error']) == (False, 'failed', 'x')
    # Tags inside the file never make "enhanced" (word-by-word) lyrics.
    assert song_status(folder, 'T', [], embedded.parse(song({'lyrics': '[00:01.00]<00:01.00>hi'})))['enhanced'] is False


# ----- the saved answers -----
def test_cache_is_valid_while_size_and_time_match_and_failures_retry_later(ffprobe, tmp_path):
    answers, calls = ffprobe
    store, root = Store(tmp_path / 'config'), tmp_path / 'media'
    path = write(root / 'a.mkv')
    answers['a.mkv'] = video(sub('eng'))
    assert embedded.lookup(store, root, ['a.mkv']) == {} and calls == []            # looking up never probes
    assert embedded.languages(embedded.check(store, root, 'a.mkv')) == {'en'}
    embedded.check(store, root, 'a.mkv')
    assert calls == ['a.mkv'] and list(embedded.lookup(store, root, ['a.mkv'])) == ['a.mkv']
    embedded.check(store, root, 'a.mkv', force=True)
    assert len(calls) == 2
    path.write_bytes(b'a longer file than before')                                   # size changed
    os.utime(path, (OLD, OLD))
    assert embedded.lookup(store, root, ['a.mkv']) == {}
    embedded.check(store, root, 'a.mkv')
    assert len(calls) == 3
    os.utime(path, (OLD + 5, OLD + 5))                                               # only the modified time changed
    assert embedded.lookup(store, root, ['a.mkv']) == {}
    # A failed look is kept, so a bad file isn't probed on every page load, until its retry time.
    answers['a.mkv'] = b'junk'
    now = 1_700_000_000
    assert embedded.check(store, root, 'a.mkv', now=now)['error']
    embedded.check(store, root, 'a.mkv', now=now + embedded.RETRY_SECONDS - 1)
    assert len(calls) == 4
    answers['a.mkv'] = video(sub('spa'))
    assert embedded.languages(embedded.check(store, root, 'a.mkv', now=now + embedded.RETRY_SECONDS + 1)) == {'es'}
    assert len(calls) == 5
    assert embedded.check(store, root, 'gone.mkv')['error'] == 'the file is not readable'


def test_check_many_is_capped(ffprobe, tmp_path):
    answers, calls = ffprobe
    store, root = Store(tmp_path / 'config'), tmp_path / 'media'
    names = [f'f{i}.mkv' for i in range(embedded.MAX_PATHS + 20)]
    for name in names:
        write(root / name); answers[name] = video()
    assert len(embedded.check_many(store, root, names)) == embedded.MAX_PATHS == len(calls)


# ----- the monitor -----
def test_monitor_counts_what_is_inside(ffprobe, tmp_path):
    answers, _calls = ffprobe
    store, root = Store(tmp_path / 'config'), tmp_path / 'media'
    store.save_settings(store.settings().model_copy(update={'target_languages': ['en']}))
    for name in ('Inside.mkv', 'Nothing.mkv', 'Tagged.mp3', 'Bare.mp3'):
        write(root / name)
    answers.update({'Inside.mkv': video(sub('eng')), 'Nothing.mkv': video(sub('fra')), 'Tagged.mp3': song({'lyrics': 'la'}), 'Bare.mp3': song()})
    assert monitor.audit_library(store, root)['visited'] == 4
    _total, rows = monitor.items(store, limit=0)
    states = {r['path']: r['state'] for r in rows}
    assert states == {'Inside.mkv': 'complete', 'Nothing.mkv': 'missing', 'Tagged.mp3': 'complete', 'Bare.mp3': 'missing'}
    # With "skip existing" off only our own files count (unchanged rule), so the track inside doesn't.
    settings = store.settings().model_copy(update={'skip_existing': False})
    assert monitor.check(root / 'Inside.mkv', ['Inside.mkv'], settings, embedded.parse(video(sub('eng')))) == ('video', ['en'], [])
    on = store.settings().model_copy(update={'skip_existing': True})
    assert monitor.check(root / 'Inside.mkv', ['Inside.mkv'], on, embedded.parse(video(sub('eng')))) == ('video', [], [])
    assert monitor.check(root / 'Inside.mkv', ['Inside.mkv'], on, None) == ('video', ['en'], [])
    failed = {'subs': [], 'lyrics': False, 'synced': False, 'error': 'x'}
    assert monitor.check(root / 'Bare.mp3', ['Bare.mp3'], on, failed) == ('song', ['lyrics'], [])


# ----- through the app: Library lists, checking, coverage and Make missing -----
@pytest.fixture
def setup(tmp_path, ffprobe):
    answers, calls = ffprobe
    root = tmp_path / 'media'
    show, album = root / 'tv' / 'Show', root / 'music' / 'Album'
    for name in ('E.mkv', 'X.mkv', 'F.mkv', 'Bad.mkv'):
        write(show / name)
    write(show / 'F.es.srt', b'x')
    write(album / 'Tagged.mp3'); write(album / 'Bare.mp3')
    answers.update({'E.mkv': video(sub('eng')), 'X.mkv': video(), 'F.mkv': video(), 'Bad.mkv': b'not json',
                    'Tagged.mp3': song({'lyrics': 'la la'}), 'Bare.mp3': song()})
    app = create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': 'test-password-123'}).status_code == 200
        settings = c.get('/api/state').json()['settings']
        settings.update(target_languages=['en', 'es'], lyrics_words=False)
        assert c.post('/api/settings', json=settings).status_code == 200
        yield c, root, app.state.store, calls


def statuses(c, folder='tv/Show', api='browse'):
    return {e['name']: e.get('status') for e in c.get(f'/api/{api}?path={folder}').json()['entries']}


def test_pages_never_probe_and_checking_fills_in_the_chips(setup):
    c, root, store, calls = setup
    before = statuses(c)
    assert calls == [] and statuses(c, 'music/Album', 'music/browse') and calls == []
    assert {s['source'] for n, s in before.items() if n.endswith('.mkv')} == {'unchecked'}
    assert before['F.mkv']['external'] == ['es'] and before['F.mkv']['subs'] == ['es']      # files still show before any check
    paths = ['tv/Show/' + n for n in ('E.mkv', 'X.mkv', 'F.mkv', 'Bad.mkv')]
    r = c.post('/api/embedded/check', json={'paths': [*paths, 'tv/Show/Nope.mkv', '../x.mkv']})
    assert r.status_code == 200
    got = {Path(p).name: s for p, s in r.json()['statuses'].items()}
    assert sorted(got) == ['Bad.mkv', 'E.mkv', 'F.mkv', 'X.mkv']
    assert (got['E.mkv']['source'], got['E.mkv']['embedded'], got['E.mkv']['missing']) == ('embedded', ['en'], ['es'])
    assert (got['X.mkv']['source'], got['X.mkv']['missing']) == ('missing', ['en', 'es'])
    assert (got['F.mkv']['source'], got['F.mkv']['external']) == ('external', ['es'])
    assert got['Bad.mkv']['source'] == 'failed' and got['Bad.mkv']['error'] and got['Bad.mkv']['missing'] == ['en', 'es']
    assert sorted(calls) == ['Bad.mkv', 'E.mkv', 'F.mkv', 'X.mkv']
    after = statuses(c)
    assert after['E.mkv']['source'] == 'embedded' and after['Bad.mkv']['source'] == 'failed'
    c.post('/api/embedded/check', json={'paths': paths})
    assert len(calls) == 4                                                                     # saved: not probed again
    c.post('/api/embedded/check', json={'paths': ['tv/Show/Bad.mkv'], 'force': True})         # Retry scan
    assert len(calls) == 5
    music = c.post('/api/embedded/check', json={'paths': ['music/Album/Tagged.mp3', 'music/Album/Bare.mp3']}).json()['statuses']
    assert music['music/Album/Tagged.mp3']['lyrics_source'] == 'embedded' and music['music/Album/Bare.mp3']['lyrics'] is False
    assert statuses(c, 'music/Album', 'music/browse')['Tagged.mp3']['lyrics'] is True
    search = c.get('/api/library/search', params={'q': 'mkv'}).json()['entries']
    assert {e['name']: e['status']['source'] for e in search}['E.mkv'] == 'embedded'
    assert c.post('/api/embedded/check', json={'paths': []}).status_code == 422
    c.cookies.clear()
    assert c.post('/api/embedded/check', json={'paths': paths}).status_code == 401


def test_coverage_and_make_missing_use_what_is_inside(setup):
    c, root, store, calls = setup
    data = c.get('/api/coverage?refresh=true').json()
    assert calls == [] and data['inside'] == {'checked': 0, 'unchecked': 6, 'failed': 0, 'total': 6, 'used': True}
    assert {s['language']: s['have'] for s in data['subtitles']} == {'en': 0, 'es': 1}        # unchecked: files only
    for path in ('tv/Show/E.mkv', 'tv/Show/X.mkv', 'tv/Show/F.mkv', 'tv/Show/Bad.mkv', 'music/Album/Tagged.mp3', 'music/Album/Bare.mp3'):
        embedded.check(store, root, path)
    data = c.get('/api/coverage?refresh=true').json()
    assert data['inside'] == {'checked': 5, 'unchecked': 0, 'failed': 1, 'total': 6, 'used': True}
    assert {s['language']: s['have'] for s in data['subtitles']} == {'en': 1, 'es': 1}
    assert data['lyrics'] == {'have': 1, 'missing': 1, 'total': 2, 'percent': 50.0}
    assert data['enhanced']['have'] == 0                                                       # embedded tags are never enhanced
    r = c.post('/api/coverage/generate', json={'what': 'subtitles', 'language': 'en'}).json()
    assert r['queued'] == 3                                                                    # E has English inside; the failed file is not skipped
    jobs = [j for j in store.jobs() if j['kind'] == 'subtitle']
    assert sorted(Path(j['spec']['path']).name for j in jobs) == ['Bad.mkv', 'F.mkv', 'X.mkv']
    assert all(j['spec']['regenerate'] is False and j['spec']['audio_stream'] is None and j['spec']['settings']['target_languages'] == ['en'] for j in jobs)
    r = c.post('/api/coverage/generate', json={'what': 'lyrics'}).json()
    assert r['queued'] == 1 and [j['spec']['path'] for j in store.jobs() if j['kind'] == 'lyrics'] == ['music/Album/Bare.mp3']
    # With "skip existing" off, tracks inside don't count, so English is wanted for E as well.
    settings = c.get('/api/state').json()['settings']
    settings['skip_existing'] = False
    assert c.post('/api/settings', json=settings).status_code == 200
    data = c.get('/api/coverage?refresh=true').json()
    assert data['inside']['used'] is False and {s['language']: s['have'] for s in data['subtitles']} == {'en': 0, 'es': 1}


def test_check_inside_pass_checks_only_what_is_unchecked(setup):
    from zilcho_subs import coverage
    c, root, store, calls = setup
    embedded.check(store, root, 'tv/Show/E.mkv')
    coverage._inside_pass(store, root, None)
    assert sorted(calls).count('E.mkv') == 1 and len(calls) == 6                               # five more files, E not again
    progress = coverage.inside_progress()
    assert progress['total'] == 5 and progress['done'] == 5 and progress['failed'] == 1
    assert c.get('/api/coverage?refresh=true').json()['inside']['checked'] == 5
