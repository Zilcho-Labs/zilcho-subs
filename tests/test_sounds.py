import json

import numpy as np
import pytest

from zilcho_subs.core import Settings, Store, validate_srt
from zilcho_subs.pipeline import process_subtitle
from zilcho_subs.sounds import (SAMPLE_RATE, SOUND_VERSION, add_sound_captions, detect_sounds, fbank, features,
                                sound_model_ready)

INFO = {'duration': 10, 'streams': [{'index': 0, 'codec_type': 'video'}, {'index': 1, 'codec_type': 'audio', 'tags': {'language': 'eng'}}]}


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'media'; root.mkdir(); (root / 'episode.mkv').write_bytes(b'synthetic media')
    return Store(tmp_path / 'config'), root, root / 'episode.mkv'


def extract_fake(media, index, out, duration): out.write_bytes(b'fake audio')


def enqueue(store, **extra):
    store.enqueue('subtitle', {'path': 'episode.mkv', 'settings': store.settings().model_dump(), **extra}); return store.claim()


class FakeEngine:
    def __init__(self): self.calls = 0
    def translate(self, text, source, target): return target + ': ' + text


def test_features_match_the_ast_input_shape():
    clip = np.random.default_rng(1).normal(0, .1, 3 * SAMPLE_RATE)
    assert fbank(clip).shape == (1 + (3 * SAMPLE_RATE - 400) // 160, 128)
    f = features(clip)
    assert f.shape == (1, 1024, 128) and f.dtype == np.float32 and np.isfinite(f).all()


class LoudnessTagger:
    """Stands in for the model: 'hears a cat' wherever the audio is loud."""
    watch = {0: '[Cat meowing]', 1: '[Dog barking]'}
    def scores(self, clip): return {0: 0.9 if np.abs(clip).max() > .5 else 0.01, 1: 0.0}


def test_detection_merges_windows_into_one_event_near_the_sound():
    audio = np.zeros(30 * SAMPLE_RATE, dtype=np.float32); audio[9 * SAMPLE_RATE:12 * SAMPLE_RATE] = .8
    events = detect_sounds(audio, LoudnessTagger())
    assert [e['text'] for e in events] == ['[Cat meowing]']
    assert 7.5 <= events[0]['start'] <= 9.0 and 12.0 <= events[0]['end'] <= 13.5


def test_quiet_audio_gives_no_captions():
    assert detect_sounds(np.zeros(20 * SAMPLE_RATE, dtype=np.float32), LoudnessTagger()) == []


def test_sound_in_a_gap_gets_its_own_cue_without_touching_dialogue():
    cues = [{'start': 1, 'end': 3, 'text': 'Hello'}, {'start': 9, 'end': 11, 'text': 'Huh, kitty?'}]
    merged = add_sound_captions(cues, [{'start': 2.5, 'end': 8, 'text': '[Cat meowing]'}])
    assert merged[0] == cues[0] and merged[2] == cues[1]
    assert merged[1]['text'] == '[Cat meowing]' and merged[1]['start'] == pytest.approx(3.084) and merged[1]['end'] == pytest.approx(8)


def test_sound_during_a_line_is_written_in_front_of_it():
    merged = add_sound_captions([{'start': 5, 'end': 9, 'text': 'Huh, kitty?'}], [{'start': 5.5, 'end': 8, 'text': '[Cat meowing]'}])
    assert merged == [{'start': 5, 'end': 9, 'text': '[Cat meowing] Huh, kitty?'}]


def test_sound_in_a_tiny_gap_is_skipped():
    cues = [{'start': 1, 'end': 3, 'text': 'A'}, {'start': 3.5, 'end': 6, 'text': 'B'}]
    assert add_sound_captions(cues, [{'start': 2.8, 'end': 3.7, 'text': '[Dog barking]'}]) == cues


class SoundEngine(FakeEngine):
    def __init__(self): super().__init__(); self.sound_calls = 0
    def speech(self, audio, settings, task, progress):
        self.calls += 1
        return {'language': 'en', 'segments': [{'start': 0, 'end': 2, 'text': 'Here, kitty.'}, {'start': 7, 'end': 9, 'text': 'Good cat.'}]}
    def sounds(self, audio, settings, progress):
        self.sound_calls += 1; progress(5); return [{'start': 3, 'end': 6, 'text': '[Cat meowing]', 'score': .9}]


def test_english_subtitles_include_sound_captions_and_reuse_the_scan(setup):
    store, root, _ = setup
    store.save_settings(Settings(source_language='en', sound_captions=True))
    engine = SoundEngine()
    job = enqueue(store)
    process_subtitle(store, job, root, engine, lambda _: INFO, extract_fake)
    text = (root / store.job(job['id'])['outputs']['en']).read_text(encoding='utf-8')
    validate_srt(text)
    assert '[Cat meowing]' in text and text.index('Here, kitty.') < text.index('[Cat meowing]') < text.index('Good cat.')
    cached = list((store.config / 'transcripts').glob('*.sounds.json'))
    assert len(cached) == 1 and json.loads(cached[0].read_text())[0]['text'] == '[Cat meowing]'
    # A regeneration reuses both the transcript and the sound scan.
    job = enqueue(store, regenerate=True)
    process_subtitle(store, job, root, engine, lambda _: INFO, extract_fake)
    assert engine.calls == 1 and engine.sound_calls == 1


def test_speech_timing_change_does_not_repeat_the_sound_scan(setup, monkeypatch):
    store, root, _ = setup
    store.save_settings(Settings(source_language='en', sound_captions=True))
    engine = SoundEngine()
    process_subtitle(store, enqueue(store), root, engine, lambda _: INFO, extract_fake)
    import zilcho_subs.timing
    monkeypatch.setattr(zilcho_subs.timing, 'TIMING_VERSION', 'some-later-version')
    process_subtitle(store, enqueue(store, regenerate=True), root, engine, lambda _: INFO, extract_fake)
    assert engine.calls == 2 and engine.sound_calls == 1


class WordsEngine(SoundEngine):
    def speech(self, audio, settings, task, progress):
        self.calls += 1
        text = "It's such a pleasure to see you again. It's been some time."
        words = [[i * .3, i * .3 + .25, (' ' if i else '') + w] for i, w in enumerate(text.split(' '))]
        words = [[a + (.5 if i > 7 else 0), b + (.5 if i > 7 else 0), w] for i, (a, b, w) in enumerate(words)]
        return {'language': 'en', 'segments': [{'start': 0, 'end': words[-1][1], 'text': text, 'words': words}]}


def test_long_english_line_is_split_in_the_written_subtitle(setup):
    store, root, _ = setup
    store.save_settings(Settings(source_language='en'))
    job = enqueue(store)
    process_subtitle(store, job, root, WordsEngine(), lambda _: INFO, extract_fake)
    text = (root / store.job(job['id'])['outputs']['en']).read_text(encoding='utf-8')
    validate_srt(text)
    assert text.count('-->') == 2
    assert "It's such a pleasure to see you again.\n" in text and "It's been some time." in text


def test_sound_captions_off_by_default(setup):
    store, root, _ = setup
    store.save_settings(Settings(source_language='en'))
    engine = SoundEngine(); job = enqueue(store)
    process_subtitle(store, job, root, engine, lambda _: INFO, extract_fake)
    assert engine.sound_calls == 0
    assert '[Cat meowing]' not in (root / store.job(job['id'])['outputs']['en']).read_text(encoding='utf-8')


def test_sound_model_not_ready_without_files(tmp_path):
    assert not sound_model_ready(tmp_path)
