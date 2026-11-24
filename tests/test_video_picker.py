"""Video picker for Translate subtitles (1.5.0): the page has Browse, /api/browse lists videos,
and /api/subtitles checks the attached video on the server. Synthetic files only."""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zilcho_subs.app import create_app

PASSWORD = 'test-password-123'
STATIC = Path(__file__).resolve().parents[1] / 'zilcho_subs' / 'static'
SRT = '\n'.join(['1', '00:00:01,250 --> 00:00:04,500', 'Hello world.', ''])


def fake_pack(config, src, dst):
    folder = config / 'packages' / f'{src}_{dst}'; folder.mkdir(parents=True, exist_ok=True)
    (folder / 'metadata.json').write_text(json.dumps({'from_code': src, 'to_code': dst, 'from_name': src, 'to_name': dst}), encoding='utf-8')


@pytest.fixture
def env(tmp_path):
    root = tmp_path / 'media'
    for rel in ['tv/Show/A.mkv', 'tv/Show/B.mp4', 'tv/Show/notes.txt', 'other/Out.mkv']:
        p = root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'synthetic')
    (root / 'tv' / 'Show' / 'A.en.srt').write_text(SRT, encoding='utf-8')
    (root / 'tv' / 'Empty').mkdir()
    app = create_app(tmp_path / 'config', root, PASSWORD, run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': PASSWORD}).status_code == 200
        fake_pack(app.state.store.config, 'en', 'es')
        yield c, root, app.state.store


def body(**extra):
    return {'text': SRT, 'filename': 'x.en.srt', 'source': 'en', 'targets': ['es'], **extra}


def test_page_has_browse_and_dialog():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    for needle in ['id="srt-video-browse"', 'id="srt-video-clear"', 'id="video-picker"', 'id="video-list"', '/static/videopicker.js']:
        assert needle in html
    assert 'id="srt-video"' in html   # manual entry stays


def test_chosen_video_is_stored_on_its_own_job(env):
    c, root, store = env
    a = c.post('/api/subtitles', json=body(video_path='tv/Show/A.mkv')); b = c.post('/api/subtitles', json=body(filename='y.en.srt', text=SRT.replace('Hello', 'Hi'), video_path='tv/Show/B.mp4'))
    n = c.post('/api/subtitles', json=body(filename='z.en.srt', text=SRT.replace('Hello', 'Yo')))
    assert a.status_code == b.status_code == n.status_code == 200
    assert store.job(a.json()['id'])['spec']['video_path'] == 'tv/Show/A.mkv'
    assert store.job(b.json()['id'])['spec']['video_path'] == 'tv/Show/B.mp4'
    assert store.job(n.json()['id'])['spec']['video_path'] is None


@pytest.mark.parametrize('bad', ['../x.mkv', '/etc/passwd', 'tv/Show/notes.txt', 'tv/Show/missing.mkv', 'tv/Show', 'tv/Show/A.en.srt'])
def test_bad_video_paths_rejected(env, bad):
    c, root, store = env
    assert c.post('/api/subtitles', json=body(video_path=bad)).status_code == 400


def test_symlinked_video_rejected(env):
    c, root, store = env
    link = root / 'tv' / 'Show' / 'Link.mkv'
    try: os.symlink(root / 'tv' / 'Show' / 'A.mkv', link)
    except (OSError, NotImplementedError): pytest.skip('symlinks not available')
    assert c.post('/api/subtitles', json=body(video_path='tv/Show/Link.mkv')).status_code == 400


def test_video_outside_every_collection_rejected(env):
    c, root, store = env
    # The default collection covers the whole media folder; narrow it to tv only.
    default = c.get('/api/libraries').json()['libraries'][0]
    assert c.put(f"/api/libraries/{default['id']}", json={'name': default['name'], 'folders': ['tv']}).status_code == 200
    assert c.post('/api/subtitles', json=body(video_path='other/Out.mkv')).status_code == 400
    assert c.post('/api/subtitles', json=body(video_path='tv/Show/A.mkv')).status_code == 200


def test_browse_lists_videos_and_empty_folder(env):
    c, root, store = env
    listing = c.get('/api/browse', params={'path': 'tv/Show'}).json()
    names = {e['name'] for e in listing['entries'] if not e['directory'] and not e['subtitle']}
    assert names == {'A.mkv', 'B.mp4'}
    assert [e for e in c.get('/api/browse', params={'path': 'tv/Empty'}).json()['entries']] == []
    assert c.get('/api/browse', params={'path': 'nope'}).status_code == 400
