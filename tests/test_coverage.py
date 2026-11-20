"""Subtitle & lyrics coverage page: percentages per language and the Generate
missing buttons. Synthetic files only; nothing runs and no media file changes."""
import os
import pytest
from fastapi.testclient import TestClient
from zilcho_subs.app import create_app

OLD = 1_600_000_000


def write(path, data=b'synthetic'):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); os.utime(path, (OLD, OLD)); return path


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'media'
    show = root / 'tv' / 'Show'
    # 4 videos: en 3/4 (forced doesn't count), es 1/4, fr 2/4.
    write(show / 'A.mkv'); write(show / 'A.en.srt', b'x'); write(show / 'A.es.AI.srt', b'x'); write(show / 'A.fr.srt', b'x')
    write(show / 'B.mkv'); write(show / 'B.eng.srt', b'x')
    write(show / 'C.mp4'); write(show / 'C.en.AI.srt', b'x'); write(show / 'C.fr.vtt', b'x')
    write(show / 'D.mkv'); write(show / 'D.en.forced.srt', b'x'); write(show / 'D.es.srt', b'')  # empty: doesn't count
    album = root / 'music' / 'Album'
    # 4 songs: lyrics 3/4, enhanced 2/4.
    write(album / 'Plain.mp3'); write(album / 'Plain.lrc', b'[00:01.00]Hello\n')
    write(album / 'Karaoke.mp3'); write(album / 'Karaoke.lrc', b'[00:01.00]<00:01.00>Hello <00:01.50>there\n')
    write(album / 'Words.flac'); write(album / 'Words.txt', b'Hello'); write(album / 'Words.words.lrc', b'[00:01.00]<00:01.00>Hi\n')
    write(album / 'Nothing.mp3')
    write(root / '.hidden' / 'Skip.mkv')
    app = create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': 'test-password-123'}).status_code == 200
        settings = c.get('/api/state').json()['settings']
        settings.update(target_languages=['en', 'es', 'de'], lyrics_words=False)
        assert c.post('/api/settings', json=settings).status_code == 200
        yield c, root, app.state.store


def test_coverage_percentages(setup):
    c, root, _store = setup
    data = c.get('/api/coverage?refresh=true').json()
    assert data['videos'] == 4 and data['songs'] == 4 and not data['truncated']
    subs = {s['language']: s for s in data['subtitles']}
    # Your languages come first, in your order; others found after them.
    assert [s['language'] for s in data['subtitles']] == ['en', 'es', 'de', 'fr']
    assert (subs['en']['have'], subs['en']['percent']) == (3, 75.0)
    assert (subs['es']['have'], subs['es']['percent']) == (1, 25.0)
    assert (subs['de']['have'], subs['de']['percent'], subs['de']['target']) == (0, 0.0, True)
    assert (subs['fr']['have'], subs['fr']['target']) == (2, False)
    assert data['lyrics'] == {'have': 3, 'missing': 1, 'total': 4, 'percent': 75.0}
    assert data['enhanced']['have'] == 2 and data['enhanced']['percent'] == 50.0
    assert '_videos' not in data and 'paths' not in str(data)


def test_empty_library_and_sign_in(setup, tmp_path):
    c, root, _store = setup
    settings = c.get('/api/state').json()['settings']
    settings['scan_dirs'] = ['nowhere']
    assert c.post('/api/settings', json=settings).status_code == 200
    data = c.get('/api/coverage?refresh=true').json()
    assert data['videos'] == 0 and data['lyrics']['percent'] is None
    c.cookies.clear()
    assert c.get('/api/coverage').status_code == 401


def test_generate_missing_language(setup):
    c, root, store = setup
    before = snapshot(root)
    r = c.post('/api/coverage/generate', json={'what': 'subtitles', 'language': 'es'}).json()
    assert r['queued'] == 3 and r['missing'] == 3 and r['coverage']['subtitles'][1]['language'] == 'es'
    jobs = [j for j in store.jobs() if j['kind'] == 'subtitle']
    assert sorted(j['spec']['path'] for j in jobs) == ['tv/Show/B.mkv', 'tv/Show/C.mp4', 'tv/Show/D.mkv']
    assert all(j['spec']['settings']['target_languages'] == ['es'] and not j['spec']['regenerate'] for j in jobs)
    # A second click doesn't queue them again.
    again = c.post('/api/coverage/generate', json={'what': 'subtitles', 'language': 'es'}).json()
    assert again['queued'] == 0 and again['already_queued'] == 3
    assert snapshot(root) == before
    assert c.post('/api/coverage/generate', json={'what': 'subtitles', 'language': 'x/../'}).status_code in (400, 422)
    assert c.post('/api/coverage/generate', json={'what': 'subtitles'}).status_code == 400
    assert c.post('/api/coverage/generate', json={'what': 'videos'}).status_code == 422


def test_generate_missing_lyrics_and_enhanced(setup):
    c, root, store = setup
    before = snapshot(root)
    r = c.post('/api/coverage/generate', json={'what': 'lyrics'}).json()
    assert r['queued'] == 1
    lyrics = [j for j in store.jobs() if j['kind'] == 'lyrics']
    assert [j['spec']['path'] for j in lyrics] == ['music/Album/Nothing.mp3'] and not lyrics[0]['spec']['regenerate']
    r = c.post('/api/coverage/generate', json={'what': 'enhanced'}).json()
    # Nothing.mp3's pending job doesn't make word timing (setting off), so it gets its own.
    assert r['queued'] == 2 and r['missing'] == 2
    enhanced = {j['spec']['path']: j['spec'] for j in store.jobs() if j['kind'] == 'lyrics' and j['spec']['settings']['lyrics_words']}
    assert set(enhanced) == {'music/Album/Plain.mp3', 'music/Album/Nothing.mp3'}
    assert enhanced['music/Album/Plain.mp3']['regenerate'] is True       # has Song.lrc already
    assert enhanced['music/Album/Nothing.mp3']['regenerate'] is False
    assert snapshot(root) == before
