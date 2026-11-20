import logging
import shutil
import os
import signal
import subprocess
import sys
import threading
import time
from .core import allowed_hour
from .monitor import scan_once  # noqa: F401  (old import path)


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
                settings=self.store.settings()  # library watching runs in monitor.Monitor
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
