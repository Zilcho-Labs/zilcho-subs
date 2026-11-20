"""Monitor library: audit, missing-content detection, queueing rules, the
indicator summary, the API and a smoke test of the background thread.
Synthetic media and fake inference only."""
import time
import pytest
from fastapi.testclient import TestClient
from zilcho_subs import monitor as library
from zilcho_subs.app import create_app
from zilcho_subs.core import Settings, Store
from zilcho_subs.pipeline import process_subtitle

INFO = {'duration': 10, 'streams': [{'index': 0, 'codec_type': 'video'}, {'index': 1, 'codec_type': 'audio', 'tags': {'language': 'jpn'}}]}


class FakeEngine:
    def speech(self, audio, settings, task, progress):
        progress(5); return {'language': 'ja', 'segments': [{'start': 0, 'end': 3, 'text': 'Hello'}]}
    def translate(self, text, source, target): return target + ': ' + text


def extract_fake(media, index, out, duration): out.write_bytes(b'fake audio')


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / 'media'
    (root / 'tv' / 'Show').mkdir(parents=True)
    (root / 'music' / 'Album').mkdir(parents=True)
    store = Store(tmp_path / 'config')
    store.save_settings(Settings(stable_seconds=30))
    return store, root


def write(path, data=b'synthetic'):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); return path


def settle(store, root, **kw):
    """Two passes 31 s apart: the first sees the files, the second trusts them."""
    library.audit_library(store, root, now=1000, **kw)
    return library.audit_library(store, root, now=1031, **kw)


def states(store):
    with store.db() as db: return {r['path']: r['state'] for r in db.execute('SELECT path,state FROM audit')}


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}


def test_detects_missing_subtitles_and_lyrics(lib):
    store, root = lib
    show = root / 'tv' / 'Show'
    write(show / 'S01E01.mkv'); write(show / 'S01E01.en.AI.srt', b'1\n00:00:00,000 --> 00:00:01,000\nhi\n')
    write(show / 'S01E02.mkv'); write(show / 'S01E02.en.AI.2.srt', b'x')     # a later version counts
    write(show / 'S01E03.mkv'); write(show / 'S01E03.en.srt', b'x')          # the user's own file
    write(show / 'S01E04.mkv'); write(show / 'S01E04.en.forced.srt', b'x')   # forced only: still missing
    write(show / 'S01E05.mkv'); write(show / 'S01E05.en.srt', b'')           # empty: still missing
    write(show / 'S01E06.mkv')
    album = root / 'music' / 'Album'
    for name, side in [('a', '.lrc'), ('b', '.txt'), ('c', '.words.lrc'), ('d', '.AI.lrc'), ('e', None)]:
        write(album / (name + '.flac'))
        if side: write(album / (name + side), b'[00:01.00]la')
    write(root / 'notes.txt'); write(root / '.hidden' / 'x.mkv')
    assert settle(store, root)['queued'] == []   # audit only
    found = states(store)
    assert found == {'tv/Show/S01E01.mkv': 'complete', 'tv/Show/S01E02.mkv': 'complete', 'tv/Show/S01E03.mkv': 'complete',
                     'tv/Show/S01E04.mkv': 'missing', 'tv/Show/S01E05.mkv': 'missing', 'tv/Show/S01E06.mkv': 'missing',
                     'music/Album/a.flac': 'complete', 'music/Album/b.flac': 'complete', 'music/Album/c.flac': 'complete',
                     'music/Album/d.flac': 'complete', 'music/Album/e.flac': 'missing'}
    row = library.item(store, 'tv/Show/S01E06.mkv')
    assert row['kind'] == 'video' and row['missing'] == ['en']
    assert library.item(store, 'music/Album/e.flac')['missing'] == ['lyrics']
    # Without skip_existing only Zilcho Subs' own .AI files count.
    store.save_settings(Settings(stable_seconds=30, skip_existing=False))
    library.audit_library(store, root, now=1100)
    assert states(store)['tv/Show/S01E03.mkv'] == 'missing' and states(store)['tv/Show/S01E01.mkv'] == 'complete'


def test_waits_for_files_to_settle(lib):
    store, root = lib
    video = write(root / 'tv' / 'new.mkv')
    assert library.audit_library(store, root, now=1000, generate=('video',))['queued'] == []
    assert states(store)['tv/new.mkv'] == 'waiting'
    assert library.audit_library(store, root, now=1020, generate=('video',))['queued'] == []
    video.write_bytes(b'still copying')   # changed: the clock starts again
    assert library.audit_library(store, root, now=1040, generate=('video',))['queued'] == []
    assert library.audit_library(store, root, now=1060, generate=('video',))['queued'] == []
    assert len(library.audit_library(store, root, now=1071, generate=('video',))['queued']) == 1


def test_old_files_count_as_settled(lib):
    store, root = lib
    import os
    old = write(root / 'tv' / 'old.mkv'); os.utime(old, (5000, 5000))
    fresh = write(root / 'tv' / 'fresh.mkv'); os.utime(fresh, (9990, 9990))
    queued = library.audit_library(store, root, now=10000, generate=('video',))['queued']
    assert [store.job(i)['spec']['path'] for i in queued] == ['tv/old.mkv']
    assert states(store)['tv/fresh.mkv'] == 'waiting'


def test_queues_missing_once_and_never_regenerates(lib):
    store, root = lib
    write(root / 'tv' / 'a.mkv'); write(root / 'tv' / 'b.mkv'); write(root / 'tv' / 'b.en.AI.srt', b'x')
    write(root / 'music' / 'song.mp3')
    queued = settle(store, root, generate=('video',))['queued']
    assert len(queued) == 1   # songs need auto lyrics; b already has subtitles
    job = store.job(queued[0])
    assert job['kind'] == 'subtitle' and job['spec']['path'] == 'tv/a.mkv' and job['spec']['regenerate'] is False
    assert states(store)['tv/a.mkv'] == 'queued'
    assert library.audit_library(store, root, now=1100, generate=('video',))['queued'] == []   # already queued
    lyrics = library.audit_library(store, root, now=1100, generate=('video', 'song'))['queued']
    assert [store.job(i)['kind'] for i in lyrics] == ['lyrics'] and store.job(lyrics[0])['spec']['regenerate'] is False
    # The job finishes as "skipped" (a track inside the file): done, never queued again.
    store.update(queued[0], status='skipped')
    assert library.audit_library(store, root, now=1200, generate=('video',))['queued'] == []
    assert states(store)['tv/a.mkv'] == 'complete'
    # Changing a setting that changes the result allows one new attempt.
    store.save_settings(Settings(stable_seconds=30, target_languages=['en', 'es']))
    again = library.audit_library(store, root, now=1300, generate=('video',))['queued']
    assert {store.job(i)['spec']['path'] for i in again} == {'tv/a.mkv', 'tv/b.mkv'}


def test_failed_jobs_wait_for_a_person(lib):
    store, root = lib
    write(root / 'tv' / 'a.mkv')
    [job] = settle(store, root, generate=('video',))['queued']
    store.update(job, status='failed', error='boom')
    assert library.audit_library(store, root, now=1100, generate=('video',))['queued'] == []
    assert states(store)['tv/a.mkv'] == 'failed'
    [retry] = library.audit_library(store, root, now=1200, generate=('video',), retry=True)['queued']
    assert retry != job and states(store)['tv/a.mkv'] == 'queued'


def test_end_to_end_with_fake_inference_never_touches_existing_files(lib):
    store, root = lib
    show = root / 'tv'
    write(show / 'mine.mkv'); write(show / 'mine.en.srt', b'my own subtitles')
    write(show / 'new.mkv', b'synthetic new episode')
    before = snapshot(root)
    [job_id] = settle(store, root, generate=('video',))['queued']
    assert snapshot(root) == before   # auditing reads only
    job = store.claim()
    assert job['id'] == job_id
    process_subtitle(store, job, root, FakeEngine(), lambda _: INFO, extract_fake)
    after = snapshot(root)
    assert {k: v for k, v in after.items() if k in before} == before   # nothing existing changed
    assert set(after) - set(before) == {'tv/new.en.AI.srt'}
    library.audit_library(store, root, now=1100, generate=('video',))
    assert states(store) == {'tv/mine.mkv': 'complete', 'tv/new.mkv': 'complete'}
    assert library.summary(store)['generated'] == 1


def test_removed_files_leave_the_audit(lib):
    store, root = lib
    gone = write(root / 'tv' / 'gone.mkv'); write(root / 'tv' / 'stay.mkv')
    settle(store, root)
    gone.unlink()
    library.audit_library(store, root, now=1100)
    assert set(states(store)) == {'tv/stay.mkv'}
    store.save_settings(Settings(stable_seconds=30, scan_dirs=['music']))   # out of scope now
    library.audit_library(store, root, now=1200)
    assert states(store) == {}


def test_large_library_in_slices(lib):
    store, root = lib
    for i in range(7): write(root / 'tv' / f'{i}.mkv')
    for now in (1000, 1000, 1000, 1000):
        library.audit_library(store, root, now=now, limit=2)
    assert len(states(store)) == 7
    queued = []
    for _ in range(4): queued += library.audit_library(store, root, now=1040, limit=2, generate=('video',))['queued']
    assert len(queued) == 7


def test_old_watcher_state_is_respected(lib):
    store, root = lib
    write(root / 'tv' / 'old.mkv')
    assert not library.scan_once(store, root, now=1000) and len(library.scan_once(store, root, now=1031)) == 1
    # Simulate an upgrade: the old scanner had queued it (seen.signature), the audit table is new.
    with store.db() as db:
        stat = (root / 'tv' / 'old.mkv').stat()
        db.execute('DELETE FROM audit'); db.execute('DELETE FROM jobs')
        db.execute('INSERT OR REPLACE INTO seen VALUES(?,?,?,?,?)', ('tv/old.mkv', stat.st_size, stat.st_mtime_ns, 1000, library._legacy_signature(store.settings())))
    assert library.audit_library(store, root, now=1100, generate=('video',))['queued'] == []


def test_summary_and_dismiss(lib):
    store, root = lib
    for name in 'abc': write(root / 'tv' / f'{name}.mkv')
    ids = settle(store, root, generate=('video',))['queued']
    s = library.summary(store, now=2000)
    assert s['status'] == 'off' and s['in_queue'] == 3 and s['counts']['video']['queued'] == 3
    store.update(ids[0], status='completed'); store.update(ids[1], status='completed'); store.update(ids[2], status='failed')
    s = library.summary(store)
    assert (s['generated'], s['failed'], s['in_queue']) == (2, 1, 0)
    # The list and its counts follow the jobs before the next check runs.
    assert s['counts']['video']['complete'] == 2 and s['counts']['video']['failed'] == 1
    assert library.items(store, state='failed')[1][0]['path'] == 'tv/c.mkv'
    assert library.item(store, 'tv/a.mkv')['state'] == 'complete'
    library.dismiss(store)
    s = library.summary(store)
    assert (s['generated'], s['failed']) == (0, 0)
    store.save_settings(Settings(watcher_enabled=True))
    assert library.summary(store)['status'] == 'watching'
    store.save_settings(Settings(watcher_enabled=True, paused=True))
    assert library.summary(store)['status'] == 'paused'


def test_api(tmp_path):
    root = tmp_path / 'media'; write(root / 'tv' / 'ep.mkv'); write(root / 'music' / 'song.mp3')
    app = create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.get('/api/state/audit').status_code == 401
        assert c.post('/api/login', json={'password': 'test-password-123'}).status_code == 200
        data = c.get('/api/state/audit').json()
        assert data['summary']['status'] == 'off' and data['total'] == 0
        assert c.post('/api/audit', json={}).json() == {'started': False, 'queued': []}
        assert {i['state'] for i in c.get('/api/state/audit').json()['items']} == {'waiting'}
        # One file on request: no settle wait, and it queues.
        r = c.post('/api/audit', json={'path': 'music/song.mp3', 'generate': True, 'kinds': ['song']}).json()
        assert len(r['queued']) == 1 and r['item']['state'] == 'queued'
        assert c.get('/api/state/audit', params={'kind': 'song'}).json()['items'][0]['job_status'] == 'queued'
        assert c.get('/api/state/audit', params={'items': False}).json().keys() == {'summary'}
        assert c.post('/api/audit', json={'path': '../secret.mkv'}).status_code == 400
        assert c.post('/api/audit', json={'kinds': ['photo']}).status_code == 400
        assert c.get('/api/state/audit', params={'state': 'bogus'}).status_code == 400
        assert c.post('/api/audit/dismiss', json={}).json() == {'ok': True}
        # The Settings page keeps the monitor options it doesn't show.
        settings = c.get('/api/state').json()['settings']
        assert settings['scan_interval'] == 60 and settings['auto_lyrics'] is False


def test_monitor_thread_smoke(lib):
    store, root = lib
    write(root / 'tv' / 'ep.mkv'); write(root / 'music' / 'song.ogg')
    m = library.Monitor(store, root)
    m.start()
    try:
        m.request()
        deadline = time.time() + 10
        while time.time() < deadline and (m.scanning or not library.summary(store, m)['last_scan']):
            time.sleep(.05)
        s = library.summary(store, m)
        assert s['last_scan'] and not s['last_error']
        assert s['counts']['video']['waiting'] == 1 and s['counts']['song']['waiting'] == 1
        assert store.jobs() == []   # a plain check queues nothing
    finally:
        m.stop()
    assert not m.alive
