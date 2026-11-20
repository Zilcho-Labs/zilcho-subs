"""Named libraries (0.10.0): storage and migration, the API, and the rule that files
outside every library are invisible everywhere. Synthetic files only."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from zilcho_subs import coverage, monitor
from zilcho_subs.app import create_app
from zilcho_subs.core import Settings, Store, in_folders, leads_to_folders

PASSWORD = 'test-password-123'
OLD = 1_600_000_000


def write(path, data=b'synthetic'):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); os.utime(path, (OLD, OLD)); return path


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}


def make_media(root):
    write(root / 'tv' / 'Show' / 'A.mkv'); write(root / 'tv' / 'Show' / 'A.en.srt', b'x')
    write(root / 'movies' / 'Film.mkv')
    write(root / 'music' / 'Album' / 'Song.mp3'); write(root / 'music' / 'Album' / 'Song.lrc', b'[00:01.00]Hi\n')
    write(root / 'other' / 'Outside.mkv'); write(root / 'other' / 'Outside.mp3')
    write(root / 'Loose.mkv')


@pytest.fixture
def env(tmp_path):
    root = tmp_path / 'media'
    make_media(root)
    app = create_app(tmp_path / 'config', root, PASSWORD, run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': PASSWORD}).status_code == 200
        yield c, root, app.state.store


def add(c, name, folders, whole=False):
    r = c.post('/api/libraries', json={'name': name, 'folders': folders, 'whole_folder': whole})
    assert r.status_code == 200, r.text
    return r.json()


def three(c):
    return {n: add(c, n, f) for n, f in (('TV', ['tv']), ('Movies', ['movies']), ('Music', ['music']))}


def names(entries): return sorted(e['name'] for e in entries)


# ----- storage and migration -----

def test_old_scan_dirs_become_one_default_library(tmp_path):
    store = Store(tmp_path / 'config')
    old = Settings(scan_dirs=['tv', 'music']).model_dump()
    del old['libraries']                      # what an older version saved
    with store.db() as db:
        db.execute('UPDATE settings SET data=? WHERE id=1', (json.dumps(old),))
    s = store.settings()
    assert [(l.id, l.folders, l.whole_folder) for l in s.libraries] == [('default', ['tv', 'music'], False)]
    assert s.scan_dirs == ['tv', 'music']


def test_fresh_install_is_one_whole_library():
    s = Settings()
    assert [(l.folders, l.whole_folder) for l in s.libraries] == [([''], True)]
    assert s.scan_dirs == ['']


def test_scan_dirs_follows_libraries_and_validates():
    s = Settings(libraries=[{'name': 'TV', 'folders': ['tv']}, {'name': 'Music', 'folders': ['music', 'tv']}])
    assert s.scan_dirs == ['tv', 'music'] and all(l.id for l in s.libraries)
    for bad in ({'name': 'x', 'folders': ['../up']}, {'name': ' ', 'folders': ['tv']}, {'name': 'x', 'folders': []}):
        with pytest.raises(ValueError): Settings(libraries=[bad])
    with pytest.raises(ValueError):
        Settings(libraries=[{'name': 'TV', 'folders': ['a']}, {'name': 'tv', 'folders': ['b']}])


def test_old_settings_form_still_works(env):
    c, _root, store = env
    add(c, 'TV', ['tv'])
    s = c.get('/api/state').json()['settings']
    assert c.post('/api/settings', json=s).status_code == 200            # unchanged: libraries kept
    assert [l.name for l in store.settings().libraries][-1] == 'TV'
    s['scan_dirs'] = ['movies']                                          # the old form changing folders
    assert c.post('/api/settings', json=s).status_code == 200
    assert [(l.folders) for l in store.settings().libraries] == [['movies']]


def test_path_helpers():
    assert in_folders('tv/a.mkv', ['tv']) and not in_folders('tvx/a.mkv', ['tv']) and in_folders('x', [''])
    assert not in_folders('tv/a.mkv', [])
    assert leads_to_folders('', ['tv/Show']) and leads_to_folders('tv', ['tv/Show']) and not leads_to_folders('music', ['tv/Show'])


# ----- API -----

def test_library_crud(env):
    c, _root, store = env
    first = c.get('/api/libraries').json()
    assert [l['id'] for l in first['libraries']] == ['default'] and first['coverage_max_files'] == 100_000
    tv = add(c, 'TV', ['tv'])
    assert tv['id'] and tv['folders'] == ['tv'] and tv['whole_folder'] is False
    whole = add(c, 'Everything', ['', ], False)
    assert whole['whole_folder'] is True                                  # the main folder is flagged
    flagged = add(c, 'Main', ['movies'], True)
    assert flagged['whole_folder'] is True                                # user can flag a main folder
    r = c.put(f"/api/libraries/{tv['id']}", json={'name': 'Shows', 'folders': ['tv', 'other'], 'whole_folder': False})
    assert r.status_code == 200 and r.json()['name'] == 'Shows' and r.json()['id'] == tv['id']
    assert store.settings().scan_dirs == ['', 'movies', 'tv', 'other'] or set(store.settings().scan_dirs) == {'', 'movies', 'tv', 'other'}
    assert c.delete(f"/api/libraries/{tv['id']}").json() == {'ok': True}
    assert tv['id'] not in [l['id'] for l in c.get('/api/libraries').json()['libraries']]
    assert 'other' not in store.settings().scan_dirs


def test_library_errors(env):
    c, _root, _store = env
    tv = add(c, 'TV', ['tv'])
    assert c.post('/api/libraries', json={'name': 'tv', 'folders': ['movies']}).status_code == 400     # name used
    assert c.post('/api/libraries', json={'name': 'Bad', 'folders': ['../x']}).status_code in (400, 422)
    assert c.post('/api/libraries', json={'name': 'Bad', 'folders': []}).status_code in (400, 422)
    assert c.put('/api/libraries/nope', json={'name': 'x', 'folders': ['tv']}).status_code == 404
    assert c.delete('/api/libraries/nope').status_code == 404
    assert c.put(f"/api/libraries/{tv['id']}", json={'name': 'TV', 'folders': ['tv/../..']}).status_code in (400, 422)
    c.cookies.clear()
    assert c.get('/api/libraries').status_code == 401


# ----- files outside every library are invisible -----

def test_media_and_song_lists(env):
    c, _root, _store = env
    for lib in list(c.get('/api/libraries').json()['libraries']): c.delete(f"/api/libraries/{lib['id']}")
    libs = three(c)
    top = c.get('/api/browse').json()
    assert names(top['entries']) == ['movies', 'music', 'tv']             # no 'other', no Loose.mkv
    assert names(c.get('/api/browse?path=tv/Show').json()['entries']) == ['A.en.srt', 'A.mkv']
    assert c.get('/api/browse?path=other').status_code == 400
    assert names(c.get('/api/music/browse').json()['entries']) == ['movies', 'music', 'tv']
    assert names(c.get('/api/music/browse?path=music/Album').json()['entries']) == ['Song.mp3']
    assert c.get('/api/music/browse?path=other').status_code == 400
    only = c.get(f"/api/browse?library={libs['Movies']['id']}").json()
    assert names(only['entries']) == ['movies']
    assert c.get(f"/api/browse?path=tv&library={libs['Movies']['id']}").status_code == 400
    assert c.get('/api/browse?library=nope').status_code == 404


def test_nested_library_folder_is_reachable(env):
    c, _root, _store = env
    c.delete('/api/libraries/default')
    add(c, 'Show', ['tv/Show'])
    assert names(c.get('/api/browse').json()['entries']) == ['tv']
    assert names(c.get('/api/browse?path=tv').json()['entries']) == ['Show']
    assert names(c.get('/api/browse?path=tv/Show').json()['entries']) == ['A.en.srt', 'A.mkv']


def audit_everything(store, root):
    monitor.audit_library(store, root, now=1000)
    monitor.audit_library(store, root, now=1031)


def audit_paths(c, **params):
    return sorted(i['path'] for i in c.get('/api/state/audit', params={'limit': 100, **params}).json()['items'])


def test_monitor_list_summary_and_search(env):
    c, root, store = env
    audit_everything(store, root)                                        # the default library sees all
    assert 'other/Outside.mkv' in audit_paths(c) and 'Loose.mkv' in audit_paths(c)
    libs = three(c)
    c.delete('/api/libraries/default')
    assert audit_paths(c) == ['movies/Film.mkv', 'music/Album/Song.mp3', 'tv/Show/A.mkv']      # rows stay, but hidden
    assert audit_paths(c, q='outside') == []
    assert audit_paths(c, q='film') == ['movies/Film.mkv']
    summary = c.get('/api/state/audit?items=false').json()['summary']
    assert sum(sum(v.values()) for v in summary['counts'].values()) == 3
    picked = c.get('/api/state/audit?paths=true').json()['paths']          # Select all
    assert not [p for p in picked if p.startswith('other/') or p == 'Loose.mkv']
    # ?library=<id> narrows further
    assert audit_paths(c, library=libs['TV']['id']) == ['tv/Show/A.mkv']
    one = c.get(f"/api/state/audit?items=false&library={libs['Music']['id']}").json()['summary']['counts']
    assert sum(sum(v.values()) for v in one.values()) == 1
    assert c.get('/api/state/audit?library=nope').status_code == 404


def test_monitor_scan_and_single_file_audit(env):
    c, root, store = env
    add(c, 'TV', ['tv']); c.delete('/api/libraries/default')
    monitor.audit_library(store, root, now=1000)
    with store.db() as db:
        assert [r[0] for r in db.execute('SELECT path FROM audit ORDER BY path')] == ['tv/Show/A.mkv']
    assert c.post('/api/audit', json={'path': 'other/Outside.mkv'}).status_code == 400
    assert c.post('/api/audit', json={'path': 'tv/Show/A.mkv'}).status_code == 200
    assert c.post('/api/audit/batch', json={'paths': ['other/Outside.mkv']}).json()['errors'][0]['path'] == 'other/Outside.mkv'


def test_coverage_default_all_and_per_library(env):
    c, root, _store = env
    libs = three(c); c.delete('/api/libraries/default')
    before = snapshot(root)
    allc = c.get('/api/coverage?refresh=true').json()
    assert (allc['videos'], allc['songs']) == (2, 1) and allc['library'] is None
    assert allc['whole_folder'] is False and allc['max_files'] == 100_000
    tv = c.get(f"/api/coverage?library={libs['TV']['id']}").json()
    assert (tv['videos'], tv['songs'], tv['library'], tv['folders']) == (1, 0, libs['TV']['id'], ['tv'])
    music = c.get(f"/api/coverage?library={libs['Music']['id']}").json()
    assert (music['videos'], music['songs'], music['lyrics']['have']) == (0, 1, 1)
    assert c.get('/api/coverage?library=nope').status_code == 404
    assert snapshot(root) == before


def test_coverage_generate_stays_inside_libraries(env):
    c, root, store = env
    libs = three(c); c.delete('/api/libraries/default')
    r = c.post(f"/api/coverage/generate?library={libs['Movies']['id']}", json={'what': 'subtitles', 'language': 'es'}).json()
    assert r['queued'] == 1
    paths = [j['spec']['path'] for j in store.jobs()]
    assert paths == ['movies/Film.mkv']
    r = c.post('/api/coverage/generate', json={'what': 'subtitles', 'language': 'fr'}).json()
    assert r['queued'] == 2                                               # tv + movies; never 'other' or Loose
    assert sorted(j['spec']['path'] for j in store.jobs() if 'fr' in j['spec']['settings']['target_languages']) == ['movies/Film.mkv', 'tv/Show/A.mkv']


def test_whole_folder_library_keeps_caps_and_cache(env, monkeypatch):
    c, root, _store = env
    whole = add(c, 'Everything', ['', ])
    c.delete('/api/libraries/default')
    assert whole['whole_folder'] is True
    data = c.get('/api/coverage?refresh=true').json()
    assert data['whole_folder'] is True and data['videos'] == 4 and data['truncated'] is False
    write(root / 'Later.mkv')                                            # cached for a minute
    assert c.get('/api/coverage').json()['videos'] == 4
    assert c.get('/api/coverage?refresh=true').json()['videos'] == 5
    monkeypatch.setattr(coverage, 'MAX_FILES', 3)                        # the file-count cap still applies
    capped = c.get('/api/coverage?refresh=true').json()
    assert capped['truncated'] is True and capped['videos'] + capped['songs'] == 3 and capped['max_files'] == 3


def insert_job(store, id, spec, outputs):
    with store.db() as db:
        db.execute("INSERT INTO jobs(id,kind,spec,dedupe,status,stage,progress,outputs,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (id, 'subtitle', json.dumps(spec), id, 'completed', 'done', 100, json.dumps(outputs), 1, 1))


SRT = '1\n00:00:01,000 --> 00:00:02,000\nHello there\n'


def test_quality_hides_files_outside_libraries(env):
    c, root, store = env
    for rel in ('tv/Show/A.en.srt', 'other/Outside.en.srt'):
        write(root / rel, SRT.encode())
    insert_job(store, 'j-tv', {'path': 'tv/Show/A.mkv'}, {'en': 'tv/Show/A.en.srt'})
    insert_job(store, 'j-out', {'path': 'other/Outside.mkv'}, {'en': 'other/Outside.en.srt'})
    assert c.get('/api/quality').json()['total'] == 2                     # default library: everything
    libs = three(c); c.delete('/api/libraries/default')
    q = c.get('/api/quality').json()
    assert q['total'] == 1 and [j['id'] for j in q['jobs']] == ['j-tv']
    assert c.get(f"/api/quality?library={libs['Movies']['id']}").json()['total'] == 0
    assert [j['id'] for j in c.get(f"/api/quality?library={libs['TV']['id']}").json()['jobs']] == ['j-tv']
    assert c.get('/api/quality?library=nope').status_code == 404


def test_existing_jobs_still_listed_and_untouched(env):
    c, root, store = env
    job = store.enqueue('subtitle', {'path': 'other/Outside.mkv', 'audio_stream': None, 'regenerate': False,
                                     'settings': Settings().model_dump()})
    add(c, 'TV', ['tv']); c.delete('/api/libraries/default')
    assert [j['id'] for j in c.get('/api/state').json()['jobs']] == [job]  # the queue itself is not filtered
