"""Removing, archiving and bulk clean-up of finished jobs."""
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from zilcho_subs.app import create_app
from zilcho_subs.core import Store


@pytest.fixture
def client(tmp_path):
    root = tmp_path / 'media'; root.mkdir()
    for name in ('a.mkv', 'b.mkv', 'c.mkv', 'd.mkv'): (root / name).write_bytes(b'synthetic')
    app = create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': 'test-password-123'}).status_code == 200
        yield c


def queue(c, name, status=None):
    id = c.post('/api/jobs', json={'path': name}).json()['id']
    if status: c.app.state.store.update(id, status=status)
    return id


def recent_ids(c): return [j['id'] for j in c.get('/api/state').json()['jobs']]


def test_finished_job_can_be_archived_restored_and_removed(client):
    done = queue(client, 'a.mkv', 'completed')
    assert client.post(f'/api/jobs/{done}/archive', json={}).status_code == 200
    state = client.get('/api/state').json()
    assert done not in [j['id'] for j in state['jobs']] and state['archived_count'] == 1
    assert [j['id'] for j in client.get('/api/jobs/archived').json()['jobs']] == [done]
    assert client.post(f'/api/jobs/{done}/unarchive', json={}).status_code == 200
    assert done in recent_ids(client) and client.get('/api/state').json()['archived_count'] == 0
    assert client.post(f'/api/jobs/{done}/remove', json={}).status_code == 200
    assert done not in recent_ids(client)
    assert client.post(f'/api/jobs/{done}/remove', json={}).status_code == 404


@pytest.mark.parametrize('status', ['queued', 'running', 'pausing', 'paused', 'cancelling'])
def test_active_jobs_cannot_be_archived_or_removed(client, status):
    id = queue(client, 'a.mkv', status)
    for path in ('archive', 'remove'):
        response = client.post(f'/api/jobs/{id}/{path}', json={})
        assert response.status_code == 400 and 'cancel it first' in response.json()['detail']
    assert client.app.state.store.job(id)['status'] == status


def test_removing_a_job_never_touches_files(client, tmp_path):
    store = client.app.state.store
    sidecar = tmp_path / 'media' / 'a.en.AI.srt'; sidecar.write_text('1\n00:00:01,000 --> 00:00:02,000\nHi\n', encoding='utf-8')
    id = queue(client, 'a.mkv', 'completed')
    store.update(id, outputs={'en': 'a.en.AI.srt'})
    checkpoint = store.config / 'transcripts' / 'checkpoints' / (id + '.json')
    checkpoint.parent.mkdir(parents=True, exist_ok=True); checkpoint.write_text('{}')
    assert client.post(f'/api/jobs/{id}/remove', json={}).status_code == 200
    assert sidecar.exists() and (tmp_path / 'media' / 'a.mkv').exists()
    assert not checkpoint.exists()  # the job's own leftover resume data goes with it


def test_bulk_archive_completed_and_clear_failed(client):
    done, skipped = queue(client, 'a.mkv', 'completed'), queue(client, 'b.mkv', 'skipped')
    failed, waiting = queue(client, 'c.mkv', 'failed'), queue(client, 'd.mkv')
    r = client.post('/api/jobs/cleanup', json={'statuses': ['completed', 'skipped'], 'action': 'archive'})
    assert r.json()['count'] == 2
    assert recent_ids(client) == [waiting, failed]
    r = client.post('/api/jobs/cleanup', json={'statuses': ['failed', 'cancelled'], 'action': 'remove'})
    assert r.json()['count'] == 1 and recent_ids(client) == [waiting]
    # Emptying the archive removes only archived jobs.
    r = client.post('/api/jobs/cleanup', json={'statuses': ['completed', 'failed', 'skipped', 'cancelled'], 'action': 'remove', 'archived': True})
    assert r.json()['count'] == 2 and client.get('/api/jobs/archived').json()['jobs'] == []
    assert recent_ids(client) == [waiting]


@pytest.mark.parametrize('body', [
    {'statuses': ['running'], 'action': 'remove'},
    {'statuses': ['queued'], 'action': 'archive'},
    {'statuses': [], 'action': 'remove'},
    {'statuses': ['failed'], 'action': 'delete-everything'},
    {'statuses': ['failed'], 'action': 'archive', 'archived': True},
])
def test_cleanup_rejects_unsafe_requests(client, body):
    queue(client, 'a.mkv', 'running')
    assert client.post('/api/jobs/cleanup', json=body).status_code in (400, 422)
    assert len(recent_ids(client)) == 1


def test_cleanup_needs_sign_in_and_same_origin(client):
    assert client.post('/api/jobs/cleanup', json={'statuses': ['failed'], 'action': 'remove'},
                       headers={'Origin': 'https://evil.example'}).status_code == 403
    client.post('/api/logout', json={})
    assert client.post('/api/jobs/cleanup', json={'statuses': ['failed'], 'action': 'remove'}).status_code == 401


def test_existing_queue_database_gains_archive_column(tmp_path):
    config = tmp_path / 'config'; config.mkdir()
    db = sqlite3.connect(config / 'queue.sqlite3')
    db.executescript("""CREATE TABLE settings(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
      CREATE TABLE jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, spec TEXT NOT NULL, dedupe TEXT NOT NULL,
        status TEXT NOT NULL, stage TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
        outputs TEXT NOT NULL DEFAULT '{}', cancel INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
      CREATE UNIQUE INDEX active_jobs ON jobs(dedupe) WHERE status IN ('queued','running','cancelling');""")
    db.execute("INSERT INTO jobs(id,kind,spec,dedupe,status,stage,created,updated) VALUES('old','subtitle',?,'k','completed','Done',1,1)", (json.dumps({'path': 'a.mkv'}),))
    db.commit(); db.close()
    store = Store(config)
    assert [j['id'] for j in store.jobs()] == ['old'] and store.archived_count() == 0
    store.archive('old'); assert store.jobs() == [] and store.archived_count() == 1


def test_selected_jobs_only_are_changed(client):
    a, b, c = queue(client, 'a.mkv', 'completed'), queue(client, 'b.mkv', 'failed'), queue(client, 'c.mkv', 'completed')
    waiting = queue(client, 'd.mkv')
    r = client.post('/api/jobs/selection', json={'ids': [a, b, waiting], 'action': 'remove'})
    assert r.json()['count'] == 2  # the waiting job is left alone
    assert recent_ids(client) == [waiting, c]
    assert client.post('/api/jobs/selection', json={'ids': [c], 'action': 'archive'}).json()['count'] == 1
    assert recent_ids(client) == [waiting]
    assert client.post('/api/jobs/selection', json={'ids': [c], 'action': 'unarchive'}).json()['count'] == 1
    assert c in recent_ids(client)


def test_selected_jobs_can_be_cancelled_together(client):
    waiting, paused, running = queue(client, 'a.mkv'), queue(client, 'b.mkv', 'paused'), queue(client, 'c.mkv', 'running')
    done = queue(client, 'd.mkv', 'completed')
    r = client.post('/api/jobs/selection', json={'ids': [waiting, paused, running, done, 'nope'], 'action': 'cancel'})
    assert r.status_code == 200 and r.json()['count'] == 3  # the finished and unknown jobs are left alone
    jobs = {j['id']: j for j in client.get('/api/state').json()['jobs']}
    assert jobs[waiting]['status'] == jobs[paused]['status'] == 'cancelled'
    assert jobs[running]['status'] == 'cancelling'  # the worker stops it, as with the single Cancel button
    assert jobs[done]['status'] == 'completed'
    # Asking again doesn't count jobs already cancelled or on their way out.
    assert client.post('/api/jobs/selection', json={'ids': [waiting, running], 'action': 'cancel'}).json()['count'] == 0
    # Cancelled jobs are finished, so they can then be removed as a selection.
    assert client.post('/api/jobs/selection', json={'ids': [waiting, paused], 'action': 'remove'}).json()['count'] == 2


@pytest.mark.parametrize('body', [{'ids': [], 'action': 'remove'}, {'ids': ['x'], 'action': 'wipe'}])
def test_selection_rejects_bad_requests(client, body):
    assert client.post('/api/jobs/selection', json=body).status_code == 422


def test_cpu_threads_apply_when_a_job_starts_not_when_queued(tmp_path):
    from zilcho_subs.core import Settings
    from zilcho_subs.pipeline import process_subtitle
    media = tmp_path / 'media'; media.mkdir(); (media / 'a.mkv').write_bytes(b'x')
    store = Store(tmp_path / 'config'); store.save_settings(Settings(cpu_threads=2, source_language='en'))
    id = store.enqueue('subtitle', {'path': 'a.mkv', 'settings': store.settings().model_dump()})
    store.save_settings(Settings(cpu_threads=6, source_language='en'))  # changed after queueing
    seen = {}
    class Engine:
        def speech(self, audio, settings, task, progress):
            seen['threads'] = settings.cpu_threads
            raise RuntimeError('stop here')
    probe = lambda _: {'duration': 10, 'streams': [{'index': 1, 'codec_type': 'audio', 'tags': {'language': 'eng'}}]}
    with pytest.raises(RuntimeError, match='stop here'):
        process_subtitle(store, store.job(id), media, Engine(), probe, lambda media, index, out, duration: out.write_bytes(b'x'))
    assert seen['threads'] == 6
