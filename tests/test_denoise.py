"""Dialogue cleanup (DeepFilterNet3): audio maths, chunking, download checks, pipeline use.

The real model isn't downloaded in tests. The numpy port itself was checked against the
official PyTorch DeepFilterNet3 on a real episode clip (correlation 1.00000, largest sample
difference 0.00004); these tests pin the parts that must never drift.
"""
import hashlib
import io
import json
import tarfile
import wave

import numpy as np
import pytest

from zilcho_subs import denoise
from zilcho_subs.core import Settings, Store
from zilcho_subs.pipeline import LocalEngine, process_subtitle


def test_erb_bands_match_libdf():
    widths = denoise.erb_widths()
    assert widths.sum() == 481 and len(widths) == 32
    assert widths[:8].tolist() == [2] * 8 and widths[-4:].tolist() == [42, 50, 56, 67]


def test_analysis_and_synthesis_reconstruct_the_audio():
    rng = np.random.default_rng(1)
    audio = (rng.standard_normal(48000) * 0.1).astype(np.float32)
    spec = denoise.analysis(np.concatenate([audio, np.zeros(denoise.FFT, np.float32)]))
    out = denoise.synthesis(spec)[denoise.FFT - denoise.HOP:][:len(audio)]
    assert np.max(np.abs(out - audio)) < 1e-5


class Session:
    """Stands in for one ONNX model: a pass-through mask and deep filter."""
    def __init__(self, kind): self.kind = kind

    def run(self, _outputs, feeds):
        if self.kind == 'enc':
            frames = feeds['feat_erb'].shape[2]
            return [np.zeros((1, 64, frames, 32), np.float32)] * 4 + [np.zeros((1, frames, 512), np.float32),
                    np.zeros((1, 64, frames, 96), np.float32), np.zeros((1, frames, 1), np.float32)]
        frames = feeds['emb'].shape[1]
        if self.kind == 'erb_dec':
            return [np.ones((1, 1, frames, 32), np.float32)]
        coefs = np.zeros((1, frames, 96, denoise.ORDER, 2), np.float32)
        coefs[:, :, :, denoise.ORDER - 1 - denoise.DF_LOOKAHEAD, 0] = 1  # select the current frame
        return [coefs.reshape(1, frames, 96, denoise.ORDER * 2), np.zeros((1, frames, 1), np.float32)]


def passthrough():
    cleaner = denoise.DialogueCleanup.__new__(denoise.DialogueCleanup)
    cleaner.enc, cleaner.erb_dec, cleaner.df_dec = Session('enc'), Session('erb_dec'), Session('df_dec')
    cleaner.widths = denoise.erb_widths()
    return cleaner


def test_mask_and_deep_filter_plumbing_keeps_audio_aligned():
    rng = np.random.default_rng(2)
    audio = (rng.standard_normal(30000) * 0.1).astype(np.float32)
    out = passthrough().enhance(audio)
    assert len(out) == len(audio) and np.max(np.abs(out - audio)) < 1e-5


def write_wav(path, samples, rate=48000):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setframerate(rate); w.setsampwidth(2)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())


def read_wav(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(np.float32) / 32768


def test_long_audio_is_cleaned_in_chunks_without_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(denoise, 'CHUNK', 7000)
    monkeypatch.setattr(denoise, 'WARMUP', 3000)
    rng = np.random.default_rng(3)
    audio = (rng.standard_normal(25000) * 0.1).astype(np.float32)
    write_wav(tmp_path / 'in.wav', audio)
    seen = []
    denoise.clean_file(tmp_path / 'in.wav', tmp_path / 'out.wav', passthrough(), seen.append)
    out = read_wav(tmp_path / 'out.wav')
    assert len(out) == len(audio) and np.max(np.abs(out - read_wav(tmp_path / 'in.wav'))) < 2e-4
    assert seen[-1] == 1.0 and len(seen) == 4
    write_wav(tmp_path / 'low.wav', audio, rate=16000)
    with pytest.raises(ValueError, match='48 kHz'):
        denoise.clean_file(tmp_path / 'low.wav', tmp_path / 'x.wav', passthrough())


def fake_archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo('tmp/export/' + name); info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def test_download_is_verified_before_anything_is_installed(tmp_path, monkeypatch):
    files = {'enc.onnx': b'enc', 'erb_dec.onnx': b'erb', 'df_dec.onnx': b'df', 'config.ini': b'[df]'}
    data = fake_archive(files)
    # The real pinned hashes refuse anything else.
    with pytest.raises(ValueError, match='SHA-256'):
        denoise.download_model(tmp_path / 'config', tmp_path / 'work1', fetch=lambda _url: data)
    assert not denoise.model_dir(tmp_path / 'config').exists()
    monkeypatch.setattr(denoise, 'MODEL_SHA256', hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(denoise, 'MODEL_FILES', {n: hashlib.sha256(files[n]).hexdigest() for n in ('enc.onnx', 'erb_dec.onnx', 'df_dec.onnx')})
    denoise.download_model(tmp_path / 'config', tmp_path / 'work2', fetch=lambda _url: data)
    root = denoise.model_dir(tmp_path / 'config')
    assert denoise.model_ready(tmp_path / 'config') and (root / 'LICENSE.txt').is_file()
    assert json.loads((root / 'ready.json').read_text())['license'] == 'MIT OR Apache-2.0'
    with pytest.raises(ValueError, match='already exists'):
        denoise.download_model(tmp_path / 'config', tmp_path / 'work3', fetch=lambda _url: data)
    bad = fake_archive({**files, 'enc.onnx': b'tampered'})
    monkeypatch.setattr(denoise, 'MODEL_SHA256', hashlib.sha256(bad).hexdigest())
    with pytest.raises(ValueError, match='Unexpected'):
        denoise.download_model(tmp_path / 'other', tmp_path / 'work4', fetch=lambda _url: bad)


INFO = {'duration': 10, 'streams': [{'index': 0, 'codec_type': 'audio', 'tags': {'language': 'jpn'}}]}


class Engine:
    def __init__(self): self.speech_audio, self.cleaned, self.calls = [], [], 0

    def clean(self, audio48, destination, settings, progress):
        self.cleaned.append(audio48.name); destination.write_bytes(b'cleaned'); progress(1.0)

    def speech(self, audio, settings, task, progress):
        self.calls += 1; self.speech_audio.append((audio.name, audio.read_bytes()))
        return {'language': 'ja', 'segments': [{'start': 0, 'end': 2, 'text': 'Hello'}]}

    def translate(self, text, source, target): return text


@pytest.fixture
def library(tmp_path):
    root = tmp_path / 'media'; root.mkdir(); (root / 'episode.mkv').write_bytes(b'synthetic media')
    return Store(tmp_path / 'config'), root


def run(store, root, engine, **extra):
    rates = []
    def extract(media, index, out, duration, rate=16000):
        rates.append(rate); out.write_bytes(b'audio-%d' % rate)
    store.enqueue('subtitle', {'path': 'episode.mkv', 'settings': store.settings().model_dump(), **extra})
    process_subtitle(store, store.claim(), root, engine, lambda _: INFO, extract)
    return rates


def test_cleanup_feeds_cleaned_audio_to_speech_and_keeps_its_own_cache(library):
    store, root = library
    engine = Engine()
    assert run(store, root, engine) == [16000] and engine.speech_audio[-1] == ('audio.wav', b'audio-16000')
    store.save_settings(Settings(clean_dialogue=True))
    assert run(store, root, engine, regenerate=True) == [16000, 48000]
    assert engine.cleaned == ['audio-48k.wav'] and engine.speech_audio[-1] == ('speech.wav', b'cleaned')
    assert engine.calls == 2  # a cleaned transcript never reuses the uncleaned cache
    run(store, root, engine, regenerate=True)
    assert engine.calls == 2  # ...but reuses its own
    store.save_settings(Settings(clean_dialogue=False))
    run(store, root, engine, regenerate=True)
    assert engine.calls == 2  # turning it off goes back to the original cache


def test_cleanup_needs_its_model(library, monkeypatch):
    store, root = library
    monkeypatch.setattr('zilcho_subs.pipeline.model_ready', lambda *_: True)  # speech model present
    store.save_settings(Settings(clean_dialogue=True))
    with pytest.raises(ValueError, match='Dialogue cleanup'):
        run(store, root, LocalEngine(store.config))
