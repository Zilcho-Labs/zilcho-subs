from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from zilcho_subs.app import create_app
from zilcho_subs.core import render_srt

@pytest.fixture
def client(tmp_path):
    root=tmp_path/'media'; root.mkdir(); (root/'episode.mkv').write_bytes(b'synthetic')
    app=create_app(tmp_path/'config',root,'test-password-123',run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin']='http://testserver'
        yield c

def login(c): assert c.post('/api/login',json={'password':'test-password-123'}).status_code==200

def test_auth_and_csrf(client):
    assert client.get('/').status_code==200 and client.get('/api/state').status_code==401
    assert client.post('/api/login',json={'password':'wrong'}).status_code==401
    login(client); assert client.cookies.get('zilcho_session')
    assert client.get('/api/state').json()['settings']['model']=='medium'
    assert client.post('/api/settings',json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/assets',json={},headers={'Content-Length':'2200000'}).status_code==413
    assert client.get('/static/app.js').headers['Content-Security-Policy'].startswith("default-src 'self'")
    client.post('/api/logout',json={}); assert client.get('/api/state').status_code==401

def test_paths_queue_and_retry(client):
    login(client)
    assert client.get('/api/browse').json()['entries'][0]['path']=='episode.mkv'
    assert client.get('/api/browse',params={'path':'../'}).status_code==400
    id=client.post('/api/jobs',json={'path':'episode.mkv'}).json()['id']
    assert client.post('/api/jobs',json={'path':'episode.mkv'}).json()['id']==id
    assert client.post('/api/jobs',json={'path':'../secret.mkv'}).status_code==400
    assert client.post('/api/jobs/'+id+'/cancel',json={}).status_code==200
    assert client.app.state.store.job(id)['status']=='cancelled'
    assert client.post('/api/jobs/'+id+'/retry',json={}).json()['id']!=id

def test_assets_do_not_download_on_enqueue(client):
    login(client)
    for data in [{'kind':'model','model':'turbo'},{'kind':'model','model':'../../bad'},{'kind':'pack','source':'../../bad','target':'en'}]:
        assert client.post('/api/assets',json=data).status_code==400
    assert client.post('/api/assets',json={'kind':'model','model':'medium'}).status_code==200
    assert not list((client.app.state.store.config/'models').iterdir())

def test_preview_edit_preserves_original(client):
    login(client); store=client.app.state.store
    id=client.post('/api/jobs',json={'path':'episode.mkv'}).json()['id']
    root=Path(client.get('/api/state').json()['media_root']); original=render_srt([{'start':0,'end':1,'text':'First line'}])
    path=root/'episode.en.AI.srt'; path.write_text(original)
    store.update(id,status='completed',outputs={'en':path.name})
    assert client.get(f'/api/jobs/{id}/subtitles/en').text==original
    response=client.post(f'/api/jobs/{id}/subtitles/en',json={'text':original.replace('First line','Edited line')})
    assert response.status_code==200 and response.json()['path']=='episode.en.AI.2.srt'
    assert path.read_text()==original
    assert client.post(f'/api/jobs/{id}/subtitles/en',json={'text':'bad'}).status_code==400
    store.update(id,outputs={'en':'../private.srt'})
    assert client.get(f'/api/jobs/{id}/subtitles/en').status_code==400

def test_login_throttle(client):
    for _ in range(5): assert client.post('/api/login',json={'password':'wrong'}).status_code==401
    assert client.post('/api/login',json={'password':'wrong'}).status_code==429


def test_branding_assets_are_served_locally_without_weakening_auth(client):
    from html.parser import HTMLParser
    class Assets(HTMLParser):
        def __init__(self): super().__init__(); self.urls = set()
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            for key in ('src', 'href'):
                if attrs.get(key, '').startswith('/static/'): self.urls.add(attrs[key])
    parser = Assets(); parser.feed(client.get('/').text)
    for url in parser.urls:
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.headers['Content-Security-Policy'].startswith("default-src 'self'")
    mark = client.get('/static/branding/mark.png')
    assert mark.content.startswith(b'\x89PNG\r\n\x1a\n')
    import struct
    assert struct.unpack('>II', mark.content[16:24]) == (128, 128)
    png = client.get('/static/branding/app-logo.png')
    assert png.content.startswith(b'\x89PNG\r\n\x1a\n')
    assert struct.unpack('>II', png.content[16:24]) == (512, 512)
    assert len(png.content) < 450_000  # full colour: a 256-colour palette banded the glow
    assert client.get('/api/state').status_code == 401
