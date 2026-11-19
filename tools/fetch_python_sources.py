"""Preserve Python sdists, relevant upstream source fallbacks, Rust lock sources and notices.
Runs on the ephemeral release runner. It never executes downloaded source archives.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path,PurePosixPath
import re
import sys
import subprocess
import tarfile
import tomllib

from app_version import app_version
import zipfile
from urllib.parse import quote,urlparse
from urllib.request import Request,urlopen
import time

FALLBACK={'faster-whisper':('SYSTRAN/faster-whisper','v{version}'),'flatbuffers':('google/flatbuffers','v{version}'),'onnxruntime':('microsoft/onnxruntime','v{version}'),'hf-xet':('huggingface/xet-core','v{version}')}
TERMS=('license','licence','notice','copying','copyright')
# Exact upstream source kept by Software Heritage (https://www.softwareheritage.org), used only when
# the project's own GitHub repository is unavailable. microsoft/onnxruntime vanished from GitHub on
# Nov 17-18, 2026. The commit is the v1.32.0 tag (the same one the shipped wheel reports in
# get_build_info()); the tree is that commit's git tree. Both are content hashes, and the download
# is accepted only if its recomputed git tree hash matches. Like GitHub's own source tarballs, the
# download has no git submodules, so their pinned commits are added back for the hash.
ARCHIVED={('onnxruntime','1.32.0'):{'commit':'fab30a46491a40f5e4c968b80e8a59acbbb91b53',
                                   'tree':'65dbd7f0d881551343749845cb03b616f666e228',
                                   'submodules':{'cmake/external/emsdk':'c0bb220cb6e6f4e0fabb6f6db9efd53390ef5e56',
                                                 'cmake/external/libprotobuf-mutator':'4373c81cd7d50a65f7fbd8dc3779c0c3b1dc9d72',
                                                 'cmake/external/onnx':'e1ac8e74b620bbdc7ba07f0440806bd93f651a28'}}}
SWH='https://archive.softwareheritage.org/api/1/'
# Some hosts (Software Heritage) refuse Python's default client name.
HEADERS={'User-Agent':'zilcho-subs-release-tool'}


def fetch(url):
    for attempt in range(4):
        try:
            with urlopen(Request(url,headers=HEADERS),timeout=180) as response: return response.read()
        except OSError:
            if attempt==3: raise
            time.sleep(2**attempt)


def safe_name(value):
    value=re.sub('[^A-Za-z0-9_.+-]','_',value)
    if not value or value in ('.','..'): raise ValueError('Invalid source name')
    return value


def inspect_archive(data,label,notices):
    locks=[]; notices=Path(notices)/safe_name(label); notices.mkdir(parents=True,exist_ok=True)
    def process(name,size,read):
        basename=PurePosixPath(name).name
        is_notice=any(term in basename.lower() for term in TERMS)
        if not is_notice and basename!='Cargo.lock': return
        if size>20_000_000: raise ValueError('Unexpectedly large notice or Cargo lock')
        content=read()
        if is_notice:
            filename=hashlib.sha256(name.encode()).hexdigest()[:12]+'-'+safe_name(basename)
            (notices/filename).write_bytes(content)
        if basename=='Cargo.lock': locks.append(tomllib.loads(content.decode('utf-8')))
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if not member.is_dir(): process(member.filename,member.file_size,lambda m=member:archive.read(m))
    else:
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as archive:
            for member in archive:
                if member.isfile(): process(member.name,member.size,lambda m=member:archive.extractfile(m).read())
    return locks


def git_archive(repo,ref):
    return 'https://codeload.github.com/'+repo+'/tar.gz/'+quote(ref,safe='')


def git_tree_id(data,submodules=None):
    """The git tree hash of a source tarball's single top folder (files, modes and symlinks).

    submodules maps a path to its pinned commit, for git submodules a tarball can't contain.
    """
    root={}
    for path,commit in (submodules or {}).items():
        parts=PurePosixPath(path).parts; node=root
        for part in parts[:-1]: node=node.setdefault(part,{})
        node[parts[-1]]=('160000',commit)
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as archive:
        for member in archive:
            parts=PurePosixPath(member.name).parts[1:]
            if not parts or member.isdir(): continue
            if member.issym(): mode,content='120000',member.linkname.encode()
            elif member.isfile():
                mode='100755' if member.mode&0o111 else '100644'; content=archive.extractfile(member).read()
            else: raise ValueError('Unexpected archive entry: '+member.name)
            node=root
            for part in parts[:-1]: node=node.setdefault(part,{})
            node[parts[-1]]=(mode,hashlib.sha1(b'blob %d\0'%len(content)+content).hexdigest())
    def tree(node):
        entries=[]
        for name,value in node.items():
            if isinstance(value,dict): entries.append((name+'/','40000',name,tree(value)))
            else: entries.append((name,value[0],name,value[1]))
        body=b''.join(mode.encode()+b' '+name.encode()+b'\0'+bytes.fromhex(sha) for _key,mode,name,sha in sorted(entries))
        return hashlib.sha1(b'tree %d\0'%len(body)+body).hexdigest()
    return tree(root)


def archived_source(name,version):
    """Exact source from Software Heritage, checked against the pinned commit and git tree."""
    pinned=ARCHIVED[(name,version)]
    revision=json.loads(fetch(SWH+'revision/'+pinned['commit']+'/'))
    if revision.get('directory')!=pinned['tree']: raise ValueError('Archived commit does not match its pinned source tree: '+name)
    cook=SWH+'vault/flat/swh:1:dir:'+pinned['tree']+'/'
    status=json.loads(urlopen(Request(cook,method='POST',headers=HEADERS),timeout=60).read())
    for _ in range(180):  # the archive may need a while to package it the first time
        if status.get('status')=='done': break
        if status.get('status')=='failed': raise ValueError('Software Heritage could not package '+name)
        time.sleep(10); status=json.loads(fetch(cook))
    else: raise TimeoutError('Software Heritage is still packaging '+name)
    data=fetch(status['fetch_url'])
    if git_tree_id(data,pinned.get('submodules'))!=pinned['tree']: raise ValueError('Archived source does not match its pinned git tree: '+name)
    return status['fetch_url'],data,pinned


def collect(inventory,destination,notices):
    inventory=json.loads(Path(inventory).read_text(encoding='utf-8'))
    destination=Path(destination); destination.mkdir(parents=True,exist_ok=True)
    records=[]; rust={}
    provided={'numpy':'sources/native/native/numpy-2.4.6.tar.gz','av':'sources/native/native/av-18.1.0.tar.gz','ctranslate2':'sources/native/native/ctranslate2-source-with-submodules.tar.gz','zilcho-subs':'application-source.tar.gz'}
    for dist in inventory['python']:
        name=dist['name'].lower().replace('_','-'); version=dist['version']
        if name in provided:
            expected_version={'numpy':'2.4.6','av':'18.1.0','ctranslate2':'4.9.0','zilcho-subs':app_version()}[name]
            if version!=expected_version: raise ValueError('Controlled source version mismatch: '+name)
            records.append({'name':name,'version':version,'provided_by':provided[name]}); continue
        info=json.loads(fetch('https://pypi.org/pypi/'+quote(name,safe='')+'/'+quote(version,safe='')+'/json'))
        source=next((f for f in info['urls'] if f['packagetype']=='sdist'),None)
        upstream_commit=None; archived=None; data=None
        if source:
            url=source['url']; filename=source['filename']; expected=source['digests']['sha256']
        elif name in FALLBACK:
            repo,tag=FALLBACK[name]; tag=tag.format(version=version)
            try:
                refs=subprocess.check_output(['git','ls-remote','https://github.com/'+repo+'.git','refs/tags/'+tag,'refs/tags/'+tag+'^{}'],text=True,timeout=90,stderr=subprocess.DEVNULL).splitlines()
            except subprocess.CalledProcessError:
                refs=[]
            filename=safe_name(name+'-'+version)+'.tar.gz'; expected=None
            if refs:
                upstream_commit=next((line.split()[0] for line in refs if line.endswith('^{}')),refs[0].split()[0])
                if not re.fullmatch('[a-f0-9]{40}',upstream_commit): raise ValueError('Invalid upstream commit')
                url=git_archive(repo,upstream_commit)
            elif (name,version) in ARCHIVED:
                print('GitHub source unavailable; using the Software Heritage archive:',name,version,flush=True)
                url,data,archived=archived_source(name,version); upstream_commit=archived['commit']
            else: raise ValueError('Missing upstream source tag: '+name+' '+tag)
        else: raise ValueError('No verified source mapping for Python package '+name+' '+version)
        data=data if data is not None else fetch(url); digest=hashlib.sha256(data).hexdigest()
        if expected and digest!=expected: raise ValueError('Python source hash mismatch: '+name)
        folder=destination/safe_name(name)/safe_name(version); folder.mkdir(parents=True,exist_ok=True)
        (folder/safe_name(filename)).write_bytes(data)
        record={'name':name,'version':version,'url':url,'sha256':digest,'bytes':len(data),'pypi_hash_verified':bool(expected),'upstream_commit':upstream_commit}
        if archived:
            record['archived_source']={'archive':'Software Heritage','swhid':'swh:1:dir:'+archived['tree'],
                                       'revision':'swh:1:rev:'+archived['commit'],'git_tree_verified':True}
        for lock in inspect_archive(data,name+'-'+version,notices):
            for package in lock.get('package',[]):
                origin=package.get('source','')
                if origin.startswith('registry+') or origin.startswith('git+'):
                    key=(package['name'],package['version'],origin)
                    rust[key]=package
        # ONNX Runtime's bundled Eigen headers are MPL-2.0. Preserve the pinned Eigen source and upstream patches too.
        if name=='onnxruntime':
            with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as archive:
                dependencies=next((m for m in archive.getmembers() if m.name.endswith('/cmake/deps.txt')),None)
                if dependencies:
                    for line in archive.extractfile(dependencies).read().decode().splitlines():
                        if line.startswith('eigen;'):
                            _,eigen_url,eigen_sha1,*_=line.split(';')
                            eigen=fetch(eigen_url)
                            if hashlib.sha1(eigen).hexdigest()!=eigen_sha1: raise ValueError('ONNX Eigen source hash mismatch')
                            eigen_filename='eigen-source.zip' if zipfile.is_zipfile(io.BytesIO(eigen)) else 'eigen-source.tar.gz'
                            (folder/eigen_filename).write_bytes(eigen)
                            inspect_archive(eigen,'onnxruntime-eigen',notices)
                            record['eigen']={'file':eigen_filename,'url':eigen_url,'sha1':eigen_sha1,'sha256':hashlib.sha256(eigen).hexdigest()}
        if name=='onnxruntime' and 'eigen' not in record: raise ValueError('Missing exact Eigen source mapping for ONNX Runtime')
        records.append(record); print('Preserved Python source:',name,version,flush=True)
    rust_records=[]
    def crate(package):
        name=package['name']; version=package['version']; origin=package['source']
        if origin.startswith('registry+'):
            url='https://static.crates.io/crates/'+quote(name,safe='')+'/'+quote(name+'-'+version,safe='')+'.crate'
            expected=package.get('checksum')
            if not expected or not re.fullmatch('[a-f0-9]{64}',expected): raise ValueError('Missing Rust registry checksum')
        else:
            raw=origin.removeprefix('git+'); parsed=urlparse(raw)
            if parsed.hostname!='github.com' or not re.fullmatch('[a-f0-9]{40}',parsed.fragment): raise ValueError('Unmapped Rust git dependency: '+origin)
            url=git_archive(parsed.path.strip('/').removesuffix('.git'),parsed.fragment); expected=None
        data=fetch(url); digest=hashlib.sha256(data).hexdigest()
        if expected and digest!=expected: raise ValueError('Rust source checksum mismatch: '+name)
        folder=destination/'rust-vendor'; folder.mkdir(exist_ok=True)
        filename=safe_name(name+'-'+version+'-'+digest[:12])+'.tar.gz'; (folder/filename).write_bytes(data)
        inspect_archive(data,'rust-'+name+'-'+version,notices)
        return {'name':name,'version':version,'source':origin,'url':url,'file':filename,'sha256':digest}
    with ThreadPoolExecutor(max_workers=4) as pool: rust_records=list(pool.map(crate,rust.values()))
    report={'python':records,'rust':rust_records,'all_python_source_mappings_preserved':len(records)==len(inventory['python']),'distribution_cleared':False}
    (destination/'python-source-coverage.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

if __name__=='__main__': collect(*sys.argv[1:])
