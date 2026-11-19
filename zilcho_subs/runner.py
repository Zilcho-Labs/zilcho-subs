import hashlib
import json
import logging
import shutil
import os
import signal
import subprocess
import sys
import threading
import time
from .core import VIDEO, allowed_hour, media_path


def scan_once(store, media_root, now=None, limit=5000):
    now=time.time() if now is None else now
    settings=store.settings(); visited=0; enqueued=[]
    signature=hashlib.sha256(json.dumps({k:v for k,v in settings.model_dump().items() if k in ("model","source_language","target_languages","direct_english","skip_existing","prefer_existing")},sort_keys=True).encode()).hexdigest()
    cursor_file=store.config/"scanner-cursor.json"
    try: offset=int(json.loads(cursor_file.read_text())["offset"])
    except (OSError,ValueError,KeyError): offset=0
    skipped=0
    for relative in settings.scan_dirs:
        folder=media_path(media_root,relative)
        for parent,dirs,files in os.walk(folder,followlinks=False):
            dirs[:]=[d for d in sorted(dirs) if not d.startswith('.') and not (type(folder)(parent)/d).is_symlink()]
            for name in sorted(files):
                if skipped<offset: skipped+=1; continue
                visited+=1
                if visited>limit:
                    cursor_file.write_text(json.dumps({"offset":offset+visited-1})); return enqueued
                path=type(folder)(parent)/name
                if path.is_symlink() or path.suffix.lower() not in VIDEO: continue
                relative=path.relative_to(type(folder)(media_root).resolve()).as_posix()
                stat=path.stat()
                with store.db() as db:
                    row=db.execute("SELECT * FROM seen WHERE path=?",(relative,)).fetchone()
                    if not row or row['size']!=stat.st_size or row['mtime']!=stat.st_mtime_ns:
                        db.execute("INSERT OR REPLACE INTO seen VALUES(?,?,?,?,?)",(relative,stat.st_size,stat.st_mtime_ns,now,"")); continue
                    if now-row['stable']<settings.stable_seconds or row['signature']==signature: continue
                id=store.enqueue("subtitle",{"path":relative,"settings":settings.model_dump(),"audio_stream":None,"regenerate":False})
                with store.db() as db: db.execute("UPDATE seen SET signature=? WHERE path=?",(signature,relative))
                enqueued.append(id)
    cursor_file.write_text(json.dumps({"offset":0}))
    return enqueued


class Runner:
    def __init__(self,store,media_root):
        self.store=store; self.root=media_root; self.stop_event=threading.Event(); self.thread=None; self.process=None; self.last_scan=0
    def start(self):
        self.store.recover()
        self.thread=threading.Thread(target=self.loop,name="zilcho-queue",daemon=True); self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=8)
    def terminate(self,p):
        if p.poll() is not None: return
        if os.name=='nt': p.terminate()
        else:
            try: os.killpg(p.pid,signal.SIGTERM)
            except ProcessLookupError: return
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name=='nt': p.kill()
            else:
                try: os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            p.wait(timeout=3)
    def loop(self):
        while not self.stop_event.is_set():
            job=None
            try:
                settings=self.store.settings()
                if settings.watcher_enabled and time.time()-self.last_scan>=settings.scan_interval:
                    self.last_scan=time.time(); scan_once(self.store,self.root)
                if settings.paused or not allowed_hour(settings,time.localtime().tm_hour): self.stop_event.wait(1); continue
                job=self.store.claim()
                if not job: self.stop_event.wait(1); continue
                log=self.store.config/'logs'/(job['id']+'.log')
                env=os.environ.copy(); env['ZILCHO_CONFIG']=str(self.store.config); env['ZILCHO_MEDIA']=str(self.root)
                # Do not give inference children the web UI password.
                env.pop('ZILCHO_PASSWORD',None)
                with log.open('ab') as output:
                    p=subprocess.Popen([sys.executable,'-m','zilcho_subs.worker',job['id']],stdout=output,stderr=subprocess.STDOUT,env=env,start_new_session=os.name!='nt')
                    self.process=p
                    while p.poll() is None:
                        if self.stop_event.wait(.25) or self.store.job(job['id'])['cancel']:
                            self.terminate(p); self.store.update(job['id'],status='cancelled' if not self.stop_event.is_set() else 'failed',stage='Cancelled' if not self.stop_event.is_set() else 'Interrupted'); break
                    self.process=None
                for temporary in (self.store.config/'work').glob(job['id']+'-*'):
                    if temporary.is_dir() and not temporary.is_symlink(): shutil.rmtree(temporary)
                latest=self.store.job(job['id'])
                if latest['status'] in ('running','cancelling','pausing'):
                    self.store.update(job['id'],status='failed',stage='Failed',error='Worker stopped unexpectedly; check the local job log. Memory limits may need adjustment.')
            except Exception as error:
                logging.getLogger(__name__).exception('Queue loop failed')
                if job:
                    self.store.update(job['id'],status='failed',stage='Failed',error=str(error)[:600])
                # A bad share or worker must not kill the scheduler. State remains inspectable.
                self.stop_event.wait(2)
