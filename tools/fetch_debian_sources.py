"""Preserve exact Debian source packages for every installed runtime source package.
Downloads are content-addressed and checked against the DSC. Archives are never executed.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote
from urllib.request import urlopen

BASE='https://snapshot.debian.org'


def fetch(url):
    last=None
    for attempt in range(4):
        try:
            with urlopen(url,timeout=90) as response: return response.read()
        except OSError as error:
            last=error
            if attempt<3: time.sleep(2**attempt)
    raise last


def checksums(text):
    output={}; active=False
    for line in text.splitlines():
        if line=='Checksums-Sha256:': active=True; continue
        if active:
            if not line.startswith(' '): break
            digest,size,name=line.split()
            if not re.fullmatch('[a-f0-9]{64}',digest): raise ValueError('Invalid DSC checksum')
            output[name]=(digest,int(size))
    if not output: raise ValueError('Source descriptor lacks SHA256 checksums')
    return output


def collect_package(source,version,folder):
    if not re.fullmatch('[a-z0-9][a-z0-9+.-]*',source): raise ValueError('Invalid Debian source name')
    if not re.fullmatch('[A-Za-z0-9.+:~_-]+',version) or version in ('.','..'): raise ValueError('Invalid Debian source version')
    index_url=BASE+'/mr/package/'+source+'/'+quote(version,safe='')+'/srcfiles'
    index=json.loads(fetch(index_url))
    if index.get('package')!=source or index.get('version')!=version: raise ValueError('Snapshot identity mismatch')
    folder=Path(folder)/source/version.replace(':','_'); folder.mkdir(parents=True,exist_ok=True)
    files=[]
    for entry in index['result']:
        digest=entry['hash']
        if not re.fullmatch('[a-f0-9]{40}',digest): raise ValueError('Invalid Snapshot hash')
        info=json.loads(fetch(BASE+'/mr/file/'+digest+'/info'))['result'][0]
        name=info['name']
        if Path(name).name!=name or any(c in name for c in ('/',chr(92),':')): raise ValueError('Unsafe Debian source filename')
        url=BASE+'/file/'+digest; path=folder/name
        if path.exists() and path.stat().st_size==info['size'] and hashlib.sha1(path.read_bytes()).hexdigest()==digest:
            data=path.read_bytes()
        else:
            data=fetch(url)
            if len(data)!=info['size'] or hashlib.sha1(data).hexdigest()!=digest: raise ValueError('Snapshot download mismatch')
            path.write_bytes(data)
        files.append({'name':name,'url':url,'sha1':digest,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
    descriptors=[f for f in files if f['name'].endswith('.dsc')]
    if len(descriptors)!=1: raise ValueError('Expected one source descriptor')
    text=(folder/descriptors[0]['name']).read_text(encoding='utf-8')
    if not re.search(r'^Source: '+re.escape(source)+r'(?:\s|$)',text,re.M) or not re.search(r'^Version: '+re.escape(version)+r'\s*$',text,re.M): raise ValueError('Debian source descriptor identity mismatch')
    by_name={f['name']:f for f in files}
    for name,(digest,size) in checksums(text).items():
        if name not in by_name or by_name[name]['sha256']!=digest or by_name[name]['bytes']!=size: raise ValueError('DSC checksum mismatch: '+name)
    manifest={'source':source,'version':version,'files':files,'index_url':index_url,'dsc_sha256_verified':True,'openpgp_signature_verified':False}
    (folder/'source-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


def collect(inventory,destination):
    data=json.loads(Path(inventory).read_text(encoding='utf-8'))
    required=sorted({(p['source'] or p['name'].split(':')[0],p['source_version'] or p['version']) for p in data['debian']})
    completed=[]; destination=Path(destination); destination.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending={pool.submit(collect_package,source,version,destination):(source,version) for source,version in required}
        for task in as_completed(pending):
            result=task.result(); completed.append(result)
            print('Preserved Debian source:',result['source'],result['version'],flush=True)
    report={'required':[{'source':s,'version':v} for s,v in required],'collected':sorted(completed,key=lambda p:(p['source'],p['version'])),'all_installed_debian_sources_preserved':len(required)==len(completed),'distribution_cleared':False}
    (destination/'debian-source-coverage.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

if __name__=='__main__': collect(*sys.argv[1:])
