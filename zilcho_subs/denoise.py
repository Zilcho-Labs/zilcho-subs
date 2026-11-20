"""Optional dialogue cleanup before speech recognition, with DeepFilterNet3.

DeepFilterNet3 (Schröter et al., MIT or Apache-2.0) is a speech-enhancement model: it keeps
voices and removes background noise such as crowds, chanting, music and effects. That
lets the speech detector and Whisper hear lines that are buried in a loud scene. It's for
speech only: it removes singing too, so song lyrics never use it.

The user downloads the official ONNX export explicitly from Speech models (about 8 MB).
This module re-implements the model's audio preparation (libDF's STFT, ERB features and
normalisation, and DfNet's mask and deep filter) with numpy, so it runs on ONNX Runtime
alone: no PyTorch, no extra libraries. Checked against the official PyTorch pipeline on a
real episode clip: correlation 1.00000, largest sample difference 0.00004.
"""
import hashlib
import io
import json
import tarfile
import wave
from pathlib import Path

import numpy as np

# Official export in the DeepFilterNet repository, pinned to the commit that added it.
MODEL_COMMIT = '84d57ec2c08fe08e68a13fb32a58cd7092060a0f'
MODEL_URL = f'https://github.com/Rikorose/DeepFilterNet/raw/{MODEL_COMMIT}/models/DeepFilterNet3_onnx.tar.gz'
MODEL_SHA256 = 'c94d91f70911001c946e0fabb4aa9adc37045f45a03b56008cb0c8244cb63616'
MODEL_FILES = {'enc.onnx': '7c5399d3da8a50ebef1c1a0ae421b33376aa5e45d0e92df16da7e83c9c131916',
               'erb_dec.onnx': 'ab669a1d10afe20911728b33053a452071042317a90581092b325da7b2f9d895',
               'df_dec.onnx': '23114ce3b0f6464b763ee62f7bb8aab6b2a129a21eabd5bcfe59413db05f278a'}
CLEANUP_VERSION = 'dfn3-onnx-v1:' + MODEL_SHA256[:16]

SR, FFT, HOP = 48000, 960, 480
NB_ERB, NB_DF, ORDER, DF_LOOKAHEAD, CONV_LOOKAHEAD, MIN_ERB_FREQS = 32, 96, 5, 2, 2, 2
ALPHA = 0.99          # libDF get_norm_alpha(): exp(-hop / sr / tau), tau = 1 s, rounded
CHUNK = 120 * SR      # audio is cleaned in chunks so memory stays small for long films (~650 MB peak)
WARMUP = 30 * SR      # earlier audio replayed before each chunk: the model's memory needs ~30 s to settle


def model_dir(config): return Path(config) / 'models' / 'dialogue-cleanup'


def model_ready(config):
    root = model_dir(config)
    return (root / 'ready.json').is_file() and all((root / name).is_file() for name in MODEL_FILES)


def download_model(config, work, fetch=None):
    """Download the pinned archive, check every hash, then move the model into place."""
    if fetch is None:
        from urllib.request import Request, urlopen
        def fetch(url):
            with urlopen(Request(url, headers={'User-Agent': 'zilcho-subs'}), timeout=60) as response:
                return response.read(20_000_000)
    destination = model_dir(config)
    if destination.exists(): raise ValueError('The dialogue cleanup model folder already exists; remove it before reinstalling')
    data = fetch(MODEL_URL)
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ValueError('Dialogue cleanup download did not match its pinned SHA-256; nothing installed')
    staged = Path(work) / 'dialogue-cleanup'
    staged.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = {Path(m.name).name: m for m in archive.getmembers() if m.isfile()}
        for name, digest in MODEL_FILES.items():
            content = archive.extractfile(members[name]).read()
            if hashlib.sha256(content).hexdigest() != digest: raise ValueError('Unexpected dialogue cleanup model file')
            (staged / name).write_bytes(content)
    license_text = (Path(__file__).parent / 'static' / 'licenses' / 'deepfilternet.txt').read_text(encoding='utf-8')
    (staged / 'LICENSE.txt').write_text(license_text, encoding='utf-8')
    (staged / 'ready.json').write_text(json.dumps({'model': 'DeepFilterNet3', 'url': MODEL_URL, 'sha256': MODEL_SHA256,
                                                   'license': 'MIT OR Apache-2.0'}))
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(destination)


# --- libDF audio preparation, in numpy ---

def _freq2erb(f): return 9.265 * np.log1p(f / (24.7 * 9.265))


def _erb2freq(e): return 24.7 * 9.265 * (np.exp(e / 9.265) - 1)


def erb_widths(sr=SR, fft=FFT, bands=NB_ERB, min_freqs=MIN_ERB_FREQS):
    """Number of FFT bins in each ERB band (libDF erb_fb, float32 arithmetic as in Rust)."""
    width = np.float32(sr) / np.float32(fft)
    low, high = np.float32(_freq2erb(np.float32(0))), np.float32(_freq2erb(np.float32(sr // 2)))
    step = (high - low) / np.float32(bands)
    widths, previous, over = [], 0, 0
    for i in range(1, bands + 1):
        top = int(np.round(np.float32(_erb2freq(np.float32(low + i * step))) / width))
        count = top - previous - over
        if count < min_freqs:
            over, count = min_freqs - count, min_freqs
        else:
            over = 0
        widths.append(count)
        previous = top
    widths[-1] += 1
    widths[-1] -= sum(widths) - (fft // 2 + 1)
    return np.array(widths)


def _window():
    n = np.arange(FFT)
    s = np.sin(0.5 * np.pi * (n + 0.5) / (FFT // 2))
    return np.sin(0.5 * np.pi * s * s).astype(np.float32)


def analysis(audio):
    """STFT exactly like libDF: each frame is the previous hop plus the current hop."""
    w = _window()
    frames = len(audio) // HOP
    hops = audio[:frames * HOP].reshape(frames, HOP)
    previous = np.vstack([np.zeros((1, HOP), np.float32), hops[:-1]])
    buf = np.concatenate([previous * w[:HOP], hops * w[HOP:]], axis=1)
    return (np.fft.rfft(buf, axis=1) * (1 / (FFT ** 2 / (2 * HOP)))).astype(np.complex64)


def synthesis(spec):
    """Inverse STFT with overlap-add (libDF's inverse FFT is unnormalised)."""
    w = _window()
    x = (np.fft.irfft(spec, n=FFT, axis=1) * FFT).astype(np.float32) * w
    out = x[:, :HOP].copy()
    out[1:] += x[:-1, HOP:]
    return out.reshape(-1)


def features(spec, widths):
    """ERB band energies (dB, running-mean normalised) and unit-normalised low bins."""
    power = (spec.real ** 2 + spec.imag ** 2).astype(np.float32)
    edges = np.concatenate([[0], np.cumsum(widths)])
    erb = 10 * np.log10(np.stack([power[:, a:b].mean(1) for a, b in zip(edges[:-1], edges[1:])], axis=1) + 1e-10)
    state = np.linspace(-60, -90, NB_ERB, dtype=np.float32)
    for t in range(len(erb)):
        state = erb[t] * (1 - ALPHA) + state * ALPHA
        erb[t] = (erb[t] - state) / 40
    low = spec[:, :NB_DF].copy()
    state = np.linspace(0.001, 0.0001, NB_DF, dtype=np.float32)
    for t in range(len(low)):
        state = np.abs(low[t]) * (1 - ALPHA) + state * ALPHA
        low[t] = low[t] / np.sqrt(state)
    return erb.astype(np.float32), low


def _lookahead(x, frames):
    """DfNet pad_feat: drop the first frames and append as many zero frames (time axis -2)."""
    return np.concatenate([x[..., frames:, :], np.zeros_like(x[..., :frames, :])], axis=-2)


class DialogueCleanup:
    def __init__(self, config, threads=2):
        import onnxruntime as ort
        options = ort.SessionOptions(); options.intra_op_num_threads = threads; options.inter_op_num_threads = 1
        root = model_dir(config)
        load = lambda name: ort.InferenceSession(str(root / name), options, providers=['CPUExecutionProvider'])
        self.enc, self.erb_dec, self.df_dec = load('enc.onnx'), load('erb_dec.onnx'), load('df_dec.onnx')
        self.widths = erb_widths()

    def enhance(self, audio):
        """Clean mono float32 audio at 48 kHz; returns the same number of samples."""
        audio = np.asarray(audio, np.float32)
        length = len(audio)
        spec = analysis(np.concatenate([audio, np.zeros(FFT, np.float32)]))
        erb, low = features(spec, self.widths)
        feat_erb = _lookahead(erb[None, None], CONV_LOOKAHEAD)
        feat_spec = _lookahead(np.stack([low.real, low.imag])[None].astype(np.float32), CONV_LOOKAHEAD)
        e0, e1, e2, e3, emb, c0, _ = self.enc.run(None, {'feat_erb': feat_erb, 'feat_spec': feat_spec})
        (mask,) = self.erb_dec.run(None, {'emb': emb, 'e3': e3, 'e2': e2, 'e1': e1, 'e0': e0})
        coefs = self.df_dec.run(None, {'emb': emb, 'c0': c0})[0][0]
        out = spec * np.repeat(mask[0, 0], self.widths, axis=1)          # ERB mask on every bin
        coefs = coefs.reshape(coefs.shape[0], NB_DF, ORDER, 2)
        coefs = coefs[..., 0] + 1j * coefs[..., 1]
        padded = np.pad(spec[:, :NB_DF], ((ORDER - 1 - DF_LOOKAHEAD, DF_LOOKAHEAD), (0, 0)))
        out[:, :NB_DF] = sum(padded[n:n + len(spec)] * coefs[:, :, n] for n in range(ORDER))  # deep filter
        delay = FFT - HOP
        return synthesis(out.astype(np.complex64))[delay:delay + length]


def clean_file(source, destination, cleaner, progress=lambda done: None):
    """Clean a 48 kHz mono PCM16 WAV in chunks; write the same length as PCM16."""
    with wave.open(str(source), 'rb') as reader:
        if reader.getnchannels() != 1 or reader.getframerate() != SR or reader.getsampwidth() != 2:
            raise ValueError('Dialogue cleanup needs mono 16-bit audio at 48 kHz')
        total = reader.getnframes()
        with wave.open(str(destination), 'wb') as writer:
            writer.setnchannels(1); writer.setframerate(SR); writer.setsampwidth(2)
            tail = np.zeros(0, np.float32)  # earlier audio carried into the next chunk as warm-up
            done = 0
            while done < total:
                block = np.frombuffer(reader.readframes(CHUNK), '<i2').astype(np.float32) / 32768
                cleaned = cleaner.enhance(np.concatenate([tail, block]))[len(tail):]
                writer.writeframes((np.clip(cleaned, -1, 1) * 32767).astype('<i2').tobytes())
                tail = np.concatenate([tail, block])[-WARMUP:]
                done += len(block)
                progress(done / total)
