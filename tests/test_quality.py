"""Subtitle Quality page (0.10.0): counts, confidence recording and the API.
Synthetic files and fake models only; nothing is downloaded or run."""
import math
from types import SimpleNamespace as N

import pytest
from fastapi.testclient import TestClient

from zilcho_subs.app import create_app
from zilcho_subs.core import Settings, Store
from zilcho_subs.pipeline import process_subtitle
from zilcho_subs.quality import rating, srt_counts
from zilcho_subs.timing import SAMPLE_RATE, confidence_totals, recognize_utterances

SRT = ('1\n00:00:01,000 --> 00:00:02,000\nHello there, <i>friend</i>.\n\n'
       '2\n00:00:03,000 --> 00:00:04,500\nTwo lines\nof text\n\n'
       'not a cue\n')


def test_counts_and_ratings():
    assert srt_counts(SRT) == (7, 2)
    assert srt_counts('﻿' + SRT.replace('\n', '\r\n')) == (7, 2)
    assert srt_counts('') == (0, 0)
    assert [rating(x) for x in (0.981, 0.95, 0.949, 0.90, 0.85, 0.849, None)] == \
        ['Excellent', 'Excellent', 'Good', 'Good', 'Fair', 'Poor', 'Not recorded']


def test_rating_boundaries_and_zero():
    assert [rating(x) for x in (0.0, 1.0, 0.8499, 0.85, 0.8999, 0.9, 0.9499, 0.95)] == \
        ['Poor', 'Excellent', 'Poor', 'Fair', 'Fair', 'Good', 'Good', 'Excellent']


def word(text, p): return N(word=text, probability=p, start=0, end=1)


def test_confidence_uses_word_probability_and_skips_non_speech():
    kept = N(text=' Hi there', words=[word(' Hi', 1.0), word(' there', 0.8)], no_speech_prob=0, avg_logprob=-0.2, compression_ratio=1)
    noise = N(text=' music', words=[word(' music', 0.1)], no_speech_prob=0.9, avg_logprob=-2, compression_ratio=1)
    translated = N(text=' Good morning', words=None, no_speech_prob=0, avg_logprob=math.log(0.5), compression_ratio=1)
    total, count = confidence_totals([kept, noise, translated])
    assert count == 4 and total == pytest.approx(1.0 + 0.8 + 0.5 + 0.5)


def test_recognition_reports_average_confidence():
    intervals = [{'start': SAMPLE_RATE, 'end': 3 * SAMPLE_RATE}]
    class Model:
        def transcribe(self, audio, **kwargs):
            return iter([N(text=' Hello world', words=[N(word=' Hello', start=0.0, end=0.5, probability=0.99),
                                                       N(word=' world', start=0.5, end=1.0, probability=0.95)],
                           no_speech_prob=0, avg_logprob=-0.1, compression_ratio=1)]), N(language='en')
    result = recognize_utterances(Model(), range(4 * SAMPLE_RATE), Settings(source_language='en'), 'transcribe',
                                  lambda _: None, lambda *a, **k: intervals)
    assert result['confidence'] == pytest.approx(0.97)
    assert [c['text'] for c in result['segments']] == ['Hello world']


INFO = {'duration': 10, 'streams': [{'index': 0, 'codec_type': 'video'}, {'index': 1, 'codec_type': 'audio', 'tags': {'language': 'jpn'}}]}


class Engine:
    def speech(self, audio, settings, task, progress):
        return {'language': 'ja', 'confidence': 0.912,
                'segments': [{'start': 0, 'end': 2, 'text': 'one two three'}, {'start': 3, 'end': 5, 'text': 'four'}]}
    def translate(self, text, source, target): return text


@pytest.fixture
def client(tmp_path):
    root = tmp_path / 'media'; root.mkdir()
    (root / 'episode.mkv').write_bytes(b'synthetic media')
    app = create_app(tmp_path / 'config', root, 'test-password-123', run_workers=False)
    with TestClient(app) as c:
        c.headers['Origin'] = 'http://testserver'
        assert c.post('/api/login', json={'password': 'test-password-123'}).status_code == 200
        yield c, Store(tmp_path / 'config'), root


def test_finished_job_records_quality_and_api_reports_it(client):
    c, store, root = client
    store.save_settings(Settings(target_languages=['en', 'es'], direct_english=False))
    store.enqueue('subtitle', {'path': 'episode.mkv', 'settings': store.settings().model_dump()}); job = store.claim()
    before = (root / 'episode.mkv').read_bytes()
    process_subtitle(store, job, root, Engine(), lambda _: INFO, lambda m, i, o, d: o.write_bytes(b'a'))
    report = c.get(f"/api/jobs/{job['id']}/quality").json()
    assert report['source'] == 'episode.mkv'
    files = {f['language']: f for f in report['files']}
    assert files['en'] == {'language': 'en', 'file': 'episode.en.AI.srt', 'path': 'episode.en.AI.srt',
                           'words': 4, 'segments': 2, 'confidence': 91.2, 'status': 'Good'}
    assert files['es']['status'] == 'Good'
    # Editing a file changes counts but keeps the recorded confidence.
    text = c.get(f"/api/jobs/{job['id']}/subtitles/en").text.replace('four', 'four five six')
    assert c.post(f"/api/jobs/{job['id']}/subtitles/en", json={'text': text}).status_code == 200
    edited = {f['language']: f for f in c.get(f"/api/jobs/{job['id']}/quality").json()['files']}['en']
    assert (edited['file'], edited['words'], edited['confidence']) == ('episode.en.AI.2.srt', 6, 91.2)
    assert (root / 'episode.mkv').read_bytes() == before


def test_older_jobs_get_counts_without_confidence(client):
    c, store, root = client
    (root / 'episode.en.AI.srt').write_text(SRT, encoding='utf-8')
    old = store.enqueue('subtitle', {'path': 'episode.mkv'}); store.claim()
    store.update(old, status='completed', outputs={'en': 'episode.en.AI.srt', 'fr': 'gone.fr.AI.srt'})
    store.enqueue('lyrics', {'path': 'song.mp3'})  # not a subtitle job: never listed
    page = c.get('/api/quality').json()
    assert page['total'] == 1 and page['counts'] == {'Not recorded': 1, 'File missing': 1}
    files = {f['language']: f for f in page['jobs'][0]['files']}
    assert (files['en']['words'], files['en']['segments'], files['en']['confidence']) == (7, 2, None)
    assert files['fr']['status'] == 'File missing'
    assert store.quality([old])[old]['en']['words'] == 7  # counted once, saved in app data
    assert c.post(f'/api/jobs/{old}/remove').status_code == 200 and store.quality([old]) == {}
    assert c.get('/api/jobs/nope/quality').status_code == 404


def test_zero_score_stays_zero_and_missing_stays_none(client):
    c, store, root = client
    (root / 'a.en.AI.srt').write_text(SRT, encoding='utf-8')
    (root / 'b.en.AI.srt').write_text(SRT, encoding='utf-8')
    scored = store.enqueue('subtitle', {'path': 'a.mkv'}); store.claim()
    store.update(scored, status='completed', outputs={'en': 'a.en.AI.srt'})
    store.record_quality(scored, 'en', 'a.en.AI.srt', 7, 2, confidence=0.0)
    legacy = store.enqueue('subtitle', {'path': 'b.mkv'}); store.claim()  # no saved score, like a legacy or SRT-only job
    store.update(legacy, status='completed', outputs={'en': 'b.en.AI.srt'})
    jobs = {j['id']: j['files'][0] for j in c.get('/api/quality').json()['jobs']}
    assert (jobs[scored]['confidence'], jobs[scored]['status']) == (0.0, 'Poor')
    assert (jobs[legacy]['confidence'], jobs[legacy]['status']) == (None, 'Not recorded')
