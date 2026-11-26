"""The API key: shown once, stored as a hash, and good for three things only."""
import json

from test_api import client, login  # noqa: F401  (fixtures)


def make_key(c):
    login(c)
    key = c.post('/api/api-key', json={}).json()['key']
    c.post('/api/logout', json={})
    del c.headers['Origin']  # another app is not a web page
    return key


def media(c):
    root = c.app.state.store.config.parent / 'media'
    (root / 'tv' / 'Show').mkdir(parents=True)
    (root / 'tv' / 'Show' / 'Show S01E01.mkv').write_bytes(b'synthetic')
    return root


def test_managing_the_key_needs_the_sign_in(client):
    assert client.get('/api/api-key').status_code == 401
    assert client.post('/api/api-key', json={}).status_code == 401
    login(client)
    assert client.get('/api/api-key').json() == {'exists': False, 'created': None}
    first = client.post('/api/api-key', json={}).json()['key']
    assert first.startswith('zsk_') and len(first) > 30
    saved = (client.app.state.store.config / 'api_key.json').read_text(encoding='utf-8')
    assert first not in saved and set(json.loads(saved)) == {'hash', 'created'}
    assert 'key' not in client.get('/api/api-key').json()
    second = client.post('/api/api-key', json={}).json()['key']
    assert second != first
    head = {'X-Api-Key': first}
    assert client.get('/api/external/status', headers=head).status_code == 401
    assert client.delete('/api/api-key').json()['exists'] is False
    assert client.get('/api/external/status', headers={'X-Api-Key': second}).status_code == 401


def test_the_key_opens_only_the_external_calls(client):
    head = {'X-Api-Key': make_key(client)}
    assert client.get('/api/external/status', headers=head).json()['app'] == 'Zilcho Subs'
    for path in ('/api/state', '/api/api-key', '/api/browse', '/api/jellyfin'):
        assert client.get(path, headers=head).status_code == 401, path
    assert client.post('/api/settings', json={}, headers=head).status_code in (401, 403)
    assert client.post('/api/api-key', json={}, headers=head).status_code in (401, 403)


def test_a_signed_in_browser_still_needs_the_key_there(client):
    login(client)
    assert client.get('/api/external/status').status_code == 401


def test_wrong_keys_are_limited_and_never_accepted_in_the_address(client):
    key = make_key(client)
    assert client.get('/api/external/status', params={'api_key': key}).status_code == 401
    for _ in range(4):
        assert client.get('/api/external/status',
                          headers={'X-Api-Key': 'zsk_wrong'}).status_code == 401
    assert client.get('/api/external/status',
                      headers={'X-Api-Key': key}).status_code == 429


def test_requests_find_the_video_and_report_progress(client):
    head = {'X-Api-Key': make_key(client)}
    media(client)
    r = client.post('/api/external/subtitles', headers=head, json={
        'path': '/media/tv/Show/Show S01E01.mkv', 'languages': ['eng', 'es']})
    assert r.status_code == 200, r.text
    assert r.json()['path'] == 'tv/Show/Show S01E01.mkv'
    job = client.app.state.store.job(r.json()['id'])
    assert job['spec']['settings']['target_languages'] == ['en', 'es']
    assert job['spec']['settings']['skip_existing'] is True and job['spec']['regenerate'] is False
    got = client.get('/api/external/jobs/' + r.json()['id'], headers=head).json()
    assert got['status'] == 'queued' and got['languages'] == []
    again = client.post('/api/external/subtitles', headers=head, json={
        'path': '/media/tv/Show/Show S01E01.mkv', 'languages': ['eng', 'es']})
    assert again.json()['id'] == r.json()['id']


def test_bad_paths_languages_and_other_jobs_are_refused(client):
    login(client)
    mine = client.post('/api/jobs', json={'path': 'episode.mkv'}).json()['id']
    head = {'X-Api-Key': make_key(client)}
    media(client)
    ask = lambda path, languages=('en',): client.post(
        '/api/external/subtitles', headers=head,
        json={'path': path, 'languages': list(languages)}).status_code
    assert ask('../tv/Show/Show S01E01.mkv') == 400
    assert ask('/media/tv/Other/Show S01E01.mkv') == 404
    assert ask('/media/tv/Show/missing.mkv') == 404
    assert ask('/media/tv/Show/Show S01E01.mkv', ['not a language']) == 400
    assert ask('/media/tv/Show/Show S01E01.mkv', []) == 422
    assert client.get('/api/external/jobs/' + mine, headers=head).status_code == 404
