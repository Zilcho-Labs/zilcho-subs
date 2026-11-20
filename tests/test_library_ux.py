"""List pages (0.9.0): file status on Media library and Song lyrics, Monitor
search/filter/paging, and Queue selected. Synthetic files only; nothing runs."""
import os
import pytest
from fastapi.testclient import TestClient
from zilcho_subs.app import create_app
from zilcho_subs.status import fuzzy_rank, song_status

OLD = 1_600_000_000  # files last changed long ago count as settled


def write(path, data=b'synthetic'):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); os.utime(path, (OLD, OLD)); return path


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'media'
    show = root / 'tv' / 'Show'
    write(show / 'Cat.mkv'); write(show / 'Cat.en.srt', b'1\n00:00:00,000 --> 00:00:01,000\nhi\n'); write(show / 'Cat.es.AI.srt', b'x')
    write(show / 'Core.mkv'); write(show / 'Core.en.forced.srt', b'x')
    write(show / 'Clip.mkv')
    write(show / 'Dog.mkv'); write(show / 'Dog.en.srt', b'x')
    album = root / 'music' / 'Album'
    write(album / 'Plain.mp3'); write(album / 'Plain.lrc', b'[00:01.00]Hello\n')
    write(album / 'Karaoke.mp3'); write(album / 'Karaoke.lrc', b'[00:01.00]<00:01.00>Hello <00:01.50>there\n')
    write(album / 'Words.flac'); write(album / 'Words.txt', b'Hello'); write(album / 'Words.words.lrc', b'[00:01.00]<00:01.00>Hi\n')
    write(album / 'Nothing.mp3')
    app = create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': 'test-password-123'}).status_code == 200
        settings = c.get('/api/state').json()['settings']
        settings.update(target_languages=['en', 'es'], stable_seconds=30)
        assert c.post('/api/settings', json=settings).status_code == 200
        yield c, root


def test_fuzzy_rank_matches_the_examples():
    names = ['Cat', 'Core', 'Clip', 'Dog']
    assert [n for n in names if fuzzy_rank('C', n) is not None] == ['Cat', 'Core', 'Clip']
    assert [n for n in names if fuzzy_rank('Co', n) is not None] == ['Core']
    assert fuzzy_rank('co', 'Core') < fuzzy_rank('or', 'Core') < fuzzy_rank('cr', 'Core')
    assert fuzzy_rank('show', 'Cat.mkv', 'tv/Show/Cat.mkv') == 2       # a folder name matches
    assert fuzzy_rank('', 'anything') == 0 and fuzzy_rank('zz', 'Core') is None


def test_media_library_shows_subtitle_status(setup):
    c, _root = setup
    data = c.get('/api/browse?path=tv/Show').json()
    assert data['targets'] == ['en', 'es']
    status = {e['name']: e.get('status') for e in data['entries']}
    assert status['Cat.mkv'] == {'subs': ['en', 'es'], 'missing': []}
    assert status['Core.mkv'] == {'subs': [], 'missing': ['en', 'es']}     # forced doesn't count
    assert status['Dog.mkv'] == {'subs': ['en'], 'missing': ['es']}
    assert status['Cat.en.srt'] is None                                    # subtitle files have no status


def test_song_lyrics_shows_lyric_and_enhanced_status(setup):
    c, root = setup
    status = {e['name']: e['status'] for e in c.get('/api/music/browse?path=music/Album').json()['entries']}
    assert status['Plain.mp3']['lyrics'] and not status['Plain.mp3']['enhanced']
    assert status['Karaoke.mp3']['enhanced']                                # word times inside Song.lrc
    assert status['Words.flac'] == {'lyrics': True, 'enhanced': True, 'files': ['Words.txt', 'Words.words.lrc']}
    assert status['Nothing.mp3'] == {'lyrics': False, 'enhanced': False, 'files': []}
    assert song_status(root / 'music' / 'Album', 'Missing', []) == {'lyrics': False, 'enhanced': False, 'files': []}


def test_queue_selected_videos_and_songs(setup):
    c, root = setup
    before = snapshot(root)
    single = c.post('/api/jobs', json={'path': 'tv/Show/Clip.mkv'}).json()['id']
    r = c.post('/api/jobs/batch', json={'paths': ['tv/Show/Clip.mkv', 'tv/Show/Core.mkv', 'tv/Show/Core.mkv', 'tv/Show/Nope.mkv', '../x.mkv']}).json()
    assert r['count'] == 2 and r['ids'][0] == single                       # same job as Queue subtitles, not a duplicate
    assert [e['path'] for e in r['errors']] == ['tv/Show/Nope.mkv', '../x.mkv']
    r = c.post('/api/lyrics/batch', json={'paths': ['music/Album/Nothing.mp3', 'music/Album/Plain.mp3', 'tv/Show/Clip.mkv']}).json()
    assert r['count'] == 2 and len(r['errors']) == 1
    jobs = c.get('/api/state').json()['jobs']
    assert sorted((j['kind'], j['spec']['path']) for j in jobs) == [('lyrics', 'music/Album/Nothing.mp3'), ('lyrics', 'music/Album/Plain.mp3'),
                                                                    ('subtitle', 'tv/Show/Clip.mkv'), ('subtitle', 'tv/Show/Core.mkv')]
    assert all(not j['spec']['regenerate'] for j in jobs)
    assert c.post('/api/jobs/batch', json={'paths': []}).status_code == 422
    assert snapshot(root) == before                                        # queueing never touches media


def test_monitor_search_filter_pages_and_queue_selected(setup):
    c, root = setup
    before = snapshot(root)
    assert c.post('/api/audit', json={'generate': False}).status_code == 200
    get = lambda **q: c.get('/api/state/audit', params=q).json()
    every = get(limit=0)
    assert every['total'] == 8 and len(every['items']) == 8
    names = lambda d: [i['path'].rsplit('/', 1)[-1] for i in d['items']]
    assert set(names(get(q='C', kind='video'))) == {'Cat.mkv', 'Core.mkv', 'Clip.mkv'}
    assert names(get(q='Co')) == ['Core.mkv']
    # Missing a language / missing lyrics (skip_existing is on by default: the user's own files count)
    assert set(names(get(missing='es'))) == {'Core.mkv', 'Clip.mkv', 'Dog.mkv'}
    assert names(get(missing='lyrics')) == ['Nothing.mp3']
    assert c.get('/api/state/audit', params={'missing': 'e%'}).status_code == 400
    cat = next(i for i in every['items'] if i['path'].endswith('Cat.mkv'))
    assert cat['status'] == {'subs': ['en', 'es'], 'missing': []}
    karaoke = next(i for i in every['items'] if i['path'].endswith('Karaoke.mp3'))
    assert karaoke['status']['lyrics'] and karaoke['status']['enhanced']
    # Pages and sorting
    page1, page2 = get(limit=3, offset=0, sort='name'), get(limit=3, offset=3, sort='name')
    assert page1['total'] == 8 and len(page1['items']) == 3 and not set(names(page1)) & set(names(page2))
    assert names(get(sort='name_desc', limit=1)) == ['Dog.mkv']
    # Select all = only files that can be queued
    picked = get(paths=True, kind='video')['paths']
    assert sorted(p.rsplit('/', 1)[-1] for p in picked) == ['Clip.mkv', 'Core.mkv', 'Dog.mkv']  # Cat is complete
    r = c.post('/api/audit/batch', json={'paths': ['tv/Show/Clip.mkv', 'music/Album/Nothing.mp3', 'music/Album/Plain.mp3']}).json()
    assert r['queued'] == 2 and r['errors'] == []                          # Plain.mp3 already has lyrics
    jobs = c.get('/api/state').json()['jobs']
    assert sorted(j['kind'] for j in jobs) == ['lyrics', 'subtitle'] and all(not j['spec']['regenerate'] for j in jobs)
    assert names(get(state='queued')) and set(names(get(state='queued'))) == {'Clip.mkv', 'Nothing.mp3'}
    assert snapshot(root) == before
