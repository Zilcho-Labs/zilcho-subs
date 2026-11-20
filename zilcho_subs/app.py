import hashlib
import json
import os
import secrets
import shutil
import time
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import __version__
from .core import ALIASES, AUDIO, Store, Settings, MODELS, LANGUAGES, VIDEO, media_path, safe_relative, validate_srt, write_sidecar
from .pipeline import model_ready, installed_packs
from .sounds import sound_model_ready
from .denoise import model_ready as cleanup_model_ready
from .runner import Runner

class QueueMove(BaseModel): direction: str = Field(pattern="^(up|down|top|bottom)$")
class JobSelection(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)
    action: str = Field(pattern="^(archive|unarchive|remove)$")
class JobCleanup(BaseModel):
    statuses: list[str] = Field(min_length=1, max_length=4)
    action: str = Field(pattern="^(archive|remove)$")
    archived: bool = False
class Login(BaseModel): password: str = Field(max_length=256)
class QueueItem(BaseModel):
    path: str = Field(max_length=2048)
    audio_stream: int | None = Field(default=None,ge=0,le=1024)
    regenerate: bool = False
class Asset(BaseModel):
    kind: str
    model: str = "medium"
    source: str = ""
    target: str = ""
class TextRequest(BaseModel):
    source: str = Field(max_length=8)
    targets: list[str] | None = None
    text: str | None = Field(default=None,max_length=2_000_000)
    source_path: str | None = Field(default=None,max_length=2048)
    video_path: str | None = Field(default=None,max_length=2048)
    filename: str = Field(default="subtitles.srt",max_length=180)

class SubtitleEdit(BaseModel): text: str = Field(max_length=2_000_000)
class LyricsItem(BaseModel):
    path: str = Field(max_length=2048)
    regenerate: bool = False
class LyricsEdit(BaseModel): text: str = Field(max_length=500_000)


def create_app(config=None,media=None,password=None,run_workers=True):
    root=Path(media or os.environ.get('ZILCHO_MEDIA','/media')).resolve()
    password=password if password is not None else os.environ.get('ZILCHO_PASSWORD','')
    if not 12<=len(password)<=256 or password=='replace-with-your-own-long-password': raise ValueError('Set a unique ZILCHO_PASSWORD of 12–256 characters before starting')
    store=Store(config or os.environ.get('ZILCHO_CONFIG','/config'))
    salt=secrets.token_bytes(32)
    derive=lambda value: hashlib.pbkdf2_hmac('sha256',value.encode(),salt,300000)
    password_hash=derive(password); del password
    sessions={}; failures={}; auth_lock=threading.Lock(); runner=Runner(store,root)
    @asynccontextmanager
    async def lifespan(app):
        if run_workers: runner.start()
        yield
        if run_workers: runner.stop()
    app=FastAPI(title='Zilcho Subs',version=__version__,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store; app.state.runner=runner
    def authenticated(request): return sessions.get(request.cookies.get('zilcho_session',''),0)>time.time()
    @app.middleware('http')
    async def guard(request,call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            length=request.headers.get('content-length')
            if length is None: return JSONResponse({'detail':'Content-Length is required'},411)
            try: count=int(length)
            except ValueError: return JSONResponse({'detail':'Invalid Content-Length'},400)
            if not 0<=count<=2_100_000: return JSONResponse({'detail':'Request too large'},413)
            origin=str(request.base_url).rstrip('/')
            if request.headers.get('origin')!=origin:
                return JSONResponse({'detail':'Request must come from this web interface origin'},403)
        if request.url.path.startswith('/api/') and request.url.path not in ('/api/login','/api/session','/api/health') and not authenticated(request):
            return JSONResponse({'detail':'Sign in first'},401)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers['Cache-Control']='no-store'
        return response
    @app.exception_handler(ValueError)
    async def bad_input(_request,error): return JSONResponse({'detail':str(error)},400)
    @app.exception_handler(KeyError)
    async def missing(_request,_error): return JSONResponse({'detail':'Not found'},404)
    @app.exception_handler(FileNotFoundError)
    async def missing_file(_request,_error): return JSONResponse({'detail':'File not found'},404)
    @app.exception_handler(PermissionError)
    async def permission(_request,_error): return JSONResponse({'detail':'Permission denied; check container UID/GID and mount access'},403)
    @app.get('/api/health')
    def health(): return {'status':'ok','version':__version__}
    @app.get('/api/session')
    def session(request:Request): return {'authenticated':authenticated(request)}
    @app.post('/api/login')
    def login(data:Login,request:Request):
        with auth_lock: return login_locked(data,request)
    def login_locked(data,request):
        ip=request.client.host if request.client else 'unknown'; now=time.time()
        if len(failures)>1000: failures.clear()
        failures[ip]=[x for x in failures.get(ip,[]) if now-x<60]
        if len(failures[ip])>=5: raise HTTPException(429,'Too many attempts; wait a minute')
        if not secrets.compare_digest(derive(data.password),password_hash):
            failures[ip].append(now); raise HTTPException(401,'Incorrect password')
        for token in [k for k,v in list(sessions.items()) if v<=now]: del sessions[token]
        if len(sessions)>=100: sessions.pop(next(iter(sessions)))
        token=secrets.token_urlsafe(32); sessions[token]=now+43200; failures.pop(ip,None)
        response=JSONResponse({'ok':True}); response.set_cookie('zilcho_session',token,max_age=43200,httponly=True,samesite='strict',secure=request.url.scheme=='https')
        return response
    @app.post('/api/logout')
    def logout(request:Request):
        sessions.pop(request.cookies.get('zilcho_session',''),None)
        response=JSONResponse({'ok':True}); response.delete_cookie('zilcho_session'); return response
    @app.get('/api/state')
    def state():
        catalog=store.config/'catalog.json'
        return {'version':__version__,'settings':store.settings().model_dump(),'jobs':store.jobs(),'archived_count':store.archived_count(),
            'models':[{'name':m,'installed':model_ready(store.config,m)} for m in MODELS],
            'sound_model':sound_model_ready(store.config),
            'cleanup_model':cleanup_model_ready(store.config),
            'packs':installed_packs(store.config),'catalog':json.loads(catalog.read_text(encoding='utf-8')) if catalog.exists() else [],
            'languages':LANGUAGES,'media_root':str(root),'media_available':root.is_dir(),'config_root':str(store.config),
            'ffmpeg':bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))}
    @app.post('/api/settings')
    def settings(data:Settings):
        for folder in data.scan_dirs: media_path(root,folder)
        store.save_settings(data); return {'ok':True}
    @app.get('/api/browse')
    def browse(path:str=''):
        folder=media_path(root,path)
        if not folder.is_dir(): raise ValueError('Folder not found')
        entries=[]; truncated=False
        for i,p in enumerate(sorted(folder.iterdir(),key=lambda p:(not p.is_dir(),p.name.casefold()))):
            if i>=1000: truncated=True; break
            if p.is_symlink() or p.name.startswith('.'): continue
            if p.is_dir() or p.suffix.lower() in VIDEO or p.suffix.lower()=='.srt':
                entries.append({'path':p.relative_to(root).as_posix(),'name':p.name,'directory':p.is_dir(),'subtitle':p.suffix.lower()=='.srt','bytes':p.stat().st_size if p.is_file() else 0})
        return {'path':path,'entries':entries,'truncated':truncated}
    @app.post('/api/subtitles')
    def translate_subtitles(data:TextRequest):
        import re
        from .text_subtitles import parse_srt
        if (data.text is None)==(data.source_path is None): raise ValueError('Upload an SRT or choose a library SRT, not both')
        source=ALIASES.get(data.source.lower(),data.source.lower())
        if source=='auto' or not re.fullmatch('[a-z]{2,3}',source): raise ValueError('Choose the original subtitle language code')
        targets=Settings(target_languages=data.targets or store.settings().target_languages).target_languages
        video=data.video_path
        if data.source_path is not None:
            path=media_path(root,data.source_path)
            if path.suffix.lower()!='.srt' or not path.is_file() or path.stat().st_size>2_000_000: raise ValueError('Choose a UTF-8 SRT file, at most 2 MB')
            try: text=path.read_text(encoding='utf-8-sig')
            except UnicodeError: raise ValueError('Convert the subtitle file to UTF-8 first')
            name=path.name
            if not video:
                matches=[p for p in path.parent.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in VIDEO and (path.name==p.stem+'.srt' or path.name.startswith(p.stem+'.'))]
                if matches: video=max(matches,key=lambda p:len(p.stem)).relative_to(root).as_posix()
        else: text=data.text; name=data.filename
        parse_srt(text)
        if video and media_path(root,video,file=True).suffix.lower() not in VIDEO: raise ValueError('Attach subtitles to a supported media file')
        base=Path(name.replace(chr(92),'/').split('/')[-1]).stem
        bad_chars=set('<>:"/|?*')|{chr(92)}
        base=''.join('_' if c in bad_chars or ord(c)<32 else c for c in base).strip(' .')[:100] or 'subtitles'
        last=base.rsplit('.',1)[-1].lower()
        if ALIASES.get(last,last)==source and '.' in base: base=base.rsplit('.',1)[0] or 'subtitles'
        digest=hashlib.sha256(text.encode()).hexdigest()+'.srt'
        saved=store.config/'inputs'/digest
        try:
            with saved.open('x',encoding='utf-8') as out: out.write(text)
        except FileExistsError: pass
        spec={'input':digest,'name':name,'base':base,'source':source,'targets':targets,'video_path':video,'cpu_threads':store.settings().cpu_threads}
        return {'id':store.enqueue('text',spec),'video_path':video}
    @app.post('/api/jobs')
    def enqueue(data:QueueItem):
        media_path(root,data.path,file=True)
        spec={**data.model_dump(),'settings':store.settings().model_dump()}
        return {'id':store.enqueue('subtitle',spec)}
    @app.post('/api/jobs/{id}/cancel')
    def cancel(id:str): store.cancel(id); return {'ok':True}
    @app.post('/api/jobs/{id}/pause')
    def pause_job(id:str): store.pause(id); return {'ok':True}
    @app.post('/api/jobs/{id}/resume')
    def resume_job(id:str): store.resume(id); return {'ok':True}
    @app.post('/api/jobs/{id}/move')
    def move_job(id:str,data:QueueMove): store.move(id,data.direction); return {'ok':True}
    @app.post('/api/jobs/{id}/run-next')
    def run_next(id:str): store.run_next(id); return {'ok':True}
    @app.get('/api/jobs/archived')
    def archived_jobs(): return {'jobs':store.archived_jobs()}
    @app.post('/api/jobs/cleanup')
    def cleanup_jobs(data:JobCleanup): return {'count':store.clear(data.statuses,data.action,data.archived)}
    @app.post('/api/jobs/selection')
    def selected_jobs(data:JobSelection): return {'count':store.apply(data.ids,data.action)}
    @app.post('/api/jobs/{id}/archive')
    def archive_job(id:str): store.archive(id,True); return {'ok':True}
    @app.post('/api/jobs/{id}/unarchive')
    def unarchive_job(id:str): store.archive(id,False); return {'ok':True}
    @app.post('/api/jobs/{id}/remove')
    def remove_job(id:str): store.remove(id); return {'ok':True}
    @app.post('/api/jobs/{id}/retry')
    def retry(id:str):
        job=store.job(id)
        if job['status'] not in ('failed','cancelled','skipped','completed'): raise ValueError('Wait for the current job to finish')
        spec=job['spec']
        if job['kind']=='subtitle':
            media_path(root,spec['path'],file=True); spec={**spec,'settings':store.settings().model_dump()}
        elif job['kind']=='lyrics':
            media_path(root,spec['path'],file=True,kinds=AUDIO); spec={**spec,'settings':store.settings().model_dump()}
        return {'id':store.enqueue(job['kind'],spec)}
    # ----- Music lyrics -----
    @app.get('/api/music/browse')
    def music_browse(path:str=''):
        folder=media_path(root,path)
        if not folder.is_dir(): raise ValueError('Folder not found')
        entries=[]; truncated=False
        for i,p in enumerate(sorted(folder.iterdir(),key=lambda p:(not p.is_dir(),p.name.casefold()))):
            if i>=1000: truncated=True; break
            if p.is_symlink() or p.name.startswith('.'): continue
            if p.is_dir() or p.suffix.lower() in AUDIO:
                entries.append({'path':p.relative_to(root).as_posix(),'name':p.name,'directory':p.is_dir(),
                    'lyrics':p.is_file() and p.with_name(p.stem+'.lrc').is_file()})
        return {'path':path,'entries':entries,'truncated':truncated}
    @app.post('/api/lyrics')
    def make_lyrics(data:LyricsItem):
        media_path(root,data.path,file=True,kinds=AUDIO)
        return {'id':store.enqueue('lyrics',{'path':data.path,'regenerate':data.regenerate,'settings':store.settings().model_dump()})}
    def lyrics_job(id):
        job=store.job(id)
        if job['kind']!='lyrics': raise KeyError(id)
        return job
    def lyric_file(id,name):
        job=lyrics_job(id)
        if name not in ('lrc','txt','words') or name not in job['outputs']: raise KeyError(name)
        path=media_path(root,job['outputs'][name])
        if path.suffix.lower() not in ('.lrc','.txt') or not path.is_file(): raise ValueError('Lyrics file missing')
        return job,path
    @app.get('/api/lyrics/{id}/file/{name}')
    def lyrics_file(id:str,name:str,download:bool=False):
        _job,path=lyric_file(id,name)
        if download: return FileResponse(path,media_type='text/plain; charset=utf-8',filename=path.name)
        if path.stat().st_size>500_000: raise ValueError('Lyrics file too large to preview')
        return PlainTextResponse(path.read_text(encoding='utf-8'))
    @app.post('/api/lyrics/{id}')
    def lyrics_edit(id:str,data:LyricsEdit):
        from .lyrics import lines_from_lrc, lyrics_data_path, parse_lrc, publish, render_lrc, render_text
        job=lyrics_job(id)
        if job['status']!='completed': raise ValueError('Wait for the lyrics to finish first')
        parsed=parse_lrc(data.text)
        song=media_path(root,job['spec']['path'],file=True,kinds=AUDIO)
        path=lyrics_data_path(store,id)
        saved=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        lines=lines_from_lrc(parsed,saved.get('lines',[]),saved.get('duration'))
        if not lines: raise ValueError('No lyric lines found')
        meta={k:saved.get(k,'') for k in ('title','artist','album')}
        outputs=dict(job['outputs'])
        words=store.settings().lyrics_lrc_words
        outputs['lrc']=publish(store,root,song,'.lrc',render_lrc(lines,meta,saved.get('duration'),words=words)).relative_to(root).as_posix()
        outputs['txt']=publish(store,root,song,'.txt',render_text(lines)).relative_to(root).as_posix()
        temp=path.with_suffix('.tmp'); temp.write_text(json.dumps({**saved,'lines':lines},ensure_ascii=False),encoding='utf-8'); temp.replace(path)
        store.update(id,outputs=outputs)
        return {'ok':True,'lrc':outputs['lrc']}
    @app.post('/api/assets')
    def assets(data:Asset):
        import re
        if data.kind not in ('model','remove_model','sound_model','remove_sound_model','cleanup_model','remove_cleanup_model','catalog','pack','remove_pack'): raise ValueError('Unknown asset action')
        if data.model not in MODELS: raise ValueError('Invalid model')
        if data.kind in ('pack','remove_pack'):
            if any(not re.fullmatch(r'[a-z]{2,3}(?:_[A-Z]{2})?',x) for x in (data.source,data.target)): raise ValueError('Choose a valid language pair')
        return {'id':store.enqueue(data.kind,data.model_dump())}
    def subtitle_path(id,language):
        job=store.job(id)
        if language not in job['outputs']: raise KeyError(language)
        relative=job['outputs'][language]
        if relative.startswith('config:exports/'):
            path=media_path(store.config/'exports',relative[len('config:exports/'):])
        else: path=media_path(root,relative)
        if path.suffix!='.srt' or not path.is_file(): raise ValueError('Subtitle file missing')
        return job,path
    @app.get('/api/jobs/{id}/subtitles/{language}')
    def preview(id:str,language:str):
        _job,path=subtitle_path(id,language)
        if path.stat().st_size>2_000_000: raise ValueError('Subtitle too large to preview')
        return PlainTextResponse(path.read_text(encoding='utf-8'))
    @app.get('/api/jobs/{id}/download/{language}')
    def download(id:str,language:str):
        _job,path=subtitle_path(id,language)
        return FileResponse(path,media_type='application/x-subrip',filename=path.name)
    @app.post('/api/jobs/{id}/subtitles/{language}')
    def edit(id:str,language:str,data:SubtitleEdit):
        job,_path=subtitle_path(id,language)
        if job['status'] in ('queued','running','cancelling','pausing','paused'): raise ValueError('Wait for the job to finish')
        from .text_subtitles import parse_srt
        parse_srt(data.text)
        if '<' in data.text: raise ValueError('Use plain subtitle text, not markup')
        text=data.text.lstrip(chr(65279)).replace(chr(13)+chr(10),chr(10)).strip()+chr(10)
        exported=job['outputs'][language].startswith('config:exports/')
        if job['kind']=='text':
            video=media_path(root,job['spec']['video_path'],file=True) if job['spec'].get('video_path') else store.config/'exports'/id/(job['spec']['base']+'.media')
        else: video=media_path(root,job['spec']['path'],file=True)
        output=write_sidecar(video,language,text,version=True)
        relative='config:'+output.relative_to(store.config).as_posix() if exported else output.relative_to(root).as_posix()
        store.update(id,outputs={**job['outputs'],language:relative})
        return {'ok':True,'path':relative}
    static=Path(__file__).parent/'static'
    @app.get('/')
    def index(): return FileResponse(static/'index.html')
    app.mount('/static',StaticFiles(directory=static),name='static')
    return app
