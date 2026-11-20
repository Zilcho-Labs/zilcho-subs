"""Optional Jellyfin link and the status card (notification channel).

A fake Jellyfin server runs on 127.0.0.1 inside the test; nothing else is
contacted. Synthetic media only."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from fastapi.testclient import TestClient
from zilcho_subs import jellyfin
from zilcho_subs import monitor as library
from zilcho_subs.app import create_app
from zilcho_subs.core import Settings, Store

KEY = 'fake-test-key-0123456789abcdef'


class FakeJellyfin:
    """Records requests. `status` sets the answer; `redirect` answers 302."""

    def __init__(self):
        self.requests, self.status, self.redirect = [], 204, None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def answer(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                fake.requests.append({'method': self.command, 'path': self.path, 'headers': dict(self.headers),
                                      'body': json.loads(body) if body else None})
                if fake.redirect:
                    self.send_response(302); self.send_header('Location', fake.redirect); self.end_headers(); return
                if self.headers.get('X-Emby-Token') != KEY:
                    self.send_response(401); self.end_headers(); return
                if self.path == '/System/Info':
                    data = json.dumps({'ServerName': 'Test server', 'Version': '10.10.3'}).encode()
                    self.send_response(200); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
                self.send_response(fake.status); self.end_headers()

            do_GET = do_POST = answer

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()

    def posts(self, path):
        return [r for r in self.requests if r['method'] == 'POST' and r['path'] == path]


@pytest.fixture
def fake():
    server = FakeJellyfin()
    yield server
    server.close()


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'config')
    s.save_settings(Settings(stable_seconds=30))
    return s


def finished(store, path, kind='subtitle', outputs=None, status='completed'):
    spec = {'path': path, 'settings': {}, 'regenerate': False}
    job = store.enqueue(kind, spec)
    store.update(job, status=status, outputs=outputs if outputs is not None else {'en': path.rsplit('.', 1)[0] + '.en.AI.srt'})
    return job


def test_off_by_default_sends_nothing(store, fake):
    assert not jellyfin.public(store)['enabled']
    assert jellyfin.notify_new(store) == 0           # first call only sets the starting point
    finished(store, 'tv/Show/ep.mkv')
    assert jellyfin.notify_new(store) == 0
    assert jellyfin.refresh_library(store, explicit=True) is False
    assert fake.requests == []
    # Address without a key is still off.
    jellyfin.save(store, fake.url, '', '/media')
    assert not jellyfin.public(store)['enabled'] and jellyfin.notify_new(store) == 0 and fake.requests == []


def test_new_files_are_announced_once_with_jellyfins_paths(store, fake):
    jellyfin.save(store, fake.url, KEY, '/data/media')
    jellyfin.notify_new(store, now=time.time() - 60)  # starting point before the jobs finish
    finished(store, 'tv/Show/S01E01.mkv'); finished(store, 'tv/Show/S01E02.mkv')
    finished(store, 'music/Album/song.flac', 'lyrics', {'lrc': 'music/Album/song.lrc', 'txt': 'music/Album/song.txt'})
    finished(store, 'tv/Other/skip.mkv', status='skipped', outputs={})       # nothing made: not announced
    finished(store, 'tv/Exported/x.mkv', outputs={'en': 'config:exports/1/x.en.AI.srt'})  # not in the library
    assert jellyfin.notify_new(store) == 2
    [call] = fake.posts('/Library/Media/Updated')
    assert call['body'] == {'Updates': [{'Path': '/data/media/tv/Show', 'UpdateType': 'Modified'},
                                        {'Path': '/data/media/music/Album', 'UpdateType': 'Modified'}]}
    assert call['headers']['X-Emby-Token'] == KEY and 'Token="%s"' % KEY in call['headers']['Authorization']
    assert jellyfin.notify_new(store) == 0 and len(fake.posts('/Library/Media/Updated')) == 1
    assert jellyfin.public(store)['last_notified'] and not jellyfin.public(store)['last_error']


def test_jellyfin_down_is_retried_later_and_never_raises(store, fake):
    jellyfin.save(store, 'http://127.0.0.1:9', KEY)   # nothing listens on port 9
    t0 = time.time()
    jellyfin.notify_new(store, now=t0 - 60)
    finished(store, 'tv/Show/ep.mkv')
    assert jellyfin.notify_new(store, now=t0 + 1) == 0
    assert "Can't reach Jellyfin" in jellyfin.public(store)['last_error']
    # Waits before trying again; the work isn't lost.
    jellyfin.save(store, fake.url, '')               # fixed address, saved key kept
    assert jellyfin.notify_new(store, now=t0 + 2) == 0 and fake.requests == []
    assert jellyfin.notify_new(store, now=t0 + jellyfin.RETRY_AFTER + 5) == 1
    assert fake.posts('/Library/Media/Updated')[0]['body']['Updates'][0]['Path'] == '/media/tv/Show'
    assert jellyfin.public(store)['last_error'] == ''


def test_wrong_key_and_redirects(store, fake):
    jellyfin.save(store, fake.url, 'wrong-key')
    with pytest.raises(ValueError, match='refused the API key'):
        jellyfin.test(store)
    assert 'refused' in jellyfin.public(store)['last_error']
    jellyfin.save(store, fake.url, KEY)
    assert jellyfin.test(store) == {'name': 'Test server', 'version': '10.10.3'}
    assert jellyfin.public(store)['server']['name'] == 'Test server'
    # A redirect is not followed, so the key can't be sent to another address.
    other = FakeJellyfin()
    try:
        fake.redirect = other.url + '/System/Info'
        with pytest.raises(ValueError, match='redirect'):
            jellyfin.test(store)
        assert other.requests == []
    finally:
        other.close()


def test_refresh_after_full_check(store, fake):
    jellyfin.save(store, fake.url, KEY, refresh_after_audit=False)
    assert jellyfin.refresh_library(store, explicit=True) is False
    jellyfin.save(store, fake.url, '', refresh_after_audit=True)
    assert jellyfin.refresh_library(store, explicit=False, now=10_000) is True
    assert jellyfin.refresh_library(store, explicit=False, now=10_100) is False     # once an hour automatically
    assert jellyfin.refresh_library(store, explicit=True, now=10_200) is True       # a button always does
    assert len(fake.posts('/Library/Refresh')) == 2


def test_key_stays_on_the_server(tmp_path, fake):
    root = tmp_path / 'media'; root.mkdir()
    app = create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.get('/api/jellyfin').status_code == 401
        assert c.post('/api/login', json={'password': 'test-password-123'}).status_code == 200
        assert c.get('/api/jellyfin').json()['enabled'] is False
        assert c.post('/api/jellyfin', json={'url': 'ftp://nas', 'api_key': KEY}).status_code == 400
        assert c.post('/api/jellyfin', json={'url': 'http://user:pw@nas:8096', 'api_key': KEY}).status_code == 400
        assert c.post('/api/jellyfin', json={'url': fake.url, 'api_key': KEY, 'media_root': 'relative'}).status_code == 400
        saved = c.post('/api/jellyfin', json={'url': fake.url + '/', 'api_key': KEY, 'media_root': '/data/media'}).json()
        assert saved['enabled'] and saved['has_key'] and saved['url'] == fake.url
        assert c.post('/api/jellyfin/test', json={}).json()['server']['version'] == '10.10.3'
        # Saving again without a key keeps the saved one.
        assert c.post('/api/jellyfin', json={'url': fake.url, 'media_root': '/data/media', 'notify': False}).json()['has_key']
        for path in ('/api/jellyfin', '/api/state', '/api/state/audit'):
            assert KEY not in c.get(path).text
        assert c.get('/api/state/audit', params={'items': False}).json()['summary']['jellyfin']['enabled'] is True
        off = c.post('/api/jellyfin/forget', json={}).json()
        assert not off['enabled'] and not off['has_key'] and not (tmp_path / 'config' / 'jellyfin.json').exists()


def test_status_card_counts(tmp_path):
    """The notification channel: status, missing items, pending jobs, last check."""
    root = tmp_path / 'media'
    for name in ('a', 'b'): (root / 'tv').mkdir(parents=True, exist_ok=True); (root / 'tv' / f'{name}.mkv').write_bytes(b'x')
    (root / 'music').mkdir(); (root / 'music' / 's.mp3').write_bytes(b'x')
    store = Store(tmp_path / 'config'); store.save_settings(Settings(stable_seconds=30))
    library.audit_library(store, root, now=1000); library.audit_library(store, root, now=1031)
    s = library.summary(store)
    assert (s['status'], s['missing'], s['pending_jobs'], s['last_scan']) == ('off', 3, 0, None)
    store.enqueue('subtitle', {'path': 'tv/manual.mkv'})                       # a job queued by hand counts too
    library.audit_path(store, root, 'tv/a.mkv', generate=('video',))
    s = library.summary(store)
    assert (s['missing'], s['pending_jobs'], s['in_queue']) == (2, 2, 1)
    app = create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        c.post('/api/login', json={'password': 'test-password-123'})
        assert c.post('/api/audit', json={}).json()['started'] is False
        summary = c.get('/api/state/audit', params={'items': False}).json()['summary']
        assert summary['pending_jobs'] == 2 and summary['missing'] == 2 and summary['jellyfin'] == {
            'enabled': False, 'error': '', 'last_ok': None, 'last_notified': None}


def test_monitor_thread_tells_jellyfin(tmp_path, fake):
    """End to end: the background thread announces a finished job and asks for a
    rescan after a check; a broken Jellyfin doesn't stop it checking."""
    root = tmp_path / 'media'; (root / 'tv').mkdir(parents=True); (root / 'tv' / 'ep.mkv').write_bytes(b'x')
    store = Store(tmp_path / 'config'); store.save_settings(Settings(stable_seconds=30))
    jellyfin.save(store, fake.url, KEY, '/media', refresh_after_audit=True)
    jellyfin.notify_new(store, now=time.time() - 60)
    finished(store, 'tv/ep.mkv')
    m = library.Monitor(store, root)
    m.start()
    try:
        m.request()
        deadline = time.time() + 10
        while time.time() < deadline and not (fake.posts('/Library/Media/Updated') and fake.posts('/Library/Refresh')):
            time.sleep(.05)
        assert fake.posts('/Library/Media/Updated')[0]['body']['Updates'] == [{'Path': '/media/tv', 'UpdateType': 'Modified'}]
        assert len(fake.posts('/Library/Refresh')) == 1
        assert library.summary(store, m)['last_full_scan']
        # Jellyfin goes away: checks carry on.
        fake.status = 500
        before = library.summary(store, m)['last_scan']
        time.sleep(.05); m.request()
        deadline = time.time() + 10
        while time.time() < deadline and library.summary(store, m)['last_scan'] == before:
            time.sleep(.05)
        s = library.summary(store, m)
        assert s['last_scan'] > before and not s['last_error'] and 'error 500' in s['jellyfin']['error']
    finally:
        m.stop()
