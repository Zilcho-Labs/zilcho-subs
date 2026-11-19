from pathlib import Path
from types import SimpleNamespace as N
import sqlite3
import pytest
from fastapi.testclient import TestClient
from zilcho_subs.core import Store,Settings,JobPaused
from zilcho_subs.app import create_app
from zilcho_subs.timing import SpeechCheckpoint,recognize_utterances,SAMPLE_RATE


def queued(store,name):return store.enqueue('subtitle',{'path':name,'settings':store.settings().model_dump()})


def test_run_next_checkpoints_then_returns_original_job_to_its_position(tmp_path):
    store=Store(tmp_path);a=queued(store,'a');b=queued(store,'b');c=queued(store,'c')
    assert store.claim()['id']==a
    store.update(a,progress=43)
    store.run_next(c)
    assert store.job(a)['status']=='pausing' and store.job(a)['resume_after_pause']==1
    store.finish_pause(a)
    assert store.claim()['id']==c
    store.update(c,status='completed')
    resumed=store.claim();assert resumed['id']==a and resumed['progress']==43
    store.update(a,status='completed');assert store.claim()['id']==b


def test_manual_pause_resume_and_order_survive_store_reopen(tmp_path):
    store=Store(tmp_path);a=queued(store,'a');b=queued(store,'b');c=queued(store,'c')
    store.pause(a);store.move(c,'top')
    other=Store(tmp_path);assert other.claim()['id']==c
    other.resume(a);assert other.claim()['id']==a
    assert len({j['queue_order'] for j in other.jobs()})==3
    assert other.job(b)['status']=='queued'


def test_move_preserves_reserved_running_slot(tmp_path):
    store=Store(tmp_path);a=queued(store,'a');b=queued(store,'b');c=queued(store,'c');store.claim()
    store.move(c,'up')
    assert len({store.job(x)['queue_order'] for x in (a,b,c)})==3
    assert store.job(c)['queue_order']<store.job(b)['queue_order']


def test_cancel_wins_over_pause_auto_resume_race(tmp_path):
    store=Store(tmp_path);a=queued(store,'a');b=queued(store,'b');store.claim();store.run_next(b);store.cancel(a);store.finish_pause(a)
    assert store.job(a)['status']=='cancelled'
    assert store.claim()['id']==b and store.claim() is None


def test_paused_job_dedupes_and_can_be_cancelled_without_restart(tmp_path):
    store=Store(tmp_path);a=queued(store,'a');store.pause(a)
    assert queued(store,'a')==a
    assert store.claim() is None
    store.cancel(a);assert store.job(a)['status']=='cancelled'
    with pytest.raises(ValueError):store.resume(a)


def test_running_download_cannot_be_preempted(tmp_path):
    store=Store(tmp_path);a=store.enqueue('model',{'model':'tiny'});store.claim();b=queued(store,'b');before=store.job(b)['queue_order']
    with pytest.raises(ValueError,match='cannot be interrupted'):store.run_next(b)
    assert store.job(a)['status']=='running' and store.job(b)['queue_order']==before


def test_all_pending_jobs_remain_visible_beyond_recent_history_limit(tmp_path):
    store=Store(tmp_path)
    for i in range(105):queued(store,str(i))
    assert len(store.jobs())==105


def test_old_schema_migration_preserves_jobs_and_assigns_order(tmp_path):
    store=Store(tmp_path);a=queued(store,'a');b=queued(store,'b')
    with sqlite3.connect(store.path) as db:
        for column in ('queue_order','pause_requested','resume_after_pause'):db.execute('ALTER TABLE jobs DROP COLUMN '+column)
        db.execute('DROP INDEX active_jobs')
        db.execute("CREATE UNIQUE INDEX active_jobs ON jobs(dedupe) WHERE status IN ('queued','running','cancelling')")
    migrated=Store(tmp_path)
    assert migrated.claim()['id']==a
    assert migrated.claim()['id']==b


def test_recognition_resumes_after_completed_window_without_decoding_it_again(tmp_path):
    intervals=[{'start':SAMPLE_RATE,'end':3*SAMPLE_RATE},{'start':6*SAMPLE_RATE,'end':8*SAMPLE_RATE}]
    class Model:
        def __init__(self):self.calls=0
        def transcribe(self,audio,**kwargs):
            self.calls+=1;return iter([N(text='Translated cue',words=None)]),N(language='ja')
    checkpoint=SpeechCheckpoint(tmp_path/'checkpoint.json','unchanged-source-and-model')
    first=Model()
    def pause(end):
        if end==3:raise JobPaused()
    with pytest.raises(JobPaused):recognize_utterances(first,range(10*SAMPLE_RATE),Settings(source_language='ja'),'translate',pause,lambda *a,**k:intervals,checkpoint)
    assert first.calls==1 and checkpoint.load()['completed']==1
    resumed=Model();result=recognize_utterances(resumed,range(10*SAMPLE_RATE),Settings(source_language='ja'),'translate',lambda _:None,lambda *a,**k:intervals,checkpoint)
    assert resumed.calls==1 and len(result['segments'])==2
    assert [c['start'] for c in result['segments']]==[1,6]
    with pytest.raises(ValueError,match='source/model changed'):SpeechCheckpoint(checkpoint.path,'different-source').load()


def test_job_controls_require_session_and_same_origin(tmp_path):
    media=tmp_path/'media';media.mkdir()
    app=create_app(tmp_path/'config',media,'synthetic-password-123',run_workers=False)
    with TestClient(app) as client:
        store=app.state.store;a=queued(store,'a');b=queued(store,'b')
        for route,body in [('pause',{}),('resume',{}),('run-next',{}),('move',{'direction':'top'})]:
            assert client.post('/api/jobs/'+a+'/'+route,json=body,headers={'Origin':'http://testserver'}).status_code==401
        client.post('/api/login',json={'password':'synthetic-password-123'},headers={'Origin':'http://testserver'})
        assert client.post('/api/jobs/'+a+'/pause',json={},headers={'Origin':'https://evil.invalid'}).status_code==403
        def post(id,route,body={}):return client.post('/api/jobs/'+id+'/'+route,json=body,headers={'Origin':'http://testserver'})
        assert post(a,'pause').status_code==200 and store.job(a)['status']=='paused'
        assert post(a,'resume').status_code==200
        assert post(b,'move',{'direction':'top'}).status_code==200
        assert post(a,'move',{'direction':'not-a-direction'}).status_code==422
        store.claim();assert post(a,'run-next').status_code==200


def test_recovery_retains_paused_jobs_without_automatic_manual_resume(tmp_path):
    store=Store(tmp_path);a=queued(store,'a');store.claim();store.pause(a);store.recover()
    assert store.job(a)['status']=='paused' and store.claim() is None
    store.resume(a);assert store.claim()['id']==a


def test_worker_handles_pause_as_paused_instead_of_failure(tmp_path,monkeypatch):
    from zilcho_subs import worker
    store=Store(tmp_path/'config');a=queued(store,'a');store.claim();store.pause(a)
    monkeypatch.setenv('ZILCHO_CONFIG',str(store.config));monkeypatch.setenv('ZILCHO_MEDIA',str(tmp_path))
    monkeypatch.setattr(worker.sys,'argv',['worker',a])
    def pause(*a,**k):raise JobPaused()
    monkeypatch.setattr(worker,'process_subtitle',pause)
    worker.main()
    assert store.job(a)['status']=='paused' and not store.job(a)['error']


def test_resume_preserves_already_published_target_and_reuses_completed_speech(tmp_path,monkeypatch):
    from zilcho_subs.pipeline import process_subtitle
    root=tmp_path/'media';root.mkdir();(root/'episode.mkv').write_bytes(b'synthetic')
    store=Store(tmp_path/'config');settings=Settings(target_languages=['es','fr'],prefer_existing=False)
    a=store.enqueue('subtitle',{'path':'episode.mkv','settings':settings.model_dump()});job=store.claim()
    class Engine:
        calls=0
        def speech(self,*args):self.calls+=1;return {'language':'ja','segments':[{'start':1,'end':2,'text':'source'}]}
        def translate(self,text,source,target):return target+' translated'
    engine=Engine();info={'duration':4,'streams':[{'index':0,'codec_type':'audio','tags':{'language':'jpn'}}]}
    original=store.update;paused=[False]
    def update(id,**fields):
        original(id,**fields)
        if 'es' in fields.get('outputs',{}) and not paused[0]:paused[0]=True;store.pause(id)
    monkeypatch.setattr(store,'update',update)
    def extract(media,index,out,duration):out.write_bytes(b'fake audio')
    with pytest.raises(JobPaused):process_subtitle(store,job,root,engine,lambda _:info,extract)
    first=(root/'episode.es.AI.srt').read_bytes()
    store.finish_pause(a);store.resume(a);resumed=store.claim()
    process_subtitle(store,resumed,root,engine,lambda _:info,extract)
    assert engine.calls==1 and store.job(a)['status']=='completed'
    assert set(store.job(a)['outputs'])=={'es','fr'}
    assert (root/'episode.es.AI.srt').read_bytes()==first
