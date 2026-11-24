"""0.13.0 sidebar rebuild: one Library page, Library status, file-name search.

The front end is plain JavaScript with no build step, so the page logic is checked by
running the real scripts under Node against a stub page (tests/library_page_check.js,
tests/page_wiring_check.js; skipped when Node is missing). The new read-only search
endpoint and the requests the Make / Retry buttons send are replayed against the real API.
Synthetic media only, no workers."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zilcho_subs import monitor
from zilcho_subs.app import create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'zilcho_subs' / 'static'
PASSWORD = 'test-password-123'
NODE = shutil.which('node')


def run_node(script):
    if not NODE: pytest.skip('Node is not installed')
    r = subprocess.run([NODE, str(ROOT / 'tests' / script), str(STATIC)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# ----- the page -----

def test_page_logic_under_node():
    out = run_node('library_page_check.js')
    assert len(out['passed']) >= 15


def test_every_script_id_exists_and_the_sidebar_has_eight_entries():
    out = run_node('page_wiring_check.js')
    assert out['nav'] == ['Queue', 'Library', 'Library status', 'Translate subtitles', 'Speech models', 'Language packs', 'Settings', 'About &amp; credits']


def test_moved_features_are_where_the_card_put_them():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    def section(name): return html.split('<section id="%s"' % name)[1].split('</section>')[0]
    queue, library, status, settings = (section(n) for n in ('queue', 'library', 'monitor', 'settings'))
    assert 'id="quality-list"' in queue and 'data-queue-tab="completed"' in queue                    # Subtitle quality: Queue > Completed
    assert all(i in library for i in ('id="audio-stream"', 'id="regenerate"', 'id="lyrics-regenerate"', 'id="library-options"'))   # the Options fold
    assert all(f'data-library-kind="{k}"' in library for k in ('', 'video', 'song'))                  # All / Videos / Songs
    assert all(f'data-status-tab="{t}"' in status for t in ('coverage', 'results', 'watching'))      # the three views
    assert 'id="coverage-subs"' in status and 'id="monitor-retry-failed"' in status and 'id="monitor-form"' in status
    assert 'id="monitor-filter"' in status and status.index('id="monitor-filter"') < status.index('id="monitor-retry-failed"') < status.index('id="monitor-kind"')
    assert 'id="library-form"' in settings and 'id="folder-picker"' in html and 'id="server-list"' in settings
    for gone in ('id="music"', 'id="quality"', 'id="coverage"'): assert gone not in html
    # Coverage is the view that opens first
    assert status.index('id="status-coverage"') < status.index('id="status-results"') < status.index('id="status-watching"')
    assert 'id="status-results" class="status-pane" hidden' in status and 'id="status-coverage" class="status-pane"' in status


def test_old_page_names_still_redirect():
    app = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert "VIEW_ALIASES={music:'library',quality:'queue',coverage:'monitor'}" in app


# ----- the search endpoint and the buttons' requests, against the real API -----

FILES = ['tv/Naruto/Naruto 01.mkv', 'tv/Naruto/Naruto 02.mkv', 'tv/Bleach/Bleach 01.mkv', 'tv/.hidden/Naruto Hidden.mkv',
         'movies/The Matrix.mkv', 'music/Album/Naruto Theme.flac', 'other/Naruto Outside.mkv', 'Loose Naruto.mkv']


@pytest.fixture
def env(tmp_path):
    root = tmp_path / 'media'
    for p in FILES:
        f = root / p; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'x')
    (root / 'tv/Naruto/Naruto 01.en.srt').write_bytes(b'1\n00:00:00,000 --> 00:00:01,000\nhi\n')
    (root / 'music/Album/Naruto Theme.lrc').write_bytes(b'[00:01.00]hi\n')
    app = create_app(tmp_path / 'config', root, PASSWORD, run_workers=False)
    store = app.state.store
    store.save_settings(store.settings().model_copy(update={'stable_seconds': 30}))
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': PASSWORD}).status_code == 200
        c.delete('/api/libraries/default')
        libs = {name: c.post('/api/libraries', json={'name': name, 'folders': [folder], 'whole_folder': False}).json()['id']
                for name, folder in (('TV', 'tv'), ('Movies', 'movies'), ('Music', 'music'))}
        yield c, root, store, libs


def search(c, **params): return c.get('/api/library/search', params=params)


def names(r): return [e['name'] for e in r.json()['entries']]


def test_search_finds_names_across_a_whole_library_and_never_outside_it(env):
    c, _root, _store, libs = env
    r = search(c, q='naruto', library=libs['TV'])
    assert names(r) == ['Naruto 01.mkv', 'Naruto 02.mkv'] and r.json()['total'] == 2    # not Hidden, Outside, Loose or the song
    first = r.json()['entries'][0]
    assert first['path'] == 'tv/Naruto/Naruto 01.mkv' and first['folder'] == 'tv/Naruto' and first['kind'] == 'video'
    assert first['status']['subs'] == ['en'] and r.json()['entries'][1]['status']['subs'] == []
    everywhere = search(c, q='naruto')                                                  # every library: still not the files in none
    assert names(everywhere) == ['Naruto 01.mkv', 'Naruto 02.mkv', 'Naruto Theme.flac']
    assert everywhere.json()['entries'][2]['status']['lyrics'] is True and everywhere.json()['entries'][2]['kind'] == 'song'
    for hidden in ('outside', 'loose', 'hidden'): assert search(c, q=hidden).json()['entries'] == []


def test_search_ranks_like_the_list_pages_and_matches_folder_names(env):
    c, _root, _store, _libs = env
    assert names(search(c, q='album')) == ['Naruto Theme.flac']                         # a folder in the path counts
    assert names(search(c, q='bleach 01')) == ['Bleach 01.mkv']
    assert names(search(c, q='  NARUTO   01 ')) == ['Naruto 01.mkv']                    # spaces and case do not matter
    nothing = search(c, q='').json()                                                     # an empty box lists nothing, not the whole library
    assert nothing['entries'] == [] and nothing['total'] == 0


def test_search_kind_paging_and_bad_input(env):
    c, _root, _store, libs = env
    assert names(search(c, q='naruto', kind='song')) == ['Naruto Theme.flac']
    assert names(search(c, q='naruto', kind='video')) == ['Naruto 01.mkv', 'Naruto 02.mkv']
    page = search(c, q='naruto', library=libs['TV'], limit=1, offset=1).json()
    assert [e['name'] for e in page['entries']] == ['Naruto 02.mkv'] and page['total'] == 2
    assert search(c, q='naruto', kind='photo').status_code == 400
    assert search(c, q='naruto', library='nope').status_code == 404                     # an unknown library finds nothing
    c.cookies.clear()
    assert search(c, q='naruto').status_code == 401


def test_search_writes_nothing(env):
    c, root, store, libs = env
    def snap(): return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
    before = snap()
    for params in ({'q': 'naruto'}, {'q': 'matrix', 'library': libs['Movies']}, {'q': 'x', 'kind': 'song'}): search(c, **params)
    assert snap() == before
    with store.db() as db: assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0


def test_a_mixed_selection_queues_each_kind_with_its_own_job(env):
    c, _root, store, _libs = env
    v = c.post('/api/jobs/batch', json={'paths': ['tv/Naruto/Naruto 01.mkv', 'tv/Naruto/Naruto 02.mkv']}).json()
    s = c.post('/api/lyrics/batch', json={'paths': ['music/Album/Naruto Theme.flac']}).json()
    assert (v['count'], s['count'], v['errors'], s['errors']) == (2, 1, [], [])
    kinds = sorted(j['kind'] for j in c.get('/api/state').json()['jobs'])
    assert kinds == ['lyrics', 'subtitle', 'subtitle']


def settle(store, root):
    monitor.audit_library(store, root, now=1000)
    monitor.audit_library(store, root, now=1031)


def test_make_missing_for_one_library_queues_only_its_own_files(env):
    c, root, store, libs = env
    settle(store, root)
    tv = c.get('/api/state/audit', params={'kind': 'video', 'paths': 'true', 'library': libs['TV']}).json()
    assert sorted(tv['paths']) == ['tv/Bleach/Bleach 01.mkv', 'tv/Naruto/Naruto 02.mkv']      # Naruto 01 already has its .en.srt
    songs = c.get('/api/state/audit', params={'kind': 'song', 'paths': 'true', 'library': libs['Music']}).json()
    assert songs['paths'] == []                                                              # the only song already has lyrics
    allv = c.get('/api/state/audit', params={'kind': 'video', 'paths': 'true'}).json()
    assert 'movies/The Matrix.mkv' in allv['paths'] and len(allv['paths']) == 3              # "All libraries" is everything
    r = c.post('/api/audit/batch', json={'paths': tv['paths']}).json()
    assert r['queued'] == 2 and r['errors'] == []
    with store.db() as db: states = {row['path']: row['state'] for row in db.execute('SELECT path,state FROM audit')}
    assert states['tv/Bleach/Bleach 01.mkv'] == states['tv/Naruto/Naruto 02.mkv'] == 'queued'
    assert states['movies/The Matrix.mkv'] == 'missing'                                      # another library: untouched


def test_retry_failed_requeues_the_failed_files_of_that_library_only(env):
    c, root, store, libs = env
    settle(store, root)
    jobs = monitor.audit_library(store, root, now=1100, generate=('video',))['queued']
    assert len(jobs) == 3
    for job in jobs: store.update(job, status='failed', error='boom')
    monitor.audit_library(store, root, now=1200)                                             # the check marks them failed
    summary = c.get('/api/state/audit', params={'items': 'false', 'library': libs['TV']}).json()['summary']
    assert summary['counts']['video']['failed'] == 2                                         # the number on the button
    failed = c.get('/api/state/audit', params={'state': 'failed', 'paths': 'true', 'library': libs['TV']}).json()['paths']
    assert sorted(failed) == ['tv/Bleach/Bleach 01.mkv', 'tv/Naruto/Naruto 02.mkv']          # as many as the button said
    assert c.get('/api/state/audit', params={'state': 'failed', 'paths': 'true', 'library': libs['Music']}).json()['paths'] == []
    r = c.post('/api/audit/batch', json={'paths': failed}).json()
    assert r['queued'] == len(failed) == 2 and r['errors'] == []
    with store.db() as db: states = {row['path']: row['state'] for row in db.execute('SELECT path,state FROM audit')}
    assert states['tv/Bleach/Bleach 01.mkv'] == 'queued' and states['movies/The Matrix.mkv'] == 'failed'   # Movies still failed
