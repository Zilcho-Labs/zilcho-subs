import hashlib
import json
import math
import os
import re
import sqlite3
import textwrap
import time
import uuid
from pathlib import Path, PurePosixPath
from contextlib import contextmanager
from pydantic import BaseModel, Field, field_validator

MODELS = ("tiny", "base", "small", "medium", "large-v3")
LANGUAGES = {"auto":"Auto detect", "ja":"Japanese", "ko":"Korean", "zh":"Chinese", "en":"English", "es":"Spanish", "fr":"French", "de":"German", "it":"Italian", "pt":"Portuguese", "ru":"Russian", "ar":"Arabic", "hi":"Hindi", "vi":"Vietnamese", "th":"Thai", "id":"Indonesian", "nl":"Dutch", "pl":"Polish", "uk":"Ukrainian", "tr":"Turkish"}
VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts"}
AUDIO = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aiff", ".aif", ".alac", ".ape", ".wv"}
ALIASES = {"eng":"en","jpn":"ja","kor":"ko","zho":"zh","chi":"zh","spa":"es","fra":"fr","fre":"fr","deu":"de","ger":"de","por":"pt","ita":"it","rus":"ru","ara":"ar","hin":"hi","vie":"vi","tha":"th","ind":"id","nld":"nl","dut":"nl"}
ACTIVE = ("queued", "running", "cancelling", "pausing", "paused")

class JobPaused(Exception):
    pass

class Settings(BaseModel):
    model: str = "medium"
    source_language: str = "auto"
    target_languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1, max_length=8)
    cpu_threads: int = Field(default=2, ge=1, le=32)
    watcher_enabled: bool = False
    scan_dirs: list[str] = Field(default_factory=lambda: [""])
    scan_interval: int = Field(default=120, ge=30, le=86400)
    stable_seconds: int = Field(default=120, ge=30, le=86400)
    schedule_start: int = Field(default=0, ge=0, le=23)
    schedule_end: int = Field(default=0, ge=0, le=23)
    paused: bool = False
    skip_existing: bool = True
    direct_english: bool = True
    prefer_existing: bool = True
    sound_captions: bool = False
    clean_dialogue: bool = False  # DeepFilterNet3 before speech recognition (videos only)
    lyrics_language: str = "auto"
    lyrics_words: bool = True
    lyrics_lrc_words: bool = False  # word timing inside Song.lrc (Jellyfin reads only Song.lrc)

    @field_validator("model")
    @classmethod
    def model_valid(cls, value):
        if value not in MODELS: raise ValueError("Choose tiny, base, small, medium or large-v3")
        return value

    @field_validator("source_language", "lyrics_language")
    @classmethod
    def source_valid(cls, value):
        if value != "auto" and not re.fullmatch(r"[a-z]{2,3}", value): raise ValueError("Use a supported speech language code")
        return value

    @field_validator("target_languages")
    @classmethod
    def targets_valid(cls, values):
        if any(not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", x) for x in values): raise ValueError("Invalid target language code")
        return list(dict.fromkeys(values))

    @field_validator("scan_dirs")
    @classmethod
    def dirs_valid(cls, values):
        if len(values) > 32: raise ValueError("Too many folders")
        for value in values: safe_relative(value, allow_empty=True)
        return list(dict.fromkeys(values))

def safe_relative(value, allow_empty=False):
    if not isinstance(value,str) or len(value)>2048 or "\\" in value or ":" in value or "\x00" in value: raise ValueError("Use a relative media path")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or (not value and not allow_empty): raise ValueError("Path must stay inside the media folder")
    return p

def media_path(root, relative, file=False, kinds=VIDEO):
    safe_relative(relative, allow_empty=not file)
    base = Path(root).resolve()
    candidate = base / relative
    # Reject symlinks, even ones resolving back inside the mount.
    cursor = candidate
    while cursor != base:
        if cursor.is_symlink(): raise ValueError("Symlinked media paths are not supported")
        cursor = cursor.parent
    candidate = candidate.resolve()
    if not candidate.is_relative_to(base): raise ValueError("Path escapes the media mount")
    if file and (not candidate.is_file() or candidate.suffix.lower() not in kinds): raise ValueError("Choose an existing song file" if kinds is AUDIO else "Choose an existing video file")
    return candidate

def allowed_hour(settings, hour):
    a,b = settings.schedule_start, settings.schedule_end
    return a == b or (a <= hour < b if a < b else hour >= a or hour < b)

# Jobs that have stopped for good. Only these can be archived or removed.
FINISHED = ('completed', 'failed', 'skipped', 'cancelled')
_FINISHED_SQL = "('completed','failed','skipped','cancelled')"

class Store:
    def __init__(self, config):
        self.config = Path(config).resolve(); self.config.mkdir(parents=True, exist_ok=True)
        for name in ("models","work","transcripts","packages","logs","cache","inputs","exports","lyrics"): (self.config/name).mkdir(exist_ok=True)
        self.path = self.config / "queue.sqlite3"
        with self.db() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, spec TEXT NOT NULL, dedupe TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                outputs TEXT NOT NULL DEFAULT '{}', cancel INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
              CREATE UNIQUE INDEX IF NOT EXISTS active_jobs ON jobs(dedupe) WHERE status IN ('queued','running','cancelling');
              CREATE TABLE IF NOT EXISTS seen(path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, stable REAL, signature TEXT DEFAULT '');
              CREATE TABLE IF NOT EXISTS lyric_files(path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, updated REAL NOT NULL);
            """)
            # Upgrade existing app-data in place; serialize migrations across web/worker processes.
            db.execute("BEGIN IMMEDIATE")
            columns={r[1] for r in db.execute("PRAGMA table_info(jobs)")}
            for name in ('queue_order','pause_requested','resume_after_pause','archived'):
                if name not in columns: db.execute('ALTER TABLE jobs ADD COLUMN '+name+' INTEGER NOT NULL DEFAULT 0')
            if 'queue_order' not in columns:
                for order,row in enumerate(db.execute('SELECT id FROM jobs ORDER BY created,id').fetchall(),1):
                    db.execute('UPDATE jobs SET queue_order=? WHERE id=?',(order,row[0]))
            definition=db.execute("SELECT sql FROM sqlite_master WHERE name='active_jobs'").fetchone()[0]
            if 'pausing' not in definition:
                db.execute('DROP INDEX active_jobs')
                db.execute("CREATE UNIQUE INDEX active_jobs ON jobs(dedupe) WHERE status IN ('queued','running','cancelling','pausing','paused')")
            db.execute("INSERT OR IGNORE INTO settings VALUES(1,?)", (Settings().model_dump_json(),))
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path, timeout=15); db.row_factory=sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=15000"); yield db; db.commit()
        except Exception: db.rollback(); raise
        finally: db.close()
    def settings(self):
        with self.db() as db: return Settings.model_validate_json(db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
    def owns_lyrics(self,path,sha256):
        """True if Zilcho Subs wrote this lyric file and nobody has changed it since."""
        with self.db() as db: row=db.execute("SELECT sha256 FROM lyric_files WHERE path=?",(path,)).fetchone()
        return bool(row) and row[0]==sha256
    def own_lyrics(self,path,sha256):
        with self.db() as db: db.execute("INSERT OR REPLACE INTO lyric_files VALUES(?,?,?)",(path,sha256,time.time()))
    def save_settings(self, settings):
        with self.db() as db: db.execute("UPDATE settings SET data=? WHERE id=1", (settings.model_dump_json(),))
    def enqueue(self, kind, spec):
        key=hashlib.sha256(json.dumps([kind,spec],sort_keys=True).encode()).hexdigest()
        now=time.time(); id=uuid.uuid4().hex
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing=db.execute("SELECT id FROM jobs WHERE dedupe=? AND status IN ('queued','running','cancelling','pausing','paused')",(key,)).fetchone()
            if existing: return existing[0]
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running','cancelling','pausing','paused')").fetchone()[0]>=500: raise ValueError("Queue is full; wait for jobs to finish")
            try: db.execute("INSERT INTO jobs(id,kind,spec,dedupe,status,stage,created,updated,queue_order) VALUES(?,?,?,?,?,?,?,?,?)",(id,kind,json.dumps(spec),key,"queued","Waiting",now,now,db.execute("SELECT COALESCE(MAX(queue_order),0)+1 FROM jobs").fetchone()[0]))
            except sqlite3.IntegrityError: return db.execute("SELECT id FROM jobs WHERE dedupe=? AND status IN ('queued','running','cancelling','pausing','paused')",(key,)).fetchone()[0]
        return id
    def job(self,id):
        with self.db() as db: row=db.execute("SELECT * FROM jobs WHERE id=?",(id,)).fetchone()
        if not row: raise KeyError(id)
        value=dict(row)
        for k in ("spec","outputs"): value[k]=json.loads(value[k])
        return value
    def jobs(self):
        with self.db() as db: ids=[r[0] for r in db.execute("""WITH history AS (SELECT id FROM jobs WHERE status NOT IN ('queued','running','cancelling','pausing','paused') AND archived=0 ORDER BY created DESC LIMIT 100)
                SELECT id FROM jobs WHERE status IN ('queued','running','cancelling','pausing','paused') OR id IN (SELECT id FROM history)
                ORDER BY CASE WHEN status IN ('running','cancelling','pausing') THEN 0 WHEN status IN ('queued','paused') THEN 1 ELSE 2 END,
                CASE WHEN status IN ('queued','running','cancelling','pausing','paused') THEN queue_order ELSE 0 END,created DESC,id""")]
        return [self.job(id) for id in ids]
    def archived_jobs(self):
        """Finished jobs moved out of Recent jobs, newest first."""
        with self.db() as db:
            ids=[r[0] for r in db.execute("SELECT id FROM jobs WHERE archived=1 AND status IN "+_FINISHED_SQL+" ORDER BY updated DESC,id LIMIT 500")]
        return [self.job(id) for id in ids]
    def archived_count(self):
        with self.db() as db: return db.execute("SELECT COUNT(*) FROM jobs WHERE archived=1 AND status IN "+_FINISHED_SQL).fetchone()[0]
    def archive(self,id,archived=True):
        """Hides a finished job from Recent jobs (or brings it back). Nothing on disk changes."""
        self.job(id)
        with self.db() as db:
            if not db.execute("UPDATE jobs SET archived=? WHERE id=? AND status IN "+_FINISHED_SQL,(1 if archived else 0,id)).rowcount:
                raise ValueError('Only finished jobs can be archived; cancel it first')
    def remove(self,id):
        """Deletes a finished job's entry from the list. Subtitles, videos and other
        files are never touched; only the job's own leftover resume data is cleared."""
        self.job(id)
        with self.db() as db:
            if not db.execute("DELETE FROM jobs WHERE id=? AND status IN "+_FINISHED_SQL,(id,)).rowcount:
                raise ValueError('Only finished jobs can be removed; cancel it first')
        checkpoint=self.config/'transcripts'/'checkpoints'/(id+'.json')
        if checkpoint.is_file(): checkpoint.unlink()
    def apply(self,ids,action):
        """Archives, restores or removes the chosen jobs. Jobs that aren't finished
        are left alone. Returns how many changed."""
        if action not in ('archive','unarchive','remove'): raise ValueError('Invalid action')
        ids=sorted(set(ids))
        if not ids: return 0
        marks=','.join('?'*len(ids))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            chosen=[r[0] for r in db.execute('SELECT id FROM jobs WHERE id IN ('+marks+') AND status IN '+_FINISHED_SQL,ids)]
            for id in chosen:
                if action=='remove': db.execute('DELETE FROM jobs WHERE id=?',(id,))
                else: db.execute('UPDATE jobs SET archived=? WHERE id=?',(1 if action=='archive' else 0,id))
        if action=='remove':
            for id in chosen:
                checkpoint=self.config/'transcripts'/'checkpoints'/(id+'.json')
                if checkpoint.is_file(): checkpoint.unlink()
        return len(chosen)
    def clear(self,statuses,action,archived=False):
        """Archives or removes every finished job with one of these statuses, either in
        Recent jobs or (archived=True) in the archive. Returns how many changed."""
        statuses=sorted(set(statuses))
        if not statuses or set(statuses)-set(FINISHED): raise ValueError('Choose finished job types to clear')
        if action not in ('archive','remove'): raise ValueError('Invalid clean-up action')
        if action=='archive' and archived: raise ValueError('Those jobs are already archived')
        marks=','.join('?'*len(statuses))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            ids=[r[0] for r in db.execute('SELECT id FROM jobs WHERE archived=? AND status IN ('+marks+')',(1 if archived else 0,*statuses))]
            for id in ids:
                if action=='archive': db.execute('UPDATE jobs SET archived=1 WHERE id=?',(id,))
                else: db.execute('DELETE FROM jobs WHERE id=?',(id,))
        if action=='remove':
            for id in ids:
                checkpoint=self.config/'transcripts'/'checkpoints'/(id+'.json')
                if checkpoint.is_file(): checkpoint.unlink()
        return len(ids)
    def update(self,id,**fields):
        if not fields or set(fields)-{"status","stage","progress","error","outputs","cancel"}: raise ValueError("Invalid job update")
        fields={k:json.dumps(v) if k=="outputs" else v for k,v in fields.items()}; fields["updated"]=time.time()
        with self.db() as db: db.execute("UPDATE jobs SET "+",".join(k+"=?" for k in fields)+" WHERE id=?",[*fields.values(),id])
    def cancel(self,id):
        self.job(id)
        with self.db() as db:
            db.execute("UPDATE jobs SET cancel=1,pause_requested=0,resume_after_pause=0,status=CASE WHEN status IN ('queued','paused') THEN 'cancelled' ELSE 'cancelling' END,updated=? WHERE id=? AND status IN ('queued','running','cancelling','pausing','paused')",(time.time(),id))
    def claim(self):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT id FROM jobs WHERE status='queued' AND cancel=0 ORDER BY queue_order,created,id LIMIT 1").fetchone()
            if not row: return None
            db.execute("UPDATE jobs SET status='running',stage='Starting',updated=? WHERE id=?",(time.time(),row[0]))
        return self.job(row[0])
    def pause(self,id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status,kind FROM jobs WHERE id=?',(id,)).fetchone()
            if not row: raise KeyError(id)
            if row['status']=='queued': db.execute("UPDATE jobs SET status='paused',stage='Paused',updated=? WHERE id=?",(time.time(),id))
            elif row['status']=='running' and row['kind']=='subtitle':
                db.execute("UPDATE jobs SET status='pausing',pause_requested=1,resume_after_pause=0,stage='Pausing after current speech window',updated=? WHERE id=?",(time.time(),id))
            else: raise ValueError('Only queued jobs or a running video subtitle job can be paused')
    def resume(self,id):
        with self.db() as db:
            changed=db.execute("UPDATE jobs SET status='queued',pause_requested=0,resume_after_pause=0,stage='Waiting to resume',updated=? WHERE id=? AND status='paused' AND cancel=0",(time.time(),id)).rowcount
            if not changed: raise ValueError('Only paused jobs can be resumed')
    def finish_pause(self,id):
        with self.db() as db:
            db.execute("""UPDATE jobs SET status=CASE WHEN cancel=1 THEN 'cancelled' WHEN resume_after_pause=1 THEN 'queued' ELSE 'paused' END,
                stage=CASE WHEN cancel=1 THEN 'Cancelled' WHEN resume_after_pause=1 THEN 'Waiting to resume after priority job' ELSE 'Paused at checkpoint' END,
                pause_requested=0,resume_after_pause=0,updated=? WHERE id=? AND status IN ('running','pausing','cancelling')""",(time.time(),id))
    def move(self,id,direction):
        if direction not in ('up','down','top','bottom'): raise ValueError('Invalid queue direction')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute("SELECT id,queue_order FROM jobs WHERE status IN ('queued','paused') ORDER BY queue_order,created,id").fetchall()
            ids=[r['id'] for r in rows];slots=[r['queue_order'] for r in rows]
            if id not in ids: raise ValueError('Only queued or paused jobs can be moved')
            index=ids.index(id);ids.pop(index)
            target={'up':max(0,index-1),'down':min(len(ids),index+1),'top':0,'bottom':len(ids)}[direction]
            ids.insert(target,id)
            for order,item in zip(slots,ids): db.execute('UPDATE jobs SET queue_order=? WHERE id=?',(order,item))
    def run_next(self,id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            target=db.execute('SELECT status FROM jobs WHERE id=?',(id,)).fetchone()
            if not target or target['status'] not in ('queued','paused'): raise ValueError('Choose a queued or paused job to run next')
            running=db.execute("SELECT id,kind FROM jobs WHERE status IN ('running','pausing') LIMIT 1").fetchone()
            if running and running['kind']!='subtitle': raise ValueError('The current download/text job cannot be interrupted; move this job to the top instead')
            first=db.execute('SELECT COALESCE(MIN(queue_order),0)-1 FROM jobs').fetchone()[0]
            db.execute("UPDATE jobs SET status='queued',queue_order=?,pause_requested=0,resume_after_pause=0,stage='Next in queue',updated=? WHERE id=?",(first,time.time(),id))
            if running:
                db.execute("UPDATE jobs SET status='pausing',pause_requested=1,resume_after_pause=1,stage='Pausing for priority job; will resume automatically',updated=? WHERE id=?",(time.time(),running['id']))
    def recover(self):
        with self.db() as db:
            db.execute("UPDATE jobs SET status=CASE WHEN resume_after_pause=1 THEN 'queued' ELSE 'paused' END,pause_requested=0,resume_after_pause=0,stage='Interrupted pause; checkpoint retained',updated=? WHERE status='pausing'",(time.time(),))
            db.execute("UPDATE jobs SET status='failed',stage='Interrupted',error='Service restarted during this job. Retry explicitly.',updated=? WHERE status IN ('running','cancelling')",(time.time(),))


def stamp(seconds):
    ms=round(seconds*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def render_srt(segments):
    blocks=[]; previous=0.0
    for segment in segments:
        start,end=float(segment["start"]),float(segment["end"])
        if not all(math.isfinite(x) and x>=0 for x in (start,end)) or end<=start: raise ValueError("Invalid subtitle timing")
        text=re.sub(r"<[^>]*>","",str(segment["text"])); text=" ".join(text.replace("\x00","").split())
        if not text: continue
        if start<previous: raise ValueError('Overlapping speech cues; refusing to silently shift subtitle timing')
        # Wrap text spatially only. New temporal cues require actual word/speech
        # boundaries upstream, not proportional guesses from translated text length.
        lines=textwrap.wrap(text,42,break_long_words=True,break_on_hyphens=False)
        blocks.append(f"{len(blocks)+1}\n{stamp(start)} --> {stamp(end)}\n"+"\n".join(lines))
        previous=end
    if not blocks: raise ValueError("No speech was recognized; no empty subtitle was written")
    return "\n\n".join(blocks)+"\n"

def validate_srt(text):
    if len(text.encode()) > 2_000_000: raise ValueError("Subtitle too large")
    blocks=re.split(r"\n\s*\n",text.replace("\r\n","\n").strip())
    previous=0
    for block in blocks:
        lines=block.splitlines()
        if len(lines)<3 or not lines[0].isdigit(): raise ValueError("Invalid SRT cue")
        m=re.fullmatch(r"(\d{2,}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2,}):(\d{2}):(\d{2}),(\d{3})",lines[1])
        if not m: raise ValueError("Invalid SRT timestamp")
        n=list(map(int,m.groups()))
        if any(n[i]>=60 for i in (1,2,5,6)): raise ValueError("Invalid timestamp range")
        a=((n[0]*60+n[1])*60+n[2])*1000+n[3]; b=((n[4]*60+n[5])*60+n[6])*1000+n[7]
        if a<previous or b<=a: raise ValueError("Overlapping or reversed cues")
        if any('<' in line or '\x00' in line for line in lines[2:]): raise ValueError("Use plain subtitle text, not markup")
        previous=b
    if not blocks or not text.strip(): raise ValueError("Empty subtitle")
    return "\n\n".join(blocks)+"\n"

def write_sidecar(media, language, text, version=False):
    if not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", language): raise ValueError("Invalid language")
    temp=media.parent/("."+media.name+".zilchosubs-"+uuid.uuid4().hex+".tmp")
    try:
        with temp.open("x",encoding="utf-8",newline="\n") as out: out.write(text); out.flush(); os.fsync(out.fileno())
        for i in range(1,101 if version else 2):
            suffix="" if i==1 else f".{i}"
            destination=media.with_name(f"{media.stem}.{language}.AI{suffix}.srt")
            try:
                # Same-directory hard link publishes a complete file atomically, never overwrites.
                os.link(temp,destination); return destination
            except FileExistsError:
                if not version: raise FileExistsError("AI subtitle already exists; use a versioned regeneration")
        raise ValueError("Too many subtitle versions")
    finally: temp.unlink(missing_ok=True)
