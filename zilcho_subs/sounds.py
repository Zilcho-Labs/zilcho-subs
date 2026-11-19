"""Optional sound captions such as [Cat meowing] from a local AudioSet tagger.

Uses the Audio Spectrogram Transformer fine-tuned on Google AudioSet (MIT,
BSD-3-Clause), in the ONNX community's int8 conversion. The user downloads it
explicitly from Models; nothing is bundled in the image.
"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

SOUND_REPO = "onnx-community/ast-finetuned-audioset-10-10-0.4593-ONNX"
SOUND_REVISION = "2fe67046c486b8b8bc90a7b469239b2dec822f72"
SOUND_FILES = {"onnx/model_int8.onnx": "6739e480a4faf4651f60d2238b0eb8cd58bead2a07ae1da5e2fbc72e3f7c98a6",
               "config.json": None, "preprocessor_config.json": None}
SOUND_VERSION = "ast-int8-v1"

# AudioSet label -> caption. Animal sounds only for now; extend deliberately.
CAPTIONS = {
    "Meow": "[Cat meowing]", "Caterwaul": "[Cat yowling]", "Purr": "[Cat purring]", "Hiss": "[Cat hissing]",
    "Bark": "[Dog barking]", "Bow-wow": "[Dog barking]", "Yip": "[Dog yipping]", "Howl": "[Dog howling]",
    "Whimper (dog)": "[Dog whimpering]", "Growling": "[Growling]",
    "Bird vocalization, bird call, bird song": "[Birds chirping]", "Chirp, tweet": "[Birds chirping]",
    "Crow": "[Crow cawing]", "Caw": "[Crow cawing]", "Rooster crowing, cock-a-doodle-doo": "[Rooster crowing]",
    "Neigh, whinny": "[Horse neighing]", "Moo": "[Cow mooing]", "Oink": "[Pig oinking]", "Bleat": "[Sheep bleating]",
    "Quack": "[Duck quacking]", "Frog": "[Frog croaking]", "Croak": "[Frog croaking]",
    "Roaring cats (lions, tigers)": "[Roaring]",
}
THRESHOLD = 0.5   # clear sounds only. Measured on one episode: real meows 0.68-0.92; faint stylised ones 0.1-0.37; a frog-like blip in theme music 0.45
WINDOW = 3.0
HOP = 1.5
SAMPLE_RATE = 16000


def sound_model_dir(config): return Path(config) / "models" / "sound-effects"


def sound_model_ready(config):
    root = sound_model_dir(config)
    return (root / "ready.json").is_file() and all((root / name).is_file() for name in SOUND_FILES)


def download_sound_model(config, work):
    """Download the pinned files into a staging folder, verify, then move into place."""
    from huggingface_hub import hf_hub_download
    destination = sound_model_dir(config)
    if destination.exists(): raise ValueError("The sound model folder already exists; remove it before reinstalling")
    staged = Path(work) / "sound-effects"
    for name, digest in SOUND_FILES.items():
        path = Path(hf_hub_download(SOUND_REPO, name, revision=SOUND_REVISION, local_dir=str(staged)))
        if digest and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Sound model download did not match the expected file")
    labels = json.loads((staged / "config.json").read_text(encoding="utf-8")).get("id2label", {})
    if len(labels) != 527: raise ValueError("Unexpected sound model labels")
    (staged / "ready.json").write_text(json.dumps({"repo": SOUND_REPO, "revision": SOUND_REVISION}))
    staged.rename(destination)


# --- Kaldi-compatible log-mel features (what the AST feature extractor uses) ---

def _mel(f):
    import numpy as np
    return 1127.0 * np.log(1.0 + f / 700.0)


@lru_cache(maxsize=1)
def _banks(n_mels=128, n_fft=512, sr=SAMPLE_RATE, low=20.0, high=SAMPLE_RATE / 2):
    import numpy as np
    lo, hi = _mel(low), _mel(high); delta = (hi - lo) / (n_mels + 1)
    left = lo + np.arange(n_mels)[:, None] * delta; center = left + delta; right = center + delta
    mel = _mel(sr / n_fft * np.arange(n_fft // 2))[None, :]
    up = (mel - left) / (center - left); down = (right - mel) / (right - center)
    return np.pad(np.maximum(0.0, np.minimum(up, down)), ((0, 0), (0, 1)))


def fbank(wav):
    import numpy as np
    wav = np.asarray(wav, dtype=np.float64)
    if len(wav) < 400: wav = np.pad(wav, (0, 400 - len(wav)))
    n = 1 + (len(wav) - 400) // 160
    frames = np.lib.stride_tricks.sliding_window_view(wav, 400)[::160][:n].copy()
    frames -= frames.mean(axis=1, keepdims=True)
    frames = frames - 0.97 * np.concatenate([frames[:, :1], frames[:, :-1]], axis=1)
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(400) / 399)
    power = np.abs(np.fft.rfft(frames * window, 512)) ** 2
    return np.log(np.maximum(power @ _banks().T, np.finfo(np.float32).eps))


def features(clip, max_len=1024, mean=-4.2677393, std=4.5689974):
    import numpy as np
    f = fbank(clip)[:max_len]
    if len(f) < max_len: f = np.pad(f, ((0, max_len - len(f)), (0, 0)))
    return ((f - mean) / (std * 2)).astype(np.float32)[None]


class SoundTagger:
    def __init__(self, config, threads=2):
        import onnxruntime as ort
        root = sound_model_dir(config)
        options = ort.SessionOptions(); options.intra_op_num_threads = threads; options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(root / "onnx" / "model_int8.onnx"), options, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        labels = json.loads((root / "config.json").read_text(encoding="utf-8"))["id2label"]
        self.watch = {int(i): CAPTIONS[name] for i, name in labels.items() if name in CAPTIONS}

    def scores(self, clip):
        import numpy as np
        logits = self.session.run(None, {self.input: features(clip)})[0][0]
        return {i: float(1 / (1 + np.exp(-logits[i]))) for i in self.watch}


def detect_sounds(audio, tagger, progress=lambda _: None, threshold=THRESHOLD, window=WINDOW, hop=HOP):
    """Slide over the audio; merge neighbouring windows with the same caption."""
    events = []; total = len(audio) / SAMPLE_RATE; start = 0.0
    while start < total - .5:
        clip = audio[int(start * SAMPLE_RATE):int((start + window) * SAMPLE_RATE)]
        best = {}
        for index, score in tagger.scores(clip).items():
            caption = tagger.watch[index]
            if score >= threshold and score > best.get(caption, 0): best[caption] = score
        for caption, score in best.items():
            last = events[-1] if events else None
            if last and last["text"] == caption and start <= last["end"]:
                last["end"] = min(total, start + window); last["score"] = max(last["score"], score)
            else:
                events.append({"start": start, "end": min(total, start + window), "text": caption, "score": score})
        progress(start); start += hop
    # Windows are coarse: pull each event in from the window edges.
    for e in events:
        if e["end"] - e["start"] > 2: e["start"] += .5; e["end"] -= .5
        e["start"] = round(e["start"], 3); e["end"] = round(e["end"], 3)
    return events


def add_sound_captions(cues, events, min_gap=0.8, gap=0.084):
    """Put sound captions where they fit without hiding or moving dialogue.

    A sound that mostly overlaps a line is written in front of that line, as
    official SDH subtitles do. Otherwise it gets its own cue, trimmed to the
    gap between lines; it's skipped if that gap is too short.
    """
    cues = [dict(c) for c in sorted(cues, key=lambda c: c["start"])]
    extra = []
    for e in events:
        overlaps = [c for c in cues if min(c["end"], e["end"]) - max(c["start"], e["start"]) > 0]
        main = max(overlaps, key=lambda c: min(c["end"], e["end"]) - max(c["start"], e["start"]), default=None)
        if main and min(main["end"], e["end"]) - max(main["start"], e["start"]) >= .5 * (e["end"] - e["start"]):
            if e["text"] not in main["text"]: main["text"] = e["text"] + " " + main["text"]
            continue
        start, end = e["start"], e["end"]
        for c in cues:
            if c["end"] <= e["start"] + 1e-6: start = max(start, c["end"] + gap)
            elif c["start"] >= e["end"] - 1e-6: end = min(end, c["start"] - gap)
            else:
                # Partial overlap: keep the larger free side.
                if e["start"] < c["start"]: end = min(end, c["start"] - gap)
                else: start = max(start, c["end"] + gap)
        if end - start >= min_gap: extra.append({"start": start, "end": end, "text": e["text"]})
    merged = sorted(cues + extra, key=lambda c: c["start"])
    # Captions never overlap each other either.
    result = []
    for c in merged:
        if result and c["start"] < result[-1]["end"]:
            if c["text"].startswith("[") and c["text"].endswith("]") and c not in cues: continue
            result[-1] = dict(result[-1], end=max(result[-1]["start"] + .1, c["start"] - gap))
        result.append(c)
    return result
