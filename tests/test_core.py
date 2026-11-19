from concurrent.futures import ThreadPoolExecutor
import pytest
from zilcho_subs.core import Store, Settings, media_path, safe_relative, allowed_hour, render_srt, validate_srt, write_sidecar
from zilcho_subs.pipeline import select_audio, existing_languages, process_subtitle
from zilcho_subs.runner import scan_once

@pytest.fixture
def setup(tmp_path):
    root=tmp_path/'media'; root.mkdir(); video=root/'episode.mkv'; video.write_bytes(b'synthetic media')
    return Store(tmp_path/'config'),root,video

@pytest.mark.parametrize('path',['../secret','/etc/passwd','a/../../secret',r'..\secret',r'\server\share','C:/secret','bad\0name'])
def test_paths(path):
    with pytest.raises(ValueError): safe_relative(path)

def test_symlink(setup):
    _,root,video=setup
    try: (root/'linked.mkv').symlink_to(video)
    except OSError: pytest.skip('Symlink privilege unavailable')
    with pytest.raises(ValueError): media_path(root,'linked.mkv',file=True)

def test_settings_and_schedule(setup):
    store,_,_=setup
    store.save_settings(Settings(target_languages=['en','es','en']))
    assert store.settings().target_languages==['en','es']
    for kwargs in [{'model':'turbo'},{'target_languages':['../en']},{'cpu_threads':0},{'scan_dirs':['../private']}]:
        with pytest.raises(ValueError): Settings(**kwargs)
    s=Settings(schedule_start=22,schedule_end=5)
    assert allowed_hour(s,23) and allowed_hour(s,4) and not allowed_hour(s,12)
    assert allowed_hour(Settings(),12)

def test_atomic_queue_and_recovery(setup):
    store,_,_=setup
    with ThreadPoolExecutor(8) as pool: ids=list(pool.map(lambda _:store.enqueue('subtitle',{'path':'episode.mkv'}),range(20)))
    assert len(set(ids))==1
    with ThreadPoolExecutor(8) as pool: jobs=list(pool.map(lambda _:store.claim(),range(8)))
    assert sum(j is not None for j in jobs)==1
    store.cancel(ids[0]); assert store.job(ids[0])['status']=='cancelling'
    store.recover(); assert store.job(ids[0])['status']=='failed'
    id=store.enqueue('subtitle',{'path':'episode.mkv'}); assert id!=ids[0]
    store.cancel(id); assert store.job(id)['status']=='cancelled'

def test_srt_and_non_overwrite(setup):
    _,_,video=setup
    text=render_srt([{'start':0,'end':5,'text':'Some words '*30}]); assert validate_srt(text)==text
    assert all(len(line)<=42 for line in text.splitlines() if '-->' not in line and not line.isdigit())
    first=write_sidecar(video,'en',text)
    with pytest.raises(FileExistsError): write_sidecar(video,'en','replacement')
    second=write_sidecar(video,'en',text,version=True)
    assert first!=second and first.read_text()==text and video.read_bytes()==b'synthetic media'
    bad_cases=['', chr(10).join(['1','00:00:02,000 --> 00:00:01,000','bad']), chr(10).join(['1','00:00:00,000 --> 00:00:01,000','<script>bad</script>'])]
    for bad in bad_cases:
        with pytest.raises(ValueError): validate_srt(bad)
    with pytest.raises(ValueError): render_srt([{'start':float('nan'),'end':2,'text':'bad'}])

INFO={'duration':10,'streams':[{'index':0,'codec_type':'video'},{'index':1,'codec_type':'audio','tags':{'language':'eng'}},{'index':2,'codec_type':'audio','tags':{'language':'jpn'}}]}
class FakeEngine:
    def __init__(self): self.calls=0
    def speech(self,audio,settings,task,progress):
        self.calls+=1; progress(5); return {'language':'ja','segments':[{'start':0,'end':3,'text':'Hello' if task=='translate' else 'original'}]}
    def translate(self,text,source,target): return target+': '+text

def extract_fake(media,index,out,duration): out.write_bytes(b'fake audio')
def enqueue(store,**extra):
    store.enqueue('subtitle',{'path':'episode.mkv','settings':store.settings().model_dump(),**extra}); return store.claim()

def test_audio_and_existing_subtitles(setup):
    _,_,video=setup
    assert select_audio(INFO)['index']==2 and select_audio(INFO,'en')['index']==1
    assert select_audio(INFO,stream=1)['index']==1
    with pytest.raises(ValueError): select_audio(INFO,'ko')
    (video.parent/'episode.en.forced.srt').write_text('forced'); assert 'en' not in existing_languages(video,INFO)
    (video.parent/'episode.en.srt').write_text('full'); assert 'en' in existing_languages(video,INFO)

def test_english_skip_and_cache(setup):
    store,root,_=setup; engine=FakeEngine(); job=enqueue(store)
    process_subtitle(store,job,root,engine,lambda _:INFO,extract_fake)
    first=store.job(job['id']); assert first['status']=='completed' and first['outputs']['en']=='episode.en.AI.srt'
    assert 'Hello' in (root/first['outputs']['en']).read_text()
    job=enqueue(store); process_subtitle(store,job,root,engine,lambda _:INFO,extract_fake)
    assert store.job(job['id'])['status']=='skipped' and engine.calls==1
    job=enqueue(store,regenerate=True); process_subtitle(store,job,root,engine,lambda _:INFO,extract_fake)
    assert engine.calls==1 and store.job(job['id'])['outputs']['en']=='episode.en.AI.2.srt'

def test_multiple_languages_share_transcript(setup):
    store,root,_=setup; store.save_settings(Settings(target_languages=['es','fr'],direct_english=False)); engine=FakeEngine(); job=enqueue(store)
    process_subtitle(store,job,root,engine,lambda _:INFO,extract_fake)
    result=store.job(job['id']); assert set(result['outputs'])=={'es','fr'} and engine.calls==1
    assert 'es: original' in (root/result['outputs']['es']).read_text()

def test_cancel_and_changed_media(setup):
    store,root,video=setup; job=enqueue(store); store.cancel(job['id'])
    with pytest.raises(InterruptedError): process_subtitle(store,job,root,FakeEngine(),lambda _:INFO,extract_fake)
    assert not list(root.glob('*.srt')); store.update(job['id'],status='cancelled')
    class Changing(FakeEngine):
        def speech(self,*args):
            result=super().speech(*args); video.write_bytes(b'changed media file'); return result
    job=enqueue(store)
    with pytest.raises(ValueError,match='changed'): process_subtitle(store,job,root,Changing(),lambda _:INFO,extract_fake)
    assert not list(root.glob('*.srt'))

def test_watcher_stability(setup):
    store,root,video=setup; store.save_settings(Settings(stable_seconds=30))
    assert not scan_once(store,root,now=100) and not scan_once(store,root,now=120)
    assert len(scan_once(store,root,now=131))==1
    assert not scan_once(store,root,now=200)
    video.write_bytes(b'changed'); assert not scan_once(store,root,now=201)

def test_watcher_rotates_large_library(setup):
    store,root,_=setup; store.save_settings(Settings(stable_seconds=30))
    for i in range(5): (root/f'{i}.mkv').write_bytes(b'fake')
    for i in range(3): scan_once(store,root,now=100,limit=2)
    for i in range(3): scan_once(store,root,now=140,limit=2)
    assert len(store.jobs())==6


def test_audio_offset(setup):
    store,root,_=setup; job=enqueue(store)
    info={**INFO,'format':{'start_time':'1.0'},'streams':[{'index':2,'codec_type':'audio','start_time':'2.5','tags':{'language':'jpn'}}]}
    process_subtitle(store,job,root,FakeEngine(),lambda _:info,extract_fake)
    output=root/store.job(job['id'])['outputs']['en']
    # Start carries the stream offset; the end also gets the short reading linger.
    assert '00:00:01,500 --> 00:00:04,900' in output.read_text()

def test_pause_does_not_requeue(setup):
    store,root,_=setup; store.save_settings(Settings(stable_seconds=30))
    scan_once(store,root,now=100); scan_once(store,root,now=131)
    store.save_settings(Settings(stable_seconds=30,paused=True))
    assert not scan_once(store,root,now=200)


def test_queue_capacity_is_atomic(setup):
    store,_,_=setup
    import time
    with store.db() as db:
        db.executemany("INSERT INTO jobs(id,kind,spec,dedupe,status,stage,created,updated) VALUES(?,?,?,?,?,?,?,?)",[(str(i),'subtitle','{}','key'+str(i),'queued','Waiting',time.time(),time.time()) for i in range(499)])
    def add(i):
        try: return store.enqueue('subtitle',{'new':i})
        except ValueError: return None
    with ThreadPoolExecutor(8) as pool: results=list(pool.map(add,range(16)))
    assert sum(x is not None for x in results)==1
    with store.db() as db: assert db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]==500
    index=next(i for i,x in enumerate(results) if x)
    assert store.enqueue('subtitle',{'new':index})==results[index]


def test_translation_routes_choose_direct_then_shortest_pivot():
    from types import SimpleNamespace as P
    from zilcho_subs.pipeline import translation_route, WholeCue
    a=P(from_code='ja',to_code='en',type='translate'); b=P(from_code='en',to_code='es',type='translate'); direct=P(from_code='ja',to_code='es',type='translate')
    assert translation_route([a,b,direct],'ja','es')==[direct]
    assert translation_route([a,b],'ja','es')==[a,b]
    assert translation_route([a,b],'en','en')==[]
    with pytest.raises(ValueError,match='No installed'): translation_route([a,b],'es','ja')
    assert WholeCue().split_sentences('One cue. Another sentence.')==['One cue. Another sentence.']


def test_legacy_bad_timing_cache_is_not_reused(setup):
    import hashlib,json
    store,root,video=setup; settings=store.settings();fingerprint=(video.stat().st_size,video.stat().st_mtime_ns)
    old_key=hashlib.sha256(json.dumps([str(video),fingerprint,2,settings.model,'','translate','auto']).encode()).hexdigest()
    (store.config/'transcripts'/(old_key+'.json')).write_text(json.dumps({'language':'ja','segments':[{'start':0,'end':153,'text':'Bad cached timing'}]}))
    engine=FakeEngine();job=enqueue(store,regenerate=True)
    process_subtitle(store,job,root,engine,lambda _:INFO,extract_fake)
    assert engine.calls==1
    assert 'Bad cached timing' not in (root/store.job(job['id'])['outputs']['en']).read_text()
