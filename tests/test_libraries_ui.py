"""Library manager and library picker (0.10.0 front end).

The page is plain JavaScript with no build step, so these tests check the HTML and
scripts as served, run the script's pure helpers under Node when it is installed,
and replay the exact requests the manager sends against the real API."""
import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zilcho_subs import monitor
from zilcho_subs.app import create_app

STATIC = Path(__file__).resolve().parent.parent / 'zilcho_subs' / 'static'
PASSWORD = 'test-password-123'
NODE = shutil.which('node')
WARNING = 'Searching this collection scans the entire folder.'


def text(name): return (STATIC / name).read_text(encoding='utf-8')


def view(html, name):
    return re.search(r'<section id="%s".*?</section>' % name, html, re.S).group(0)


# ----- the page -----

def test_page_loads_the_library_script_before_the_pages_that_use_it():
    html = text('index.html')
    assert '/static/libraries.js' in html
    assert html.index('/static/libraries.js') < html.index('/static/monitor.js')


@pytest.mark.parametrize('name', ['library', 'monitor', 'queue'])   # queue: Completed holds the quality list
def test_each_list_page_has_a_library_dropdown(name):
    assert 'class="library-select"' in view(text('index.html'), name)


def test_manager_has_name_folders_whole_folder_and_the_warning():
    html = text('index.html')
    for needle in ('id="library-form"', 'id="library-name"', 'id="library-folders"', 'id="library-whole"', 'id="library-list"'):
        assert needle in html
    assert WARNING in html or WARNING in text('libraries.js')


def test_monitor_folder_box_is_replaced_by_the_manager():
    assert 'id="scan-dirs"' not in text('index.html')
    assert 'scan-dirs' not in text('monitor.js')
    assert 'id="library-form"' in view(text('index.html'), 'settings')     # 0.13.0: the manager is in Settings
    assert 'id="media-servers"' in view(text('index.html'), 'settings')


def test_every_page_asks_the_server_for_the_chosen_library():
    app, lib, mon, cov, qual = (text(n) for n in ('app.js', 'library.js', 'monitor.js', 'coverage.js', 'quality.js'))
    assert "get('/browse')" in lib and "get('/music/browse')" in lib and 'withLibrary(kind+' in lib   # Library: both listings
    assert "withLibrary('/library/search?" in lib                                                  # Library: search
    assert 'libraryParam()' in mon                       # File results list and sidebar card
    assert "withLibrary('/state/audit?items=false')" in mon
    assert "withLibrary('/state/audit?kind='" in mon and "withLibrary('/state/audit?state=failed&paths=true')" in mon
    assert "withLibrary('/coverage" in cov and "withLibrary('/coverage/generate')" in cov
    assert "withLibrary('/quality?" in qual
    assert 'method' in re.search(r'async function api\(.*', app).group(0)    # PUT and DELETE for the manager


def test_pages_say_so_when_there_are_no_libraries():
    assert 'noLibraries()' in text('library.js')
    assert 'No collections yet' in text('libraries.js')


# ----- 1.5.0: libraries are called Collections on screen; names, ids, API and storage stay -----

@pytest.mark.parametrize('name', ['library', 'monitor', 'queue'])
def test_selectors_are_labelled_collection(name):
    section = view(text('index.html'), name)
    assert 'library-pick">Collection<select class="library-select" aria-label="Collection"' in section


def test_settings_section_is_collections_and_page_names_stay():
    html = text('index.html')
    settings = view(html, 'settings')
    assert '<h2>Collections</h2>' in settings and 'Add collection</button>' in settings and '<h2>Libraries</h2>' not in settings
    assert '<button data-view="library">Library</button>' in html and '<button data-view="monitor">Library status</button>' in html
    assert 'data-library-kind="video"' in html and 'data-library-kind="song"' in html      # All / Videos / Songs stay
    assert "library:'Your library'" in text('app.js')


def test_all_collections_option_and_starter_label():
    js = text('libraries.js')
    assert "'All collections'" in js and 'All libraries' not in js
    assert "'Everything (main folder)'" in js and "'/libraries'" in js and "'library='" in js
    out = run_node("""
      const libs = [{id: 'default', name: 'Library', folders: [''], whole_folder: true}, {id: 'a', name: 'TV Shows', folders: ['tv'], whole_folder: false}];
      console.log(JSON.stringify([L.libraryLabel(libs[0]), L.libraryLabel(libs[1])]))""")
    assert out == ['Everything (main folder)', 'TV Shows']


def test_upgrade_keeps_saved_collections_and_scoping(env):
    """Settings saved by an older version (starter library plus two named ones) load unchanged."""
    c, root, store = env
    saved = store.settings().model_dump()
    saved['libraries'] = [
        {'id': 'default', 'name': 'Library', 'folders': [''], 'whole_folder': True},
        {'id': 'tv1', 'name': 'TV', 'folders': ['tv'], 'whole_folder': False},
        {'id': 'mv1', 'name': 'Movies', 'folders': ['movies'], 'whole_folder': False}]
    saved['scan_dirs'] = ['', 'tv', 'movies']  # older saves keep scan_dirs as the union of the collections' folders
    store.save_settings(type(store.settings()).model_validate(saved))
    libs = c.get('/api/libraries').json()['libraries']
    assert [(l['id'], l['name'], l['folders']) for l in libs] == [
        ('default', 'Library', ['']), ('tv1', 'TV', ['tv']), ('mv1', 'Movies', ['movies'])]
    top = {e['name'] for e in c.get('/api/browse', params={'library': 'tv1'}).json()['entries']}
    assert top == {'tv'}  # from the top, the collection's own folder is the way in
    tv = {e['name'] for e in c.get('/api/browse', params={'library': 'tv1', 'path': 'tv'}).json()['entries']}
    assert tv == {'A.mkv'}
    found = c.get('/api/library/search', params={'q': 'mkv', 'library': 'mv1'}).json()
    assert {e['path'] for e in found['entries']} == {'movies/B.mkv'}
    assert c.get('/api/browse', params={'library': 'nope'}).status_code == 404


def test_deleting_a_library_asks_first_and_says_files_are_kept():
    js = text('libraries.js')
    assert 'confirm(' in js and 'never deleted' in js


# ----- pure helpers, run under Node -----

def run_node(body):
    if not NODE: pytest.skip('Node is not installed')
    script = textwrap.dedent('''
        const vm = require('vm'), fs = require('fs');
        const store = {};
        const ctx = vm.createContext({
          readPref: (k, d) => (k in store ? store[k] : d), savePref: (k, v) => { store[k] = v; },
          console, encodeURIComponent });
        vm.runInContext(fs.readFileSync(process.argv[1], 'utf8') + '\\n;this.exports = {pickableLibraries, libraryLabel, withLibrary, libraryParam, parseFolders, libraryBody, describeLibrary, pickLibrary, setLibraryChoice, currentLibrary: () => libraryChoice};', ctx);
        const L = ctx.exports;
        %s
    ''') % body
    r = subprocess.run([NODE, '-e', script, str(STATIC / 'libraries.js')], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_all_libraries_adds_nothing_to_requests():
    out = run_node("console.log(JSON.stringify([L.libraryParam(), L.withLibrary('/browse?path=tv'), L.withLibrary('/quality')]))")
    assert out == ['', '/browse?path=tv', '/quality']


def test_chosen_library_is_added_and_remembered():
    out = run_node("""
      L.setLibraryChoice('movies 1');
      console.log(JSON.stringify([L.libraryParam(), L.withLibrary('/browse?path=tv'), L.withLibrary('/quality'), store['library']]))""")
    assert out == ['library=movies%201', '/browse?path=tv&library=movies%201', '/quality?library=movies%201', 'movies 1']


def test_saved_choice_is_restored_only_if_the_library_still_exists():
    out = run_node("""
      const libs = [{id: 'a', name: 'TV'}, {id: 'b', name: 'Movies'}];
      console.log(JSON.stringify([L.pickLibrary(libs, 'b'), L.pickLibrary(libs, 'gone'), L.pickLibrary(libs, ''), L.pickLibrary([], 'a')]))""")
    assert out == ['b', '', '', '']


def test_form_values_become_the_api_body():
    out = run_node("""
      console.log(JSON.stringify([
        L.parseFolders(' tv \\n\\n/movies/\\ntv\\n'),
        L.libraryBody(' TV ', 'tv\\nanime', false),
        L.libraryBody('All', 'ignored', true),
        L.libraryBody('Empty', '  \\n', false),
        L.libraryBody('  ', 'tv', false)]))""")
    assert out[0] == ['tv', 'movies']
    assert out[1] == {'body': {'name': 'TV', 'folders': ['tv', 'anime'], 'whole_folder': False}}
    assert out[2] == {'body': {'name': 'All', 'folders': [''], 'whole_folder': True}}
    assert 'folder' in out[3]['error'] and 'name' in out[4]['error'].lower()


def test_library_description_flags_whole_folder():
    out = run_node("""
      console.log(JSON.stringify([L.describeLibrary({folders: ['tv', 'anime'], whole_folder: false}),
        L.describeLibrary({folders: [''], whole_folder: true})]))""")
    assert out[0] == 'tv, anime' and 'whole' in out[1].lower()


# ----- the requests the manager sends, against the real API -----

@pytest.fixture
def env(tmp_path):
    root = tmp_path / 'media'
    for p in ('tv/A.mkv', 'movies/B.mkv', 'other/Outside.mkv', 'Loose.mkv'):
        f = root / p; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b'x')
    app = create_app(tmp_path / 'config', root, PASSWORD, run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': PASSWORD}).status_code == 200
        yield c, root, app.state.store


def test_manager_flow_create_rename_delete_never_touches_files(env):
    c, root, _store = env
    before = sorted(p.as_posix() for p in root.rglob('*'))
    tv = c.post('/api/libraries', json={'name': 'TV', 'folders': ['tv'], 'whole_folder': False}).json()
    c.delete('/api/libraries/default')
    assert [e['name'] for e in c.get('/api/browse').json()['entries']] == ['tv']
    renamed = c.put(f"/api/libraries/{tv['id']}", json={'name': 'Shows', 'folders': ['tv'], 'whole_folder': False}).json()
    assert renamed['name'] == 'Shows' and renamed['id'] == tv['id']
    assert c.delete(f"/api/libraries/{tv['id']}").json() == {'ok': True}   # DELETE with no body, as the browser sends it
    assert c.get('/api/browse').status_code == 400                         # nothing is in a library now
    assert sorted(p.as_posix() for p in root.rglob('*')) == before         # no file was deleted


def test_whole_folder_body_from_the_form_is_accepted_and_flagged(env):
    c, _root, _store = env
    r = c.post('/api/libraries', json={'name': 'Everything', 'folders': [''], 'whole_folder': True})
    assert r.status_code == 200 and r.json()['whole_folder'] is True


def test_two_libraries_and_a_file_in_neither(env):
    c, root, store = env
    monitor.audit_library(store, root, now=1000); monitor.audit_library(store, root, now=1031)
    c.delete('/api/libraries/default')
    tv = c.post('/api/libraries', json={'name': 'TV', 'folders': ['tv'], 'whole_folder': False}).json()
    mv = c.post('/api/libraries', json={'name': 'Movies', 'folders': ['movies'], 'whole_folder': False}).json()
    everywhere = {p['path'] for lib in ('', tv['id'], mv['id'])
                  for p in c.get('/api/state/audit', params={'library': lib, 'limit': 100}).json()['items']}
    assert everywhere == {'tv/A.mkv', 'movies/B.mkv'}                      # other/Outside.mkv and Loose.mkv never appear
    assert [e['name'] for e in c.get('/api/browse').json()['entries']] == ['movies', 'tv']


# ----- 0.12.0: folder picker and the starter library -----

def test_folder_list_shows_only_sub_folders_from_the_whole_media_folder(env):
    c, root, _store = env
    (root / 'tv' / 'Show').mkdir(); (root / '.hidden').mkdir()
    top = c.get('/api/folders').json()
    assert [f['path'] for f in top['folders']] == ['movies', 'other', 'tv']    # no files, no hidden folders
    c.delete('/api/libraries/default')                                           # the picker still sees every folder
    assert [f['path'] for f in c.get('/api/folders').json()['folders']] == ['movies', 'other', 'tv']
    inside = c.get('/api/folders', params={'path': 'tv'}).json()
    assert inside['path'] == 'tv' and inside['folders'] == [{'name': 'Show', 'path': 'tv/Show'}]


def test_folder_list_refuses_paths_outside_the_media_folder_and_needs_sign_in(env):
    c, _root, _store = env
    assert c.get('/api/folders', params={'path': '../'}).status_code == 400
    assert c.get('/api/folders', params={'path': 'tv/A.mkv'}).status_code == 400   # a file, not a folder
    c.cookies.clear()
    assert c.get('/api/folders').status_code == 401


def test_picker_replaces_typing_folder_names():
    html, js = text('index.html'), text('libraries.js')
    assert 'id="library-add-folder"' in html and 'id="folder-picker"' in html and 'id="folder-add"' in html
    assert re.search(r'<textarea id="library-folders" hidden>', html)
    assert "'/folders?path='" in js and 'Add another folder' in js


def test_starter_library_is_hidden_from_dropdowns_but_not_the_manager():
    out = run_node("""
      const libs = [{id: 'default', name: 'Library', folders: [''], whole_folder: true}, {id: 'a', name: 'TV Shows', folders: ['tv'], whole_folder: false}];
      console.log(JSON.stringify([L.pickableLibraries(libs).map(l => l.id), L.pickLibrary(libs, 'default'), L.pickLibrary(libs, 'a'),
        L.libraryLabel(libs[0]), L.libraryLabel(libs[1]),
        L.pickableLibraries([{id: 'default', name: 'Mine', folders: ['tv'], whole_folder: false}]).length]))""")
    assert out == [['a'], '', 'a', 'Everything (main folder)', 'TV Shows', 1]
