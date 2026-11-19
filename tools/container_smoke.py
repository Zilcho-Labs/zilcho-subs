"""CI-only real inference check. Run prepare online, then process with Docker network disconnected.
Never imports user media: the fixture is whisper.cpp's public JFK speech sample.
"""
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

BASE='http://127.0.0.1:8765'
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def api(path,body=None):
    payload=None if body is None else json.dumps(body).encode()
    request=urllib.request.Request(BASE+'/api'+path,data=payload,headers={'Origin':BASE,'Content-Type':'application/json'})
    with opener.open(request,timeout=60) as response: return json.load(response)
def wait(id,seconds=360):
    until=time.time()+seconds
    while time.time()<until:
        job=next(j for j in api('/state')['jobs'] if j['id']==id)
        if job['status']=='completed': return job
        if job['status'] in ('failed','cancelled','skipped'): raise RuntimeError(str(job))
        time.sleep(2)
    raise TimeoutError('Job did not complete: '+id)

for attempt in range(30):
    try:
        api('/health'); break
    except (OSError, urllib.error.URLError):
        time.sleep(1)
else: raise RuntimeError('Service did not become ready')
api('/login',{'password':os.environ['ZILCHO_PASSWORD']})
if sys.argv[1]=='prepare':
    wait(api('/assets',{'kind':'model','model':'tiny'})['id'])
    wait(api('/assets',{'kind':'catalog'})['id'])
    wait(api('/assets',{'kind':'pack','source':'en','target':'es'})['id'])
    sample=Path('/media/jfk.wav')
    urllib.request.urlretrieve('https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/samples/jfk.wav',sample)
    subprocess.run(['ffmpeg','-nostdin','-y','-v','error','-i',str(sample),'-c:a','pcm_s16le','-metadata:s:a:0','language=eng','/media/sample.mkv'],check=True)
    subprocess.run(['ffmpeg','-nostdin','-y','-v','error','-i',str(sample),'-af','adelay=9500','-c:a','pcm_s16le','-metadata:s:a:0','language=eng','/media/delayed-speech.mkv'],check=True)
    subprocess.run(['ffmpeg','-nostdin','-y','-v','error','-f','lavfi','-i','sine=frequency=440:duration=4','-af',r'aselect=not(between(t\,1\,3))','-c:a','pcm_s16le','/media/gapped-audio.mkv'],check=True)
    print('Tiny, en→es pack and public fixture ready. Disconnect the container network before processing.')
else:
    # Preserve packet-timeline gaps when converting to a sample-indexed WAV.
    import wave
    from zilcho_subs.pipeline import extract,probe
    gap=Path('/media/gapped-audio.mkv')
    extract(gap,0,Path('/tmp/gapped-audio.wav'),probe(gap)['duration'])
    with wave.open('/tmp/gapped-audio.wav','rb') as audio:
        assert 3.8<audio.getnframes()/audio.getframerate()<4.2
    print('PASS: FFmpeg extraction retains an interior two-second timestamp gap.')

    settings=api('/state')['settings']; settings.update(model='tiny',target_languages=['en','es'],cpu_threads=2,direct_english=False)
    api('/settings',settings)
    job=wait(api('/jobs',{'path':'sample.mkv'})['id'])
    assert set(job['outputs'])=={'en','es'},job
    english=(Path('/media')/job['outputs']['en']).read_text(encoding='utf-8')
    spanish=(Path('/media')/job['outputs']['es']).read_text(encoding='utf-8')
    assert 'country' in english.lower() and len(spanish)>50 and english!=spanish
    from zilcho_subs.core import validate_srt
    validate_srt(english); validate_srt(spanish)
    print('PASS: real tiny transcription + local Argos translation completed with network disconnected; valid English/Spanish tracks generated.')

    # A real leading-silence regression: speech starts after 9.5 seconds.
    settings.update(source_language='en',target_languages=['en'],direct_english=True)
    api('/settings',settings)
    delayed=wait(api('/jobs',{'path':'delayed-speech.mkv'})['id'])
    from zilcho_subs.text_subtitles import parse_srt
    timed=(Path('/media')/delayed['outputs']['en']).read_text(encoding='utf-8')
    cues=parse_srt(timed)
    first=cues[0]['timing'].split(' --> ')[0]
    def seconds(stamp):
        hours,minutes,tail=stamp.split(':');return int(hours)*3600+int(minutes)*60+float(tail.replace(',','.'))
    assert 9<=seconds(first)<14,first
    for cue in cues:
        start,end=cue['timing'].split(' --> ')
        assert seconds(end)-seconds(start)<=9,(start,end)
    assert 'country' in timed.lower(),timed
    print('PASS: actual inference retains 9.5-second leading silence and bounded speech cues.')

    # Real recognition checkpoint: completed windows must not be decoded twice.
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import get_speech_timestamps
    from zilcho_subs.core import Settings,JobPaused
    from zilcho_subs.timing import SpeechCheckpoint,recognize_utterances
    backend=WhisperModel('/config/models/tiny',device='cpu',compute_type='int8',cpu_threads=2,local_files_only=True)
    class CountedModel:
        def __init__(self):self.calls=0
        def transcribe(self,*args,**kwargs):self.calls+=1;return backend.transcribe(*args,**kwargs)
    checkpoint=SpeechCheckpoint('/tmp/resume-checkpoint.json','synthetic-jfk-fixture')
    audio=decode_audio('/media/jfk.wav',sampling_rate=16000)
    options=Settings(model='tiny',source_language='en')
    first_pass=CountedModel()
    def pause_after_saved_window(end):
        if checkpoint.load()['completed']>=1:raise JobPaused()
    try:recognize_utterances(first_pass,audio,options,'transcribe',pause_after_saved_window,get_speech_timestamps,checkpoint)
    except JobPaused:pass
    else:raise AssertionError('Expected a checkpoint pause')
    saved=checkpoint.load();assert saved['completed']==1 and first_pass.calls==1
    resumed_pass=CountedModel()
    resumed=recognize_utterances(resumed_pass,audio,options,'transcribe',lambda _:None,get_speech_timestamps,checkpoint)
    assert resumed_pass.calls==len(saved['intervals'])-1
    assert 'country' in ' '.join(c['text'] for c in resumed['segments']).lower()
    print('PASS: actual speech checkpoint resumes remaining windows without repeating the completed window.')
    del backend

    wait(api('/assets',{'kind':'remove_model','model':'tiny'})['id'])
    source='1'+chr(10)+'00:00:01,250 --> 00:00:03,500'+chr(10)+'Hello, how are you?'+chr(10)
    text_job=wait(api('/subtitles',{'text':source,'filename':'hello.en.srt','source':'en','targets':['es']})['id'])
    output=Path('/config')/text_job['outputs']['es'].removeprefix('config:')
    translated=output.read_text(encoding='utf-8')
    assert '00:00:01,250 --> 00:00:03,500' in translated and translated!=source
    assert not any(m['installed'] for m in api('/state')['models'])
    print('PASS: uploaded SRT translated offline with no speech models installed; original timing preserved.')
