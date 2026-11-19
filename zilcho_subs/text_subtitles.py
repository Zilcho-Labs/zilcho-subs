"""Text subtitle translation: no audio extraction or speech recognition."""
import json
import re
import textwrap
from pathlib import Path
from .core import ALIASES, media_path, write_sidecar

CLOCK=r"\d{2,}:\d{2}:\d{2},\d{3}"

def parse_srt(text):
    text=text.lstrip('\ufeff').replace('\r\n','\n').strip()
    if not text or len(text.encode())>2_000_000 or '\ufffd' in text or '\x00' in text: raise ValueError('Use a nonempty UTF-8 SRT file, at most 2 MB')
    cues=[]; previous=-1
    for block in re.split(r'\n\s*\n',text):
        lines=block.splitlines()
        if len(lines)<3 or not lines[0].strip().isdigit(): raise ValueError('Invalid SRT cue')
        match=re.fullmatch(f'({CLOCK}) --> ({CLOCK})',lines[1].strip())
        if not match: raise ValueError('Invalid SRT timestamps')
        times=[]
        for clock in match.groups():
            h,m,s,ms=map(int,re.split('[:,]',clock))
            if m>=60 or s>=60: raise ValueError('Invalid SRT timestamp range')
            times.append(((h*60+m)*60+s)*1000+ms)
        if times[1]<=times[0] or times[0]<previous: raise ValueError('SRT cues must be ordered with positive durations')
        previous=times[0]
        visible=' '.join(re.sub(r'<[^>]*>','',' '.join(lines[2:])).split())
        if not visible: raise ValueError('SRT cue has no visible text')
        cues.append({'timing':lines[1].strip(),'text':visible})
    if len(cues)>20000: raise ValueError('Too many SRT cues')
    return cues


def translate_document(text, source, targets, engine, progress=lambda *_:None):
    cues=parse_srt(text); outputs={}
    for n,target in enumerate(targets):
        blocks=[]
        for i,cue in enumerate(cues):
            progress(f'Translating existing subtitles to {target}',5+85*(n+(i+1)/len(cues))/len(targets))
            translated=cue['text'] if target==source else engine.translate(cue['text'],source,target)
            translated=' '.join(re.sub(r'<[^>]*>','',translated).split())
            if not translated: raise ValueError('Local translation returned empty subtitle text')
            # Preserve every original cue/timestamp; do not proportionally retime text translations.
            blocks.append(str(i+1)+'\n'+cue['timing']+'\n'+'\n'.join(textwrap.wrap(translated,42)))
        outputs[target]='\n\n'.join(blocks)+'\n'
    return outputs


def existing_candidates(video):
    common={'english':'en','japanese':'ja','korean':'ko','chinese':'zh','spanish':'es','french':'fr','german':'de'}
    for path in sorted(video.parent.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix.lower()!='.srt' or not path.name.startswith(video.stem+'.'): continue
        tags=path.name[len(video.stem)+1:].split('.')[:-1]
        if not tags or 'forced' in [x.lower() for x in tags] or not 0<path.stat().st_size<=2_000_000: continue
        language=common.get(tags[0].lower(),ALIASES.get(tags[0].lower(),tags[0].lower()))
        if re.fullmatch('[a-z]{2,3}',language): yield path,language


def try_existing(video,targets,engine,progress):
    for path,source in existing_candidates(video):
        try: text=path.read_text(encoding='utf-8-sig'); parse_srt(text)
        except (UnicodeError,ValueError): continue
        try: return translate_document(text,source,targets,engine,progress),path.name
        except ValueError as error:
            if str(error).startswith('No installed translation route'): continue
            raise
    return None


def process_text_job(store,job,media_root,engine=None):
    from .pipeline import LocalEngine, local_environment
    spec=job['spec']; local_environment(store.config,threads=store.settings().cpu_threads); engine=engine or LocalEngine(store.config)
    input_path=store.config/'inputs'/spec['input']
    if not re.fullmatch(r'[a-f0-9]{64}\.srt',spec['input']) or input_path.is_symlink(): raise ValueError('Invalid saved subtitle input')
    text=input_path.read_text(encoding='utf-8')
    def progress(stage,value):
        if store.job(job['id'])['cancel']: raise InterruptedError('Cancelled')
        store.update(job['id'],stage=stage,progress=value)
    if spec.get('video_path'):
        anchor=media_path(media_root,spec['video_path'],file=True); fingerprint=(anchor.stat().st_size,anchor.stat().st_mtime_ns)
    else:
        folder=store.config/'exports'/job['id']; folder.mkdir(parents=True,exist_ok=True)
        anchor=folder/(spec['base']+'.media'); fingerprint=None
    translated=translate_document(text,spec['source'],spec['targets'],engine,progress); outputs={}
    for language,content in translated.items():
        progress(f'Saving {language} subtitles',96)
        if fingerprint:
            anchor=media_path(media_root,spec['video_path'],file=True)
            if (anchor.stat().st_size,anchor.stat().st_mtime_ns)!=fingerprint: raise ValueError('Video changed during subtitle translation')
        output=write_sidecar(anchor,language,content,version=True)
        outputs[language]=output.relative_to(Path(media_root).resolve()).as_posix() if fingerprint else 'config:'+output.relative_to(store.config).as_posix()
        store.update(job['id'],outputs=outputs)
    store.update(job['id'],status='completed',stage='Translated existing subtitles; no speech model used',progress=100)
