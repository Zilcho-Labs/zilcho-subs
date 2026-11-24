from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from zilcho_subs.app import create_app
from zilcho_subs.core import Store, Settings
from zilcho_subs.text_subtitles import parse_srt, translate_document, process_text_job, try_existing
from zilcho_subs.pipeline import process_subtitle

SRT=chr(10).join(['1','00:00:01,250 --> 00:00:04,500','Hello world.','','2','00:00:03,500 --> 00:00:05,000','How are you?',''])
class TextEngine:
    def translate(self,text,source,target): return 'Translated '+text
    def speech(self,*args): raise AssertionError('Text translation must never run speech inference')

def fake_pack(config,src,dst,to_name=None):
    import json
    folder=config/'packages'/f'{src}_{dst}'; folder.mkdir(parents=True,exist_ok=True)
    meta={'from_code':src,'to_code':dst,'from_name':src,'to_name':to_name or dst}
    (folder/'metadata.json').write_text(json.dumps(meta),encoding='utf-8')

def test_reachable_targets_direct_chained_none(tmp_path):
    from zilcho_subs.pipeline import installed_packs, reachable_targets, targets_by_source
    fake_pack(tmp_path,'ja','en'); fake_pack(tmp_path,'en','es','Spanish'); fake_pack(tmp_path,'en','xx','Klingon')
    packs=installed_packs(tmp_path)
    assert reachable_targets(packs,'en')==['es','xx']
    assert reachable_targets(packs,'ja')==['en','es','xx']
    assert reachable_targets(packs,'ko')==[]
    names=targets_by_source(packs,{'en':'English','es':'Spanish'})
    assert {'code':'xx','name':'Klingon'} in names['ja'] and {'code':'en','name':'English'} in names['ja']
    assert 'ko' not in names and 'es' not in names

def test_subtitles_validate_targets_on_server(tmp_path):
    media=tmp_path/'media'; media.mkdir()
    app=create_app(tmp_path/'config',media,'test-password-123',run_workers=False)
    with TestClient(app) as client:
        client.headers['Origin']='http://testserver'
        client.post('/api/login',json={'password':'test-password-123'})
        fake_pack(app.state.store.config,'en','es','Spanish')
        state=client.get('/api/state').json()
        assert state['targets_by_source']['en']==[{'code':'es','name':'Spanish'}]
        for target in ['fr','en','E!']:
            assert client.post('/api/subtitles',json={'text':SRT,'filename':'a.srt','source':'en','targets':[target]}).status_code==400
        assert client.post('/api/subtitles',json={'text':SRT,'filename':'a.srt','source':'ja','targets':['es']}).status_code==400
        assert client.post('/api/subtitles',json={'text':SRT,'filename':'a.srt','source':'en','targets':['es']}).status_code==200

def test_timestamps_overlap_and_plain_text():
    source=SRT.replace('Hello world.','<i>Hello world.</i>')
    result=translate_document(source,'en',['es'],TextEngine())['es']
    assert [c['timing'] for c in parse_srt(result)]==[c['timing'] for c in parse_srt(source)]
    assert '<i>' not in result
    assert translate_document(SRT,'en',['en'],object())['en']==SRT

@pytest.mark.parametrize('text',['bad',SRT.replace('00:00:01,250','00:60:01,250'),SRT.replace('Hello world.',''),SRT.replace('Hello world.',chr(0))])
def test_reject_bad_srt(text):
    with pytest.raises(ValueError): parse_srt(text)

def test_upload_pick_download_and_edit(tmp_path):
    media=tmp_path/'media'; media.mkdir(); video=media/'episode.mkv'; video.write_bytes(b'fake')
    source=media/'episode.en.srt'; source.write_text(SRT,encoding='utf-8')
    app=create_app(tmp_path/'config',media,'test-password-123',run_workers=False)
    with TestClient(app) as client:
        client.headers['Origin']='http://testserver'
        client.post('/api/login',json={'password':'test-password-123'})
        for body in [{'text':SRT,'source':'auto'},{'source_path':'../secret.srt','source':'en'},{'text':SRT,'source_path':source.name,'source':'en'},{'text':'bad','source':'en'}]:
            assert client.post('/api/subtitles',json=body).status_code==400
        store=app.state.store
        fake_pack(store.config,'en','es','Spanish')
        for body,attached in [({'text':SRT,'filename':'hello.en.srt','source':'en','targets':['es']},False),({'source_path':source.name,'source':'en','targets':['es']},True)]:
            response=client.post('/api/subtitles',json=body)
            assert response.status_code==200,response.text
            id=response.json()['id']; job=store.job(id)
            assert bool(job['spec']['video_path'])==attached
            process_text_job(store,job,media,TextEngine())
            result=client.get(f'/api/jobs/{id}/subtitles/es')
            assert result.status_code==200 and 'Translated' in result.text
            assert [c['timing'] for c in parse_srt(result.text)]==[c['timing'] for c in parse_srt(SRT)]
            download=client.get(f'/api/jobs/{id}/download/es')
            assert 'attachment' in download.headers['content-disposition']
            before=store.job(id)['outputs']['es']
            edit=client.post(f'/api/jobs/{id}/subtitles/es',json={'text':result.text.replace('Translated','Edited')})
            assert edit.status_code==200 and edit.json()['path']!=before
        assert source.read_text(encoding='utf-8')==SRT and video.read_bytes()==b'fake'
        assert not list((store.config/'models').iterdir())

def test_automatic_prefers_srt_and_missing_route_falls_back(tmp_path):
    media=tmp_path/'media'; media.mkdir(); video=media/'episode.mkv'; video.write_bytes(b'fake')
    source=media/'episode.en.srt'; source.write_text(SRT,encoding='utf-8')
    store=Store(tmp_path/'config'); settings=Settings(target_languages=['es'])
    id=store.enqueue('subtitle',{'path':video.name,'settings':settings.model_dump()})
    def no_audio(*args): raise AssertionError('Audio extraction should not be needed')
    process_subtitle(store,store.job(id),media,TextEngine(),lambda _: {'streams':[],'duration':6},no_audio)
    assert store.job(id)['status']=='completed'
    assert 'no speech model' in store.job(id)['stage']
    class NoRoute:
        def translate(self,*args): raise ValueError('No installed translation route: en to es')
    assert try_existing(video,['fr'],NoRoute(),lambda *_:None) is None
    assert source.read_text(encoding='utf-8')==SRT

@pytest.mark.parametrize('prefer,missing_route',[(True,True),(False,False)])
def test_audio_fallback_and_preference_switch(tmp_path,prefer,missing_route):
    media=tmp_path/'media'; media.mkdir(); video=media/'episode.mkv'; video.write_bytes(b'fake')
    (media/'episode.en.srt').write_text(SRT,encoding='utf-8')
    store=Store(tmp_path/'config')
    settings=Settings(target_languages=['es'],prefer_existing=prefer,direct_english=False)
    id=store.enqueue('subtitle',{'path':video.name,'settings':settings.model_dump()})
    class Engine:
        calls=0
        def speech(self,*args):
            self.calls+=1
            return {'language':'ja','segments':[{'start':0,'end':2,'text':'Audio source'}]}
        def translate(self,text,source,target):
            if source=='en':
                assert missing_route, 'Disabled SRT preference must not translate the SRT'
                raise ValueError('No installed translation route from en to es')
            return 'Translated audio'
    engine=Engine()
    info={'duration':6,'streams':[{'codec_type':'audio','index':0,'tags':{'language':'jpn'}}]}
    process_subtitle(store,store.job(id),media,engine,lambda _:info,lambda _m,_i,out,_d:out.write_bytes(b'audio'))
    assert engine.calls==1 and store.job(id)['status']=='completed'
    assert 'Translated audio' in (media/'episode.es.AI.srt').read_text(encoding='utf-8')
