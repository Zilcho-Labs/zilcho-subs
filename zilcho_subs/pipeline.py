import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import stat
from pathlib import PurePosixPath
import time
from pathlib import Path
from .core import ALIASES, MODELS, Settings, JobPaused, media_path, render_srt, write_sidecar


def local_environment(config, online=False, threads=2):
    values={"XDG_DATA_HOME":str(config/"data"),"XDG_CONFIG_HOME":str(config/"settings"),"XDG_CACHE_HOME":str(config/"cache"),
      "HF_HOME":str(config/"cache"/"huggingface"),"ARGOS_PACKAGES_DIR":str(config/"packages"),
      "ARGOS_DEVICE_TYPE":"cpu","ARGOS_MODEL_PROVIDER":"OPENNMT","ARGOS_CHUNK_TYPE":"NONE",
      "ARGOS_INTER_THREADS":"1","ARGOS_INTRA_THREADS":str(threads),"OMP_NUM_THREADS":str(threads),
      "HF_HUB_DISABLE_TELEMETRY":"1","DO_NOT_TRACK":"1","HF_HUB_OFFLINE":"0" if online else "1"}
    os.environ.update(values)


def probe(path):
    try:
        result=subprocess.run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],capture_output=True,timeout=60,check=True)
    except FileNotFoundError as e: raise RuntimeError("FFmpeg/ffprobe are not installed") from e
    except subprocess.SubprocessError as e: raise RuntimeError("Could not read this media file with ffprobe") from e
    data=json.loads(result.stdout)
    duration=float(data.get("format",{}).get("duration") or 0)
    if not 0<duration<=86400: raise ValueError("Media duration must be between zero and 24 hours")
    data["duration"]=duration
    return data


def select_audio(info, source="auto", stream=None):
    streams=[s for s in info.get("streams",[]) if s.get("codec_type")=="audio"]
    if not streams: raise ValueError("No audio tracks in this file")
    def code(s):
        tag=s.get("tags",{}).get("language","").lower(); return ALIASES.get(tag,tag)
    if stream is not None:
        matches=[s for s in streams if s["index"]==stream]
        if not matches: raise ValueError("Requested audio track does not exist")
        return matches[0]
    if source!="auto":
        matches=[s for s in streams if code(s)==source]
        if matches: return matches[0]
        # With only one untagged track, the user can explicitly supply its language.
        if len(streams)==1 and code(streams[0]) in ("","und"): return streams[0]
        raise ValueError("No audio track matches the chosen language; select its stream index explicitly")
    for language in ("ja","ko","zh"):
        matches=[s for s in streams if code(s)==language]
        if matches: return matches[0]
    return next((s for s in streams if s.get("disposition",{}).get("default")),streams[0])


def existing_languages(media, info):
    result=set()
    for s in info.get("streams",[]):
        if s.get("codec_type")=="subtitle" and not s.get("disposition",{}).get("forced"):
            code=s.get("tags",{}).get("language","").lower(); result.add(ALIASES.get(code,code))
    for path in media.parent.iterdir():
        if path.is_symlink() or not path.is_file() or path.stat().st_size==0 or not path.name.startswith(media.stem+"."): continue
        if path.suffix.lower() not in (".srt",".ass",".ssa",".vtt",".sub"): continue
        tags=path.name[len(media.stem)+1:].split(".")[:-1]
        if "forced" in [x.lower() for x in tags] or not tags: continue
        language=tags[0].split("-")[0]; result.add(ALIASES.get(language.lower(),language))
    return result


def extract(media, stream, destination, duration, rate=16000):
    try:
        subprocess.run(["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-i",str(media),"-map",f"0:{stream}","-vn","-af","asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0","-ac","1","-ar",str(rate),"-c:a","pcm_s16le",str(destination)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=min(7200,max(300,duration*2)),check=True)
    except FileNotFoundError as e: raise RuntimeError("FFmpeg is not installed") from e
    except subprocess.SubprocessError as e: raise RuntimeError("Audio extraction failed or timed out") from e


def resample(source, destination, rate):
    try:
        subprocess.run(["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-i",str(source),"-ac","1","-ar",str(rate),"-c:a","pcm_s16le",str(destination)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=3600,check=True)
    except FileNotFoundError as e: raise RuntimeError("FFmpeg is not installed") from e
    except subprocess.SubprocessError as e: raise RuntimeError("Audio resampling failed or timed out") from e


def model_ready(config, name):
    root=config/"models"/name
    return name in MODELS and (root/"ready.json").is_file() and all((root/x).is_file() for x in ("model.bin","config.json","tokenizer.json")) and bool(list(root.glob("vocabulary.*")))


def translation_route(packages, source, target):
    from collections import deque
    pending=deque([(source, [])]); visited={source}
    while pending:
        language,route=pending.popleft()
        if language==target: return route
        for package in packages:
            if getattr(package,"type","translate")=="translate" and package.from_code==language and package.to_code not in visited:
                visited.add(package.to_code); pending.append((package.to_code,route+[package]))
    raise ValueError(f"No installed translation route from {source} to {target}; install the needed packs in Languages")


class WholeCue:
    # Cues are already short speech segments. No extra sentence-model download is needed.
    def split_sentences(self, text): return [text]


class LocalEngine:
    def __init__(self, config): self.config=config; self.translators={}; self.checkpoint=None
    def speech(self,audio,settings,task,progress):
        if not model_ready(self.config, settings.model): raise ValueError("Download the selected speech model from Models before processing")
        from faster_whisper import WhisperModel
        model=WhisperModel(str(self.config/"models"/settings.model),device="cpu",compute_type="int8",cpu_threads=settings.cpu_threads,num_workers=1,local_files_only=True)
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import get_speech_timestamps
        from .timing import recognize_utterances
        return recognize_utterances(model,decode_audio(str(audio),sampling_rate=16000),settings,task,progress,get_speech_timestamps,checkpoint=self.checkpoint)
    def lyrics(self,audio,settings,language,progress):
        if not model_ready(self.config, settings.model): raise ValueError("Download the selected speech model from Speech models before making lyrics")
        from faster_whisper import WhisperModel
        from faster_whisper.audio import decode_audio
        from .lyrics import recognize_lyrics
        model=WhisperModel(str(self.config/"models"/settings.model),device="cpu",compute_type="int8",cpu_threads=settings.cpu_threads,num_workers=1,local_files_only=True)
        return recognize_lyrics(model,decode_audio(str(audio),sampling_rate=16000),language,progress)
    def clean(self,audio48,destination,settings,progress):
        """Dialogue cleanup (DeepFilterNet3) on 48 kHz audio, written as 16 kHz for speech recognition."""
        from .denoise import DialogueCleanup, clean_file
        cleaned=Path(destination).with_name("cleaned-48k.wav")
        clean_file(audio48,cleaned,DialogueCleanup(self.config,threads=settings.cpu_threads),progress)
        resample(cleaned,destination,16000)
    def sounds(self,audio,settings,progress):
        from faster_whisper.audio import decode_audio
        from .sounds import SoundTagger, detect_sounds
        tagger=SoundTagger(self.config,threads=settings.cpu_threads)
        return detect_sounds(decode_audio(str(audio),sampling_rate=16000),tagger,progress)
    def translate(self, text, source, target):
        if source==target: return text
        from . import local_packs
        from ctranslate2 import Translator
        pair=(source,target)
        if pair not in self.translators:
            # Use the Argos-compatible whole-cue algorithm without optional sentence segmenters.
            route=translation_route(local_packs.installed(self.config),source,target)
            self.translators[pair]=[(p,Translator(str(p.package_path/"model"),device="cpu",compute_type="int8",
                inter_threads=1,intra_threads=int(os.environ.get("ARGOS_INTRA_THREADS","2")))) for p in route]
        for pkg,translator in self.translators[pair]:
            text=local_packs.translate_cue(pkg,text,translator)
        return text



def process_subtitle(store, job, media_root, engine=None, probe_fn=probe, extract_fn=extract):
    spec=job["spec"]
    # A job keeps the settings it was queued with, except CPU threads: that only
    # changes speed, so the current value applies whenever a job starts or resumes.
    settings=Settings.model_validate({**spec["settings"],"cpu_threads":store.settings().cpu_threads})
    local_environment(store.config, threads=settings.cpu_threads)
    media=media_path(media_root,spec["path"],file=True); fingerprint=(media.stat().st_size,media.stat().st_mtime_ns)
    def progress(stage,value):
        latest=store.job(job["id"])
        if latest["cancel"]: raise InterruptedError("Cancelled")
        if latest["pause_requested"]: raise JobPaused()
        store.update(job["id"],stage=stage,progress=value)
    progress("Inspecting audio and existing subtitles",2)
    info=probe_fn(media)
    targets=settings.target_languages
    if settings.skip_existing and not spec.get("regenerate"):
        present=existing_languages(media,info); targets=[t for t in targets if t not in present]
    if not targets:
        store.update(job["id"],status="completed" if job["outputs"] else "skipped",stage="Subtitles ready" if job["outputs"] else "Requested subtitles already exist",progress=100); return
    engine=engine or LocalEngine(store.config)
    if settings.prefer_existing and not spec.get("regenerate"):
        from .text_subtitles import try_existing
        existing=try_existing(media,targets,engine,progress)
        if existing:
            texts,name=existing; outputs=dict(job["outputs"])
            for language,text in texts.items():
                progress(f"Saving {language} subtitles from {name}",96)
                media=media_path(media_root,spec["path"],file=True)
                if (media.stat().st_size,media.stat().st_mtime_ns)!=fingerprint: raise ValueError("Video changed during subtitle translation")
                try: output=write_sidecar(media,language,text,version=bool(spec.get("regenerate")))
                except FileExistsError: continue
                outputs[language]=output.relative_to(Path(media_root).resolve()).as_posix()
                store.update(job["id"],outputs=outputs)
            store.update(job["id"],status="completed" if outputs else "skipped",stage="Translated existing SRT; no speech model used",progress=100)
            return
    stream=select_audio(info,settings.source_language,spec.get("audio_stream"))
    if isinstance(engine,LocalEngine) and not model_ready(store.config,settings.model): raise ValueError("Download the selected speech model from Models first")
    task="translate" if settings.direct_english and targets==["en"] else "transcribe"
    marker=store.config/"models"/settings.model/"ready.json"
    from .timing import TIMING_VERSION
    clean=settings.clean_dialogue
    if clean and isinstance(engine,LocalEngine):
        from .denoise import model_ready as cleanup_ready
        if not cleanup_ready(store.config): raise ValueError("Download Dialogue cleanup in Speech models, or turn it off in Settings")
    parts=[TIMING_VERSION,str(media),fingerprint,stream["index"],settings.model,marker.read_text() if marker.exists() else "",task,settings.source_language]
    if clean:
        # Only cleaned transcripts get a new key, so existing cached transcripts stay valid.
        from .denoise import CLEANUP_VERSION
        parts.append(CLEANUP_VERSION)
    key=hashlib.sha256(json.dumps(parts).encode()).hexdigest()
    cache=store.config/"transcripts"/(key+".json")
    if isinstance(engine,LocalEngine):
        from .timing import SpeechCheckpoint
        engine.checkpoint=SpeechCheckpoint(store.config/"transcripts"/"checkpoints"/(job["id"]+".json"),key)
    want_sounds=settings.sound_captions and "en" in targets
    from .sounds import SOUND_VERSION
    # Sounds depend only on the audio, so a speech-timing change never repeats the slow scan.
    sound_key=hashlib.sha256(json.dumps([SOUND_VERSION,str(media),fingerprint,stream["index"]]).encode()).hexdigest()
    sound_cache=store.config/"transcripts"/(sound_key+".sounds.json")
    if want_sounds and isinstance(engine,LocalEngine):
        from .sounds import sound_model_ready
        if not sound_model_ready(store.config): raise ValueError("Download the Sound effects model in Models, or turn off sound captions in Settings")
    events=json.loads(sound_cache.read_text(encoding="utf-8")) if want_sounds and sound_cache.exists() else None
    speech_end=55 if want_sounds else 65
    with tempfile.TemporaryDirectory(prefix=job["id"]+"-",dir=store.config/"work") as work:
        audio=Path(work)/"audio.wav"
        def extracted():
            if not audio.exists(): progress("Extracting audio locally",5); extract_fn(media,stream["index"],audio,info["duration"])
            return audio
        if cache.exists():
            result=json.loads(cache.read_text(encoding="utf-8")); progress("Reusing cached transcript",speech_end)
        else:
            speech_audio=extracted()
            if clean:
                # Sound captions still listen to the original audio; cleanup removes background sounds.
                audio48=Path(work)/"audio-48k.wav"; extract_fn(media,stream["index"],audio48,info["duration"],rate=48000)
                speech_audio=Path(work)/"speech.wav"
                engine.clean(audio48,speech_audio,settings,lambda done:progress("Cleaning up background noise",6+4*done))
            progress("Recognizing and translating speech" if task=="translate" else "Recognizing original speech",10)
            result=engine.speech(speech_audio,settings,task,lambda end:progress("Recognizing speech",10+(speech_end-10)*min(1,end/info["duration"])))
            # Cache only a completed speech pass, never partial results from a cancelled worker.
            temp=cache.with_suffix(".tmp"); temp.write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8"); temp.replace(cache)
        if want_sounds and events is None:
            progress("Listening for sound effects",speech_end)
            events=engine.sounds(extracted(),settings,lambda at:progress("Listening for sound effects",speech_end+10*min(1,at/info["duration"])))
            temp=sound_cache.with_suffix(".tmp"); temp.write_text(json.dumps(events),encoding="utf-8"); temp.replace(sound_cache)
    offset=float(stream.get("start_time") or 0)-float(info.get("format",{}).get("start_time") or 0)
    outputs=dict(job["outputs"])
    for index,target in enumerate(targets):
        segments=[]
        for i,segment in enumerate(result["segments"]):
            progress(f"Preparing {target} subtitles",65+28*(index+(i+1)/max(1,len(result["segments"])))/len(targets))
            same=task=="translate" or target==result["language"]
            text=segment["text"] if same else engine.translate(segment["text"],result["language"],target)
            start=max(0,segment["start"]+offset); end=segment["end"]+offset
            if end>start:
                cue={"start":start,"end":end,"text":text}
                # Word times only describe the recognized text, not a translation of it.
                if same and segment.get("words"): cue["words"]=[[w[0]+offset,w[1]+offset,w[2]] for w in segment["words"]]
                segments.append(cue)
        # Reading time and made-up-line removal happen here, after translation, so
        # they use the shown text and also apply to cached transcripts.
        from .timing import readable_cues, split_sentences
        segments=split_sentences(segments)
        if want_sounds and target=="en" and events:
            from .sounds import add_sound_captions
            shifted=[dict(e,start=max(0,e["start"]+offset),end=e["end"]+offset) for e in events]
            segments=add_sound_captions(segments,shifted)
        segments=readable_cues(segments,info["duration"])
        text=render_srt(segments); progress(f"Saving {target} subtitles",96)
        # Refuse to publish subtitles if the media changed while it was being processed.
        media=media_path(media_root,spec["path"],file=True)
        if (media.stat().st_size,media.stat().st_mtime_ns)!=fingerprint: raise ValueError("Media file changed during processing; retry after import finishes")
        try: output=write_sidecar(media,target,text,version=bool(spec.get("regenerate")))
        except FileExistsError: continue  # Another tool won the race; never overwrite it.
        outputs[target]=output.relative_to(Path(media_root).resolve()).as_posix()
        store.update(job["id"],outputs=outputs)
        # Quality page numbers; translated languages share the speech confidence they came from.
        from .quality import srt_counts
        store.record_quality(job["id"],target,outputs[target],*srt_counts(text),result.get("confidence"))
    store.update(job["id"],status="completed" if outputs else "skipped",stage="Subtitles ready" if outputs else "Existing subtitles preserved",progress=100)


def reachable_targets(packs, source):
    """Language codes reachable from source over installed packs (same walk as translation_route)."""
    from collections import deque
    pending=deque([source]); seen={source}
    while pending:
        language=pending.popleft()
        for p in packs:
            if p["source"]==language and p["target"] not in seen: seen.add(p["target"]); pending.append(p["target"])
    seen.discard(source)
    return sorted(seen)


def targets_by_source(packs, names):
    pack_names={p["target"]:p.get("to_name") or p["target"] for p in packs}
    result={}
    for source in sorted({p["source"] for p in packs}):
        targets=reachable_targets(packs,source)
        if targets: result[source]=[{"code":c,"name":names.get(c) or pack_names.get(c) or c} for c in targets]
    return result


def installed_packs(config):
    result=[]
    for metadata in (config/"packages").glob("*/metadata.json"):
        if metadata.is_symlink(): continue
        try:
            p=json.loads(metadata.read_text(encoding="utf-8"))
            result.append({"source":p["from_code"],"target":p["to_code"],"to_name":p.get("to_name",p["to_code"]),"name":p.get("from_name",p["from_code"])+" → "+p.get("to_name",p["to_code"])})
        except (ValueError,KeyError,OSError): continue
    return result


def install_archive(archive, config, source, target, job_id):
    with tempfile.TemporaryDirectory(prefix=job_id+"-",dir=config/"work") as work:
        stage=Path(work)/"package"; stage.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            entries=bundle.infolist()
            if len(entries)>100000 or sum(e.file_size for e in entries)>4_000_000_000: raise ValueError("Language package is too large")
            for entry in entries:
                path=PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or chr(92) in entry.filename or ":" in entry.filename or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("Unsafe language package path")
            bundle.extractall(stage)
        roots=[p for p in stage.iterdir() if p.is_dir() and (p/"metadata.json").is_file()]
        if len(roots)!=1: raise ValueError("Unexpected language package layout")
        package=roots[0]; metadata=json.loads((package/"metadata.json").read_text(encoding="utf-8"))
        if metadata.get("from_code")!=source or metadata.get("to_code")!=target or not (package/"model"/"model.bin").is_file():
            raise ValueError("Language package does not match the requested pair")
        destination=config/"packages"/package.name
        if destination.exists() or destination.is_symlink(): raise ValueError("This package is already installed; remove it before reinstalling")
        package.rename(destination)


def process_asset(store, job):
    kind,spec=job["kind"],job["spec"]
    local_environment(store.config,online=True)
    store.update(job["id"],stage="Downloading requested files" if kind in ("model","pack") else "Updating local models")
    if kind=="model":
        name=spec["model"]
        if name not in MODELS: raise ValueError("Invalid model")
        if not model_ready(store.config,name):
            from faster_whisper.utils import download_model
            destination=store.config/"models"/name
            with tempfile.TemporaryDirectory(prefix=job["id"]+"-",dir=store.config/"work") as work:
                staged=Path(work)/"model"; download_model(name,output_dir=str(staged))
                if not all((staged/x).exists() for x in ("model.bin","config.json","tokenizer.json")) or not list(staged.glob("vocabulary.*")): raise ValueError("Incomplete speech model download")
                (staged/"ready.json").write_text(json.dumps({"model":name,"installed":time.time()}))
                if destination.exists(): raise ValueError("An incomplete model directory exists; remove it before reinstalling")
                staged.rename(destination)
    elif kind=="sound_model":
        from .sounds import download_sound_model, sound_model_ready
        if not sound_model_ready(store.config):
            with tempfile.TemporaryDirectory(prefix=job["id"]+"-",dir=store.config/"work") as work:
                download_sound_model(store.config,work)
    elif kind=="cleanup_model":
        from .denoise import download_model as download_cleanup, model_ready as cleanup_ready
        if not cleanup_ready(store.config):
            with tempfile.TemporaryDirectory(prefix=job["id"]+"-",dir=store.config/"work") as work:
                download_cleanup(store.config,work)
    elif kind=="remove_cleanup_model":
        from .denoise import model_dir as cleanup_dir
        path=cleanup_dir(store.config)
        if path.is_symlink(): raise ValueError("Unexpected symlink")
        if path.exists(): shutil.rmtree(path)
    elif kind=="remove_sound_model":
        from .sounds import sound_model_dir
        path=sound_model_dir(store.config)
        if path.is_symlink(): raise ValueError("Unexpected symlink")
        if path.exists(): shutil.rmtree(path)
    elif kind=="remove_model":
        if spec["model"] not in MODELS: raise ValueError("Invalid model")
        path=store.config/"models"/spec["model"]
        if path.is_symlink(): raise ValueError("Unexpected symlink")
        if path.exists(): shutil.rmtree(path)
    elif kind in ("catalog","pack","remove_pack"):
        from . import local_packs
        if kind=="catalog":
            entries=local_packs.catalog(store.config,refresh=True)
            available=[{"source":p["from_code"],"target":p["to_code"],"name":p.get("from_name",p["from_code"])+" → "+p.get("to_name",p["to_code"])} for p in entries]
            (store.config/"catalog.json").write_text(json.dumps(available,ensure_ascii=False),encoding="utf-8")
        elif kind=="pack":
            available=local_packs.catalog(store.config)
            match=next((p for p in available if p["from_code"]==spec["source"] and p["to_code"]==spec["target"]),None)
            if not match: raise ValueError("Refresh the language catalog and choose an available pair")
            with tempfile.TemporaryDirectory(prefix=job["id"]+"-",dir=store.config/"work") as work:
                archive=Path(work)/"pack.argosmodel"
                failures=[]
                for url in match["links"]:
                    try: local_packs.download(url,archive); break
                    except (OSError,ValueError) as error: failures.append(str(error))
                else: raise ValueError("Language download failed: "+"; ".join(failures)[:500])
                install_archive(archive,store.config,spec["source"],spec["target"],job["id"])
        else:
            for p in local_packs.installed(store.config):
                if p.from_code==spec["source"] and p.to_code==spec["target"]: shutil.rmtree(p.package_path)
    else: raise ValueError("Unknown asset job")
    store.update(job["id"],status="completed",stage="Ready",progress=100)
