"""Fetch the exact Debian FFmpeg source package from Snapshot without executing it.
This is only FFmpeg's source package, not all linked-library corresponding sources.
"""
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote
from urllib.request import urlopen

BASE='https://snapshot.debian.org'


def fetch(url):
    with urlopen(url,timeout=90) as response: return response.read()


def dsc_checksums(text):
    found={}; active=False
    for line in text.splitlines():
        if line=='Checksums-Sha256:': active=True; continue
        if active:
            if not line.startswith(' '): break
            digest,size,name=line.split()
            if not re.fullmatch('[a-f0-9]{64}',digest): raise ValueError('Invalid DSC checksum')
            found[name]=(digest,int(size))
    if not found: raise ValueError('DSC lacks SHA256 checksums')
    return found


def collect(inventory,destination):
    data=json.loads(Path(inventory).read_text(encoding='utf-8'))
    packages=[p for p in data['debian'] if p['name'].split(':')[0]=='ffmpeg']
    if len(packages)!=1: raise ValueError('Expected one installed Debian FFmpeg package')
    package=packages[0]; version=package['source_version']; source=package['source']
    if source!='ffmpeg': raise ValueError('Unexpected FFmpeg source package')
    index_url=BASE+'/mr/package/'+source+'/'+quote(version,safe='')+'/srcfiles'
    index=json.loads(fetch(index_url)); files=[]; destination=Path(destination); destination.mkdir(parents=True,exist_ok=True)
    for entry in index['result']:
        digest=entry['hash']
        if not re.fullmatch('[a-f0-9]{40}',digest): raise ValueError('Unexpected Snapshot file hash')
        info=json.loads(fetch(BASE+'/mr/file/'+digest+'/info'))['result'][0]
        name=info['name']
        if Path(name).name!=name or '/' in name or chr(92) in name: raise ValueError('Unsafe source filename')
        url=BASE+'/file/'+digest; content=fetch(url)
        if len(content)!=info['size'] or hashlib.sha1(content).hexdigest()!=digest: raise ValueError('Snapshot content mismatch')
        (destination/name).write_bytes(content)
        files.append({'name':name,'url':url,'sha1':digest,'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content)})
    descriptors=[f for f in files if f['name'].endswith('.dsc')]
    if len(descriptors)!=1: raise ValueError('Expected one DSC')
    descriptor=(destination/descriptors[0]['name']).read_text(encoding='utf-8')
    if 'Source: ffmpeg' not in descriptor or 'Version: '+version not in descriptor: raise ValueError('DSC version mismatch')
    checksums=dsc_checksums(descriptor)
    by_name={f['name']:f for f in files}
    for name,(digest,size) in checksums.items():
        if name not in by_name or by_name[name]['sha256']!=digest or by_name[name]['bytes']!=size: raise ValueError('DSC SHA256 mismatch: '+name)
    result={'source':source,'source_version':version,'index_url':index_url,'files':files,
        'dsc_sha256_verified':True,'openpgp_signature_verified':False,'distribution_cleared':False,
        'scope':'Exact Debian FFmpeg upstream source archive, detached signature, DSC and Debian packaging/patches only. Linked libraries, PyAV wheel native sources, other image components and compliant public source delivery still require review.'}
    (destination/'source-manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

if __name__=='__main__': collect(*sys.argv[1:])
