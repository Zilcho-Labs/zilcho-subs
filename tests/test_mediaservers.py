"""Optional Emby and Plex links (Jellyfin has its own tests in test_jellyfin.py).

Fake Emby and Plex servers run on 127.0.0.1 inside the test; nothing else is
contacted. Synthetic media only."""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from fastapi.testclient import TestClient
from zilcho_subs import mediaserver
from zilcho_subs import monitor as library
from zilcho_subs.app import create_app
from zilcho_subs.core import Settings, Store

KEY = 'fake-test-token-0123456789'


class FakeServer:
    """Fake Emby (kind='emby') or Plex (kind='plex'). Records every request."""

    def __init__(self, kind):
        self.kind, self.requests, self.status = kind, [], 200
        self.sections = [{'key': '1', 'type': 'show', 'title': 'TV', 'Location': [{'path': '/data/tv'}]},
                         {'key': '2', 'type': 'artist', 'title': 'Music', 'Location': [{'path': '/data/music'}, {'path': '/data/more-music'}]}]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def send_json(self, value):
                data = json.dumps(value).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

            def answer(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                url = urllib.parse.urlsplit(self.path)
                fake.requests.append({'method': self.command, 'path': url.path, 'query': dict(urllib.parse.parse_qsl(url.query)),
                                      'headers': dict(self.headers), 'body': json.loads(body) if body else None})
                token = self.headers.get('X-Plex-Token' if fake.kind == 'plex' else 'X-Emby-Token')
                if token != KEY:
                    self.send_response(401); self.end_headers(); return
                if fake.kind == 'emby' and url.path == '/emby/System/Info':
                    return self.send_json({'ServerName': 'Test Emby', 'Version': '4.8.10.0'})
                if fake.kind == 'plex' and url.path == '/':
                    return self.send_json({'MediaContainer': {'friendlyName': 'Test Plex', 'version': '1.41.3.9314'}})
                if fake.kind == 'plex' and url.path == '/library/sections':
                    return self.send_json({'MediaContainer': {'Directory': fake.sections}})
                self.send_response(fake.status); self.send_header('Content-Length', '0'); self.end_headers()

            do_GET = do_POST = do_PUT = answer

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()

    def hits(self, path, method=None):
        return [r for r in self.requests if r['path'] == path and (method is None or r['method'] == method)]


@pytest.fixture
def emby():
    server = FakeServer('emby'); yield server; server.close()


@pytest.fixture
def plex():
    server = FakeServer('plex'); yield server; server.close()


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'config')
    s.save_settings(Settings(stable_seconds=30))
    return s


def finished(store, path, kind='subtitle'):
    job = store.enqueue(kind, {'path': path, 'settings': {}, 'regenerate': False})
    out = {'en': path.rsplit('.', 1)[0] + '.en.AI.srt'} if kind == 'subtitle' else {'lrc': path.rsplit('.', 1)[0] + '.lrc'}
    store.update(job, status='completed', outputs=out)
    return job


@pytest.mark.parametrize('kind', ['emby', 'plex'])
def test_off_by_default_sends_nothing(store, kind):
    assert not mediaserver.public(kind, store)['enabled']
    assert mediaserver.notify_new(kind, store) == 0
    finished(store, 'tv/Show/E01.mkv')
    assert mediaserver.notify_new(kind, store) == 0
    assert mediaserver.refresh_library(kind, store, explicit=True) is False
    assert not (store.config / ('%s.json' % kind)).exists()


def test_emby_is_told_about_new_folders(store, emby):
    mediaserver.save('emby', store, emby.url, KEY, '/mnt/media')
    assert mediaserver.test('emby', store) == {'name': 'Test Emby', 'version': '4.8.10.0'}
    mediaserver.notify_new('emby', store, now=time.time() - 60)
    finished(store, 'tv/Show/E01.mkv'); finished(store, 'tv/Show/E02.mkv'); finished(store, 'music/Band/Song.flac', 'lyrics')
    assert mediaserver.notify_new('emby', store) == 2
    [post] = emby.hits('/emby/Library/Media/Updated', 'POST')
    assert post['body'] == {'Updates': [{'Path': '/mnt/media/tv/Show', 'UpdateType': 'Modified'},
                                        {'Path': '/mnt/media/music/Band', 'UpdateType': 'Modified'}]}
    assert post['headers']['X-Emby-Token'] == KEY and 'Token=' not in post['headers'].get('X-Emby-Authorization', '')
    assert mediaserver.notify_new('emby', store) == 0 and len(emby.hits('/emby/Library/Media/Updated')) == 1
    mediaserver.save('emby', store, emby.url, '', '/mnt/media', refresh_after_audit=True)
    assert mediaserver.refresh_library('emby', store, explicit=True) is True
    assert emby.hits('/emby/Library/Refresh', 'POST')


def test_plex_scans_just_the_changed_folders(store, plex):
    mediaserver.save('plex', store, plex.url, KEY, '/data')
    assert mediaserver.test('plex', store) == {'name': 'Test Plex', 'version': '1.41.3.9314'}
    assert mediaserver.public('plex', store)['server']['name'] == 'Test Plex'
    mediaserver.notify_new('plex', store, now=time.time() - 60)
    finished(store, 'tv/Show/Season 1/E01.mkv'); finished(store, 'more-music/Band/Song.flac', 'lyrics')
    finished(store, 'downloads/Clip.mkv')    # not inside any Plex library: skipped
    assert mediaserver.notify_new('plex', store) == 2
    scans = sorted((r['path'], r['query'].get('path')) for r in plex.hits('/library/sections/1/refresh') + plex.hits('/library/sections/2/refresh'))
    assert scans == [('/library/sections/1/refresh', '/data/tv/Show/Season 1'), ('/library/sections/2/refresh', '/data/more-music/Band')]
    assert all(r['headers']['X-Plex-Token'] == KEY for r in plex.requests)
    assert not mediaserver.public('plex', store)['last_error']
    # Full rescan option
    mediaserver.save('plex', store, plex.url, '', '/data', refresh_after_audit=True)
    assert mediaserver.refresh_library('plex', store, explicit=True) is True
    assert plex.hits('/library/sections/all/refresh')


def test_plex_many_folders_scan_the_library_once(store, plex):
    mediaserver.save('plex', store, plex.url, KEY, '/data')
    mediaserver.notify_new('plex', store, now=time.time() - 60)
    for n in range(mediaserver.PLEX_SECTION_SCAN + 5):
        finished(store, 'tv/Show %02d/E01.mkv' % n)
    assert mediaserver.notify_new('plex', store) == mediaserver.PLEX_SECTION_SCAN + 5
    scans = plex.hits('/library/sections/1/refresh')
    assert len(scans) == 1 and 'path' not in scans[0]['query']


def test_plex_wrong_folder_says_so(store, plex):
    mediaserver.save('plex', store, plex.url, KEY, '/media')   # Plex uses /data, so nothing matches
    mediaserver.notify_new('plex', store, now=time.time() - 60)
    finished(store, 'tv/Show/E01.mkv')
    assert mediaserver.notify_new('plex', store) == 0
    assert 'None of the new files are inside a Plex library' in mediaserver.public('plex', store)['last_error']
    assert not [r for r in plex.requests if r['path'].endswith('/refresh')]
    assert mediaserver.notify_new('plex', store) == 0 and len(plex.hits('/library/sections')) == 1   # not retried forever
    mediaserver.save('plex', store, plex.url, '', '/data')                 # fixed: the old message goes away
    assert mediaserver.public('plex', store)['last_error'] == ''


def test_wrong_token_and_server_down(store, plex):
    mediaserver.save('plex', store, plex.url, 'wrong-token')
    with pytest.raises(ValueError, match='Plex refused the token'):
        mediaserver.test('plex', store)
    mediaserver.save('plex', store, 'http://127.0.0.1:9', KEY, '/data')
    t0 = time.time()
    mediaserver.notify_new('plex', store, now=t0 - 60)
    finished(store, 'tv/Show/E01.mkv')
    assert mediaserver.notify_new('plex', store, now=t0 + 1) == 0
    assert "Can't reach Plex" in mediaserver.public('plex', store)['last_error']
    mediaserver.save('plex', store, plex.url, '', '/data')
    assert mediaserver.notify_new('plex', store, now=t0 + 2) == 0               # waits before retrying
    assert mediaserver.notify_new('plex', store, now=t0 + mediaserver.RETRY_AFTER + 5) == 1
    assert mediaserver.public('plex', store)['last_error'] == ''


def test_keys_stay_on_the_server_and_api(tmp_path, plex, emby):
    root = tmp_path / 'media'; root.mkdir()
    client = TestClient(create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False))
    client.headers['Origin'] = 'http://testserver'
    assert client.get('/api/servers').status_code == 401
    assert client.post('/api/login', json={'password': 'test-password-123'}).status_code == 200
    assert [s['kind'] for s in client.get('/api/servers').json()] == ['jellyfin', 'emby', 'plex']
    saved = client.post('/api/servers/plex', json={'url': plex.url, 'api_key': KEY, 'media_root': '/data'}).json()
    assert saved['enabled'] and saved['has_key'] and KEY not in json.dumps(saved)
    assert client.post('/api/servers/plex/test', json={}).json()['server']['name'] == 'Test Plex'
    client.post('/api/servers/emby', json={'url': emby.url, 'api_key': KEY})
    everything = json.dumps(client.get('/api/servers').json()) + json.dumps(client.get('/api/state/audit').json())
    assert KEY not in everything
    assert client.get('/api/servers/kodi').status_code == 404
    assert client.post('/api/servers/plex', json={'url': 'ftp://x', 'api_key': KEY}).status_code >= 400
    off = client.post('/api/servers/plex/forget', json={}).json()
    assert not off['enabled'] and not (tmp_path / 'config' / 'plex.json').exists()
    assert client.get('/api/servers/emby').json()['enabled']        # each server is separate


def test_one_server_down_does_not_stop_the_others(tmp_path, emby):
    media = tmp_path / 'media'; (media / 'tv' / 'Show').mkdir(parents=True)
    store = Store(tmp_path / 'config'); store.save_settings(Settings(stable_seconds=30))
    mediaserver.save('plex', store, 'http://127.0.0.1:9', KEY)     # down
    mediaserver.save('emby', store, emby.url, KEY, '/media')
    for kind in mediaserver.KINDS:
        mediaserver.notify_new(kind, store, now=time.time() - 60)
    finished(store, 'tv/Show/E01.mkv')
    mediaserver.notify_all(store)
    assert emby.hits('/emby/Library/Media/Updated')
    card = {s['kind']: s for s in library.summary(store)['servers']}
    assert card['emby']['enabled'] and not card['emby']['error']
    assert card['plex']['enabled'] and "Can't reach Plex" in card['plex']['error']
    assert not card['jellyfin']['enabled']
