import hashlib
import json
import os
import re
import secrets
import shutil
import time
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import __version__
from .core import ALIASES, AUDIO, Store, Settings, Library, MODELS, LANGUAGES, VIDEO, in_folders, leads_to_folders, library_folders, media_path, safe_relative, settings_with_libraries, validate_srt, write_sidecar
from .pipeline import model_ready, installed_packs, reachable_targets, targets_by_source
from .sounds import sound_model_ready
from .denoise import model_ready as cleanup_model_ready
from .runner import Runner
from . import monitor as library
from . import embedded, external, jellyfin, mediaserver, library_search
from . import status as file_status

class QueueMove(BaseModel): direction: str = Field(pattern="^(up|down|top|bottom)$")
class JobSelection(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)
    action: str = Field(pattern="^(archive|unarchive|remove|cancel)$")
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
class AuditRequest(BaseModel):
    path: str | None = Field(default=None,max_length=2048)
    generate: bool = False
    kinds: list[str] = Field(default_factory=lambda: ['video'],max_length=2)
class QueueBatch(BaseModel):
    paths: list[str] = Field(min_length=1,max_length=5000)
    audio_stream: int | None = Field(default=None,ge=0,le=1024)
    regenerate: bool = False
class EmbeddedCheck(BaseModel):
    paths: list[str] = Field(min_length=1,max_length=5000)   # only the first 100 are looked at
    force: bool = False                                      # Retry: look again even if a failed answer is saved
class AuditBatch(BaseModel):
    paths: list[str] = Field(min_length=1,max_length=5000)
class JellyfinLink(BaseModel):
    url: str = Field(default='',max_length=512)
    api_key: str = Field(default='',max_length=256)  # empty keeps the saved key
    media_root: str = Field(default='/media',max_length=1024)
    notify: bool = True
    refresh_after_audit: bool = False
class CoverageGenerate(BaseModel):
    what: str = Field(pattern='^(subtitles|lyrics|enhanced)$')
    language: str | None = Field(default=None,max_length=8)
class JellyfinTest(BaseModel):
    url: str | None = Field(default=None,max_length=512)
    api_key: str = Field(default='',max_length=256)
class ExternalRequest(BaseModel):
    path: str = Field(max_length=2048)
    languages: list[str] = Field(min_length=1,max_length=8)


def create_app(config=None,media=None,password=None,run_workers=True):
    root=Path(media or os.environ.get('ZILCHO_MEDIA','/media')).resolve()
    password=password if password is not None else os.environ.get('ZILCHO_PASSWORD','')
    if not 12<=len(password)<=256 or password=='replace-with-your-own-long-password': raise ValueError('Set a unique ZILCHO_PASSWORD of 12–256 characters before starting')
    store=Store(config or os.environ.get('ZILCHO_CONFIG','/config'))
    salt=secrets.token_bytes(32)
    derive=lambda value: hashlib.pbkdf2_hmac('sha256',value.encode(),salt,300000)
    password_hash=derive(password); del password
    sessions={}; failures={}; key_failures={}; auth_lock=threading.Lock(); runner=Runner(store,root); monitor=library.Monitor(store,root)
    @asynccontextmanager
    async def lifespan(app):
        if run_workers: runner.start(); monitor.start()
        yield
        if run_workers: monitor.stop(); runner.stop()
    app=FastAPI(title='Zilcho Subs',version=__version__,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store; app.state.runner=runner; app.state.monitor=monitor
    def authenticated(request): return sessions.get(request.cookies.get('zilcho_session',''),0)>time.time()
    def key_refusal(request):
        # None when the key is right; otherwise the answer. Wrong tries are limited per address.
        ip=request.client.host if request.client else 'unknown'; now=time.time()
        with auth_lock:
            if len(key_failures)>1000: key_failures.clear()
            recent=key_failures[ip]=[x for x in key_failures.get(ip,[]) if now-x<60]
            if len(recent)>=5: return JSONResponse({'detail':'Too many attempts; wait a minute'},429)
            if external.matches(store,request.headers.get('x-api-key','')): return None
            recent.append(now)
        return JSONResponse({'detail':'The API key was not accepted'},401)
    @app.middleware('http')
    async def guard(request,call_next):
        # /api/external/ is for other apps: the API key replaces the sign-in and the page-origin rule.
        keyed=request.url.path.startswith('/api/external/')
        if keyed:
            refused=key_refusal(request)
            if refused: return refused
        if request.method not in ('GET','HEAD','OPTIONS'):
            length=request.headers.get('content-length')
            if length is None and request.method=='DELETE': length='0'  # browsers send no length for an empty DELETE
            if length is None: return JSONResponse({'detail':'Content-Length is required'},411)
            try: count=int(length)
            except ValueError: return JSONResponse({'detail':'Invalid Content-Length'},400)
            if not 0<=count<=2_100_000: return JSONResponse({'detail':'Request too large'},413)
            origin=str(request.base_url).rstrip('/')
            if not keyed and request.headers.get('origin')!=origin:
                return JSONResponse({'detail':'Request must come from this web interface origin'},403)
        if not keyed and request.url.path.startswith('/api/') and request.url.path not in ('/api/login','/api/session','/api/health') and not authenticated(request):
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
    # ----- API key (managed while signed in) and what it allows -----
    @app.get('/api/api-key')
    def api_key_info(): return external.info(store)
    @app.post('/api/api-key')
    def api_key_create(): return {'key':external.create(store),**external.info(store)}
    @app.delete('/api/api-key')
    def api_key_remove(): external.remove(store); return external.info(store)
    @app.get('/api/external/status')
    def external_status(): return {'app':'Zilcho Subs','version':__version__}
    @app.post('/api/external/subtitles')
    def external_subtitles(data:ExternalRequest): return external.request_subtitles(store,root,data.path,data.languages)
    @app.get('/api/external/jobs/{id}')
    def external_job(id:str): return external.job_state(store,id)
    @app.get('/api/state')
    def state():
        catalog=store.config/'catalog.json'; packs=installed_packs(store.config)
        return {'version':__version__,'settings':store.settings().model_dump(),'jobs':store.jobs(),'archived_count':store.archived_count(),
            'models':[{'name':m,'installed':model_ready(store.config,m)} for m in MODELS],
            'sound_model':sound_model_ready(store.config),
            'cleanup_model':cleanup_model_ready(store.config),
            'packs':packs,'targets_by_source':targets_by_source(packs,LANGUAGES),'catalog':json.loads(catalog.read_text(encoding='utf-8')) if catalog.exists() else [],
            'languages':LANGUAGES,'media_root':str(root),'media_available':root.is_dir(),'config_root':str(store.config),
            'ffmpeg':bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))}
    @app.post('/api/settings')
    def settings(data:Settings):
        for folder in data.scan_dirs: media_path(root,folder)
        store.save_settings(data); return {'ok':True}
    # ----- Libraries: named sets of folders. Files outside every library are invisible. -----
    def save_libraries(libraries):
        for item in libraries:
            for folder in item['folders']: media_path(root,folder)
        updated=settings_with_libraries(store.settings(),libraries); store.save_settings(updated); return updated
    @app.get('/api/libraries')
    def libraries_list():
        from . import coverage
        return {'libraries':[l.model_dump() for l in store.settings().libraries],'coverage_max_files':coverage.MAX_FILES}
    @app.post('/api/libraries')
    def libraries_add(data:Library):
        current=[l.model_dump() for l in store.settings().libraries]
        updated=save_libraries(current+[{**data.model_dump(),'id':''}])
        return next(l.model_dump() for l in updated.libraries if l.id not in {c['id'] for c in current})
    @app.put('/api/libraries/{id}')
    def libraries_edit(id:str,data:Library):
        current=[l.model_dump() for l in store.settings().libraries]
        if id not in {c['id'] for c in current}: raise KeyError(id)
        updated=save_libraries([{**data.model_dump(),'id':id} if c['id']==id else c for c in current])
        return next(l.model_dump() for l in updated.libraries if l.id==id)
    @app.delete('/api/libraries/{id}')
    def libraries_delete(id:str):
        current=[l.model_dump() for l in store.settings().libraries]
        if id not in {c['id'] for c in current}: raise KeyError(id)
        save_libraries([c for c in current if c['id']!=id]); return {'ok':True}
    @app.get('/api/folders')
    def folder_list(path:str=''):
        # For the library folder picker: sub-folders only, from the whole media folder
        # (not limited to libraries, since the picker is how you choose them).
        path=path.strip('/'); folder=media_path(root,path)
        if not folder.is_dir(): raise ValueError('Folder not found')
        names=[]; truncated=False
        for p in sorted(folder.iterdir(),key=lambda p:p.name.casefold()):
            if p.name.startswith('.') or p.is_symlink() or not p.is_dir(): continue
            if len(names)>=2000: truncated=True; break
            names.append({'name':p.name,'path':p.relative_to(Path(root).resolve()).as_posix()})
        return {'path':path,'folders':names,'truncated':truncated}
    def visible_folders(library_id):
        # The folders a list may show: one library's (?library=<id>), or every library's.
        return library_folders(store.settings(),library_id or None)
    # ----- Monitor library -----
    @app.post('/api/audit')
    def audit(data:AuditRequest):
        if set(data.kinds)-set(library.KINDS): raise ValueError('Choose video or song')
        generate=tuple(data.kinds) if data.generate else ()
        if data.path is not None: return library.audit_path(store,root,data.path,generate)
        if monitor.alive: monitor.request(generate); return {'started':True}
        # No background thread (tests, or workers turned off): run it here.
        return {'started':False,'queued':monitor.full_audit(generate)}
    @app.post('/api/audit/dismiss')
    def audit_dismiss(): library.dismiss(store); return {'ok':True}
    @app.get('/api/state/audit')
    def audit_state(state:str='',kind:str='',items:bool=True,limit:int=200,offset:int=0,q:str='',missing:str='',sort:str='status',paths:bool=False,lib:str=Query('',alias='library')):
        if state and state not in library.STATES: raise ValueError('Unknown audit state')
        if kind and kind not in library.KINDS: raise ValueError('Choose video or song')
        missing=missing.strip().lower()
        if missing and missing!='lyrics' and not re.fullmatch('[a-z]{2,3}',missing): raise ValueError('Choose a language code or lyrics')
        folders=visible_folders(lib)
        result={'summary':library.summary(store,monitor,folders=folders)}
        if paths:
            # Select all: every matching file that can be queued (missing or failed).
            _total,rows=library.items(store,state or None,kind or None,0,0,q[:200],missing or None,sort,folders)
            picked=[r['path'] for r in rows if r['state'] in ('missing','failed')]
            result.update(total=len(picked),paths=picked)
        elif items:
            total,rows=library.items(store,state or None,kind or None,0 if limit<=0 else min(limit,10000),max(0,offset),q[:200],missing or None,sort,folders)
            inside=embedded.lookup(store,root,[r['path'] for r in rows])   # saved answers only
            skip=store.settings().skip_existing
            for row in rows: row['status']=audit_status(row,inside.get(row['path']),skip)
            result.update(total=total,items=rows)
        return result
    def audit_status(row,inside=None,skip=True):
        # What's next to the file, from the names the last check saw, plus what's inside it (if known).
        stem=Path(row['path']).stem
        if row['kind']=='video':
            langs=set()
            for name in row['found']:
                if name.startswith(stem+'.'):
                    tag=name[len(stem)+1:].split('.')[0].split('-')[0]
                    langs.add(ALIASES.get(tag.lower(),tag))
            status=file_status.video_status_from(sorted(langs),[],inside,skip)
            # The audit decided what's missing (it knows the settings it ran with).
            return {**status,'subs':sorted(langs|(embedded.languages(inside) if skip else set())),'missing':row['missing']}
        return file_status.song_status((root/row['path']).parent,stem,row['found'],inside)
    @app.post('/api/audit/batch')
    def audit_batch(data:AuditBatch):
        # Queue selected: the same as pressing each file's own Make button.
        queued,errors=0,[]
        for path in dict.fromkeys(data.paths):
            try: queued+=len(library.audit_path(store,root,path,library.KINDS)['queued'])
            except (ValueError,OSError) as error:
                errors.append({'path':path,'error':str(error)})
                if 'Queue is full' in str(error): break
        return {'queued':queued,'errors':errors}
    # ----- Jellyfin (optional). The API key never leaves the server. -----
    @app.get('/api/jellyfin')
    def jellyfin_state(): return jellyfin.public(store)
    @app.post('/api/jellyfin')
    def jellyfin_save(data:JellyfinLink):
        if not data.url.strip(): jellyfin.forget(store); return jellyfin.public(store)
        return jellyfin.save(store,data.url,data.api_key,data.media_root,data.notify,data.refresh_after_audit)
    @app.post('/api/jellyfin/test')
    def jellyfin_test(data:JellyfinTest): return {'ok':True,'server':jellyfin.test(store,data.url,data.api_key)}
    @app.post('/api/jellyfin/forget')
    def jellyfin_forget(): jellyfin.forget(store); return jellyfin.public(store)
    # ----- Jellyfin, Emby and Plex (optional), one shape for all three. Keys never leave the server. -----
    def server_kind(kind):
        if kind not in mediaserver.KINDS: raise HTTPException(404,'Unknown media server')
        return kind
    @app.get('/api/servers')
    def servers_state(): return [mediaserver.public(k,store) for k in mediaserver.KINDS]
    @app.get('/api/servers/{kind}')
    def server_state(kind:str): return mediaserver.public(server_kind(kind),store)
    @app.post('/api/servers/{kind}')
    def server_save(kind:str,data:JellyfinLink):
        kind=server_kind(kind)
        if not data.url.strip(): mediaserver.forget(kind,store); return mediaserver.public(kind,store)
        return mediaserver.save(kind,store,data.url,data.api_key,data.media_root,data.notify,data.refresh_after_audit)
    @app.post('/api/servers/{kind}/test')
    def server_test(kind:str,data:JellyfinTest): return {'ok':True,'server':mediaserver.test(server_kind(kind),store,data.url,data.api_key)}
    @app.post('/api/servers/{kind}/forget')
    def server_forget(kind:str): kind=server_kind(kind); mediaserver.forget(kind,store); return mediaserver.public(kind,store)
    @app.get('/api/browse')
    def browse(path:str='',lib:str=Query('',alias='library')):
        folders=visible_folders(lib); path=path.strip('/')
        if not leads_to_folders(path,folders): raise ValueError('That folder is not in a collection')
        folder=media_path(root,path)
        if not folder.is_dir(): raise ValueError('Folder not found')
        listing=sorted(folder.iterdir(),key=lambda p:(not p.is_dir(),p.name.casefold())); names=[p.name for p in listing]
        settings=store.settings(); targets=settings.target_languages; entries=[]; truncated=False; videos=[]
        for i,p in enumerate(listing):
            if i>=5000: truncated=True; break
            if p.is_symlink() or p.name.startswith('.'): continue
            if not (leads_to_folders if p.is_dir() else in_folders)(p.relative_to(root).as_posix(),folders): continue
            if p.is_dir() or p.suffix.lower() in VIDEO or p.suffix.lower()=='.srt':
                entry={'path':p.relative_to(root).as_posix(),'name':p.name,'directory':p.is_dir(),'subtitle':p.suffix.lower()=='.srt','bytes':p.stat().st_size if p.is_file() else 0}
                if p.is_file() and p.suffix.lower() in VIDEO: videos.append((entry,p))
                entries.append(entry)
        # What's inside each video comes from saved answers only (nothing is probed while browsing);
        # the page asks POST /api/embedded/check for the files still unchecked.
        inside=embedded.lookup(store,root,[e['path'] for e,_p in videos])
        for entry,p in videos: entry['status']=file_status.video_status(folder,p.stem,names,targets,inside.get(entry['path']),settings.skip_existing)
        return {'path':path,'entries':entries,'truncated':truncated,'targets':targets}
    @app.post('/api/embedded/check')
    def embedded_check(data:EmbeddedCheck,lib:str=Query('',alias='library')):
        # Looks inside the files a page is showing (saved for next time). Only files in a collection.
        folders=visible_folders(lib); settings=store.settings(); paths=[]
        for path in list(dict.fromkeys(data.paths))[:embedded.MAX_PATHS]:
            try: file=media_path(root,path,file=True,kinds=VIDEO|AUDIO)
            except ValueError: continue   # gone or not a media file: skip it, check the rest
            if in_folders(file.relative_to(Path(root).resolve()).as_posix(),folders): paths.append(path)
        results=embedded.check_many(store,root,paths,data.force)
        statuses={}
        for path,result in results.items():
            file=media_path(root,path,file=True,kinds=VIDEO|AUDIO); parent=file.parent; names=sorted(os.listdir(parent))
            statuses[path]=file_status.video_status(parent,file.stem,names,settings.target_languages,result,settings.skip_existing) if file.suffix.lower() in VIDEO else file_status.song_status(parent,file.stem,names,result)
        return {'statuses':statuses}
    @app.post('/api/coverage/inside')
    def coverage_inside(lib:str=Query('',alias='library')):
        from . import coverage
        return coverage.start_inside_check(store,root,lib or None)
    @app.get('/api/coverage/inside')
    def coverage_inside_progress():
        from . import coverage
        return coverage.inside_progress()
    @app.post('/api/subtitles')
    def translate_subtitles(data:TextRequest):
        import re
        from .text_subtitles import parse_srt
        if (data.text is None)==(data.source_path is None): raise ValueError('Upload an SRT or choose a library SRT, not both')
        source=ALIASES.get(data.source.lower(),data.source.lower())
        if source=='auto' or not re.fullmatch('[a-z]{2,3}',source): raise ValueError('Choose the original subtitle language code')
        targets=Settings(target_languages=data.targets or store.settings().target_languages).target_languages
        reachable=reachable_targets(installed_packs(store.config),source)
        for t in targets:
            if t==source: raise ValueError('Choose a target language different from the original')
            if t not in reachable: raise ValueError(f'No installed route from {source} to {t}. Install it in Language packs')
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
        if video:
            attach=media_path(root,video,file=True)
            if attach.suffix.lower() not in VIDEO: raise ValueError('Attach subtitles to a supported media file')
            if data.video_path and not in_folders(attach.relative_to(Path(root).resolve()).as_posix(),visible_folders('')): raise ValueError('Choose a video inside a collection')
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
    @app.post('/api/jobs/batch')
    def enqueue_batch(data:QueueBatch):
        # Queue selected: one normal subtitle job per file, same as Queue subtitles.
        settings=store.settings().model_dump(); ids,errors=[],[]
        for path in dict.fromkeys(data.paths):
            try:
                media_path(root,path,file=True)
                ids.append(store.enqueue('subtitle',{'path':path,'audio_stream':data.audio_stream,'regenerate':data.regenerate,'settings':settings}))
            except (ValueError,OSError) as error:
                errors.append({'path':path,'error':str(error)})
                if 'Queue is full' in str(error): break
        return {'ids':ids,'count':len(ids),'errors':errors}
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
    def music_browse(path:str='',lib:str=Query('',alias='library')):
        folders=visible_folders(lib); path=path.strip('/')
        if not leads_to_folders(path,folders): raise ValueError('That folder is not in a collection')
        folder=media_path(root,path)
        if not folder.is_dir(): raise ValueError('Folder not found')
        listing=sorted(folder.iterdir(),key=lambda p:(not p.is_dir(),p.name.casefold())); names=[p.name for p in listing]
        targets=store.settings().target_languages; entries=[]; truncated=False; songs=[]
        for i,p in enumerate(listing):
            if i>=5000: truncated=True; break
            if p.is_symlink() or p.name.startswith('.'): continue
            if not (leads_to_folders if p.is_dir() else in_folders)(p.relative_to(root).as_posix(),folders): continue
            if p.is_dir() or p.suffix.lower() in AUDIO:
                entry={'path':p.relative_to(root).as_posix(),'name':p.name,'directory':p.is_dir(),
                    'lyrics':p.is_file() and p.with_name(p.stem+'.lrc').is_file()}
                if p.is_file(): songs.append((entry,p))
                entries.append(entry)
        inside=embedded.lookup(store,root,[e['path'] for e,_p in songs])   # saved answers only
        for entry,p in songs: entry['status']=file_status.song_status(folder,p.stem,names,inside.get(entry['path']))
        return {'path':path,'entries':entries,'truncated':truncated}
    @app.get('/api/library/search')
    def library_file_search(q:str='',kind:str='',limit:int=200,offset:int=0,lib:str=Query('',alias='library')):
        # The Library page's search box: file names across the whole chosen library (read-only).
        visible_folders(lib)  # unknown library: 404
        return library_search.search(store,root,q,kind,lib or None,limit,offset)
    @app.post('/api/lyrics')
    def make_lyrics(data:LyricsItem):
        media_path(root,data.path,file=True,kinds=AUDIO)
        return {'id':store.enqueue('lyrics',{'path':data.path,'regenerate':data.regenerate,'settings':store.settings().model_dump()})}
    @app.post('/api/lyrics/batch')
    def make_lyrics_batch(data:QueueBatch):
        # Queue selected songs: one normal lyrics job each, same as Make lyrics.
        settings=store.settings().model_dump(); ids,errors=[],[]
        for path in dict.fromkeys(data.paths):
            try:
                media_path(root,path,file=True,kinds=AUDIO)
                ids.append(store.enqueue('lyrics',{'path':path,'regenerate':data.regenerate,'settings':settings}))
            except (ValueError,OSError) as error:
                errors.append({'path':path,'error':str(error)})
                if 'Queue is full' in str(error): break
        return {'ids':ids,'count':len(ids),'errors':errors}
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
    @app.get('/api/quality')
    def quality_overview(limit:int=50,offset:int=0,lib:str=Query('',alias='library')):
        from . import quality
        return quality.overview(store,root,limit,offset,visible_folders(lib))
    @app.get('/api/coverage')
    def coverage_report(refresh:bool=False,lib:str=Query('',alias='library')):
        from . import coverage
        return coverage.report(store,root,refresh,lib or None)[0]
    @app.post('/api/coverage/generate')
    def coverage_generate(data:CoverageGenerate,lib:str=Query('',alias='library')):
        from . import coverage
        result=coverage.generate(store,root,data.what,data.language,lib or None)
        return {**result,'coverage':coverage.report(store,root,library=lib or None)[0]}
    @app.get('/api/jobs/{id}/quality')
    def job_quality(id:str):
        from . import quality
        job=store.job(id)
        if job['kind'] not in ('subtitle','text'): raise ValueError('Only subtitle jobs have a quality report')
        return quality.job_report(store,root,job)
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
