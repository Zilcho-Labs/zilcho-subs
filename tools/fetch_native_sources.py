"""Fetch checksum-pinned native build sources; never reads user media/configuration."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from urllib.request import urlopen


def main(lock_path,archive_dir,work_dir):
    lock=json.loads(Path(lock_path).read_text(encoding='utf-8'))
    archives=Path(archive_dir); work=Path(work_dir)
    archives.mkdir(parents=True,exist_ok=True); work.mkdir(parents=True,exist_ok=True)
    for key in ('ffmpeg','ffmpeg_signature','ffmpeg_key','pyav','numpy'):
        item=lock[key]; filename=item['url'].rsplit('/',1)[1]
        with urlopen(item['url'],timeout=120) as response: data=response.read()
        if hashlib.sha256(data).hexdigest()!=item['sha256']: raise ValueError('Native source checksum mismatch: '+key)
        path=archives/filename; path.write_bytes(data)
        if key in ('ffmpeg','pyav','numpy'):
            with tarfile.open(path) as archive: archive.extractall(work,filter='data')
    ct=lock['ctranslate2']; checkout=work/'ctranslate2'
    subprocess.run(['git','init',str(checkout)],check=True)
    def git(*args):
        result=subprocess.run(['git','-C',str(checkout),*args],capture_output=True,text=True)
        if result.returncode: raise RuntimeError('Native checkout failed: '+result.stderr[:2500])
        return result.stdout
    git('remote','add','origin',ct['repository']); git('fetch','--depth=1','origin',ct['commit']); git('checkout','--detach','FETCH_HEAD')
    actual=git('rev-parse','HEAD').strip()
    if actual!=ct['commit']: raise ValueError('CTranslate2 checkout mismatch')
    git('submodule','update','--init','--recursive','--depth=1','--',*ct['submodules'])
    status=git('submodule','status','--recursive')
    (archives/'ctranslate2-submodules.txt').write_text(status,encoding='utf-8')
    with tarfile.open(archives/'ctranslate2-source-with-submodules.tar.gz','w:gz') as archive:
        archive.add(checkout,arcname='ctranslate2',filter=lambda info:None if '.git' in Path(info.name).parts else info)
    (archives/'native-lock.json').write_text(json.dumps(lock,indent=2),encoding='utf-8')
    manifest=[]
    for path in sorted(archives.iterdir()):
        if path.is_file(): manifest.append({'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
    (archives/'SHA256-MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')

if __name__=='__main__': main(*sys.argv[1:])
