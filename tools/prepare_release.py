"""Build a paired, preserved private release candidate from an already-tested image.
No registry push, release publication, repository visibility or server changes occur here.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
from urllib.request import urlopen

from app_version import app_version


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def run(args,**kwargs): return subprocess.run(args,check=True,**kwargs)


def image_info(image): return json.loads(subprocess.check_output(['docker','image','inspect',image]))[0]


def archive_folder(folder,target):
    with tarfile.open(target,'w:gz',compresslevel=6) as archive:
        for path in sorted(Path(folder).iterdir()): archive.add(path,arcname=path.name)


def validate_native(report):
    forbidden=('/av.libs/','/ctranslate2.libs/','/numpy.libs/')
    paths=[f['path'] for f in report['files']]
    if any(part in p for p in paths for part in forbidden): raise ValueError('Opaque native wheel libraries remain')
    for library in ('libavcodec.so','libctranslate2.so'):
        if not any('/opt/zilcho-native/lib/'+library in p for p in paths): raise ValueError('Controlled native library missing: '+library)
    if not any('openblas' in p for p in paths): raise ValueError('System OpenBLAS not observed')


def validate_build_sources(evidence):
    evidence=Path(evidence)
    originals=list((evidence/'native-build').rglob('*'))
    if not any(p.is_file() for p in originals): raise ValueError('Native build evidence is missing')
    for path in originals:
        if path.is_file():
            exported=evidence/'sources/native/build'/path.relative_to(evidence/'native-build')
            if not exported.is_file() or sha256(path)!=sha256(exported):
                raise ValueError('Exported build sources do not match the tested native build: '+path.name)


def validate_onnx_source(native,python_sources):
    build=native.get('onnxruntime_build_info','')
    match=re.search(r'git-commit-id=([0-9a-f]{7,40})',build)
    record=next(p for p in python_sources['python'] if p['name']=='onnxruntime')
    if not match or not (record.get('upstream_commit') or '').startswith(match.group(1)):
        raise ValueError('ONNX Runtime binary commit does not match the preserved upstream source')


def candidate_smoke(image,name):
    password='ci-'+os.urandom(24).hex(); env=os.environ.copy(); env['ZILCHO_PASSWORD']=password
    # Volumes are ephemeral runner-only data, never user media. No port is published.
    run(['docker','run','-d','--name',name,'--read-only','--tmpfs','/tmp:size=128m','--cpus=2','--memory=6g',
        '-e','ZILCHO_PASSWORD','-v',name+'-config:/config','-v',name+'-media:/media',image],env=env)
    probe='''import urllib.request,urllib.error,time,json,http.cookiejar,os
base='http://127.0.0.1:8765'
for i in range(30):
    try: urllib.request.urlopen(base+'/api/health',timeout=2); break
    except OSError: time.sleep(1)
else: raise RuntimeError('Candidate did not start')
try: urllib.request.urlopen(base+'/api/state'); raise AssertionError('Unauthenticated state was exposed')
except urllib.error.HTTPError as error: assert error.code==401
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
request=urllib.request.Request(base+'/api/login',data=json.dumps({'password':os.environ['ZILCHO_PASSWORD']}).encode(),headers={'Origin':base,'Content-Type':'application/json'})
assert opener.open(request).status==200
assert json.load(opener.open(base+'/api/state'))['version']=='__APP_VERSION__'
print('Final candidate startup/authentication verified')
'''.replace('__APP_VERSION__',app_version())
    try: run(['docker','exec','-i','--user','99:100',name,'python','-'],input=probe,text=True)
    finally: run(['docker','stop',name]); run(['docker','rm',name])


def prepare(evidence,destination,image,commit,version):
    from verify_release import inspect_image_archive
    from fetch_debian_sources import collect as debian_sources
    from fetch_python_sources import collect as python_sources,inspect_archive
    if not re.fullmatch('[a-f0-9]{40}',commit) or not re.fullmatch('[0-9A-Za-z._-]+',version): raise ValueError('Invalid release identity')
    evidence=Path(evidence).resolve(); destination=Path(destination).resolve()
    if destination.exists(): raise ValueError('Refusing to overwrite an existing candidate folder')
    if not version.startswith(app_version()+'-rc.'): raise ValueError('Candidate version must start with the app version '+app_version())
    native=json.loads((evidence/'native-linkage.json').read_text(encoding='utf-8')); validate_native(native)
    validate_build_sources(evidence)
    inventory=json.loads((evidence/'dependencies/inventory.json').read_text(encoding='utf-8'))
    excluded={'minisbd','torch','stanza','spacy','argostranslate'}
    if any(p['name'].lower() in excluded for p in inventory['python']): raise ValueError('Unused segmentation runtime is still shipped')
    actual_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    if actual_commit!=commit: raise ValueError('Source checkout differs from release commit')
    run(['git','diff','--exit-code'])
    original=image_info(image)
    identity=json.loads((evidence/'image-identity.json').read_text(encoding='utf-8'))
    if identity['Id']!=original['Id'] or identity['source_commit']!=commit:
        raise ValueError('Evidence does not identify the tested image and source commit')
    destination.mkdir(parents=True)
    materials=destination/'materials'; materials.mkdir()
    notices=destination/'overlay'/'release-notices'; notices.mkdir(parents=True)
    shutil.copytree(evidence/'sources/native',materials/'sources/native')
    shutil.copytree(evidence/'dependencies',notices/'runtime')
    for directory in ('upstream-notices','build-dependency-notices'):
        if (evidence/directory).is_dir(): shutil.copytree(evidence/directory,notices/directory)
    for path in (materials/'sources/native/native').iterdir():
        if not path.name.endswith(('.tar.gz','.tar.xz','.tar.bz2')): continue
        inspect_archive(path.read_bytes(),'controlled-'+path.name,notices)
    with (materials/'application-source.tar.gz').open('wb') as target:
        run(['git','archive','--format=tar.gz','--prefix=zilcho-subs/',commit],stdout=target)
    inventory_path=evidence/'dependencies/inventory.json'
    python=python_sources(inventory_path,materials/'sources/python',notices)
    validate_onnx_source(native,python)
    debian=debian_sources(inventory_path,materials/'sources/debian')
    if not debian['all_installed_debian_sources_preserved'] or not python['all_python_source_mappings_preserved']: raise ValueError('Source coverage incomplete')
    for record in python['python']:
        if record.get('provided_by') and not (materials/record['provided_by']).is_file(): raise ValueError('Provided source archive missing: '+record['name'])
    interpreter=subprocess.check_output(['docker','run','--rm','--network','none','--entrypoint','python',image,'-c','import platform;print(platform.python_version())'],text=True).strip()
    interpreter_url='https://www.python.org/ftp/python/'+interpreter+'/Python-'+interpreter+'.tar.xz'
    with urlopen(interpreter_url,timeout=120) as response: data=response.read()
    python_dir=materials/'sources/interpreter'; python_dir.mkdir()
    (python_dir/('Python-'+interpreter+'.tar.xz')).write_bytes(data)
    inspect_archive(data,'python-interpreter-'+interpreter,notices)
    (materials/'source-coverage.json').write_text(json.dumps({'debian_source_packages':len(debian['collected']),'python_distributions':len(python['python']),'rust_sources':len(python['rust']),'python_interpreter':interpreter,'python_source_url':interpreter_url,'python_source_sha256':hashlib.sha256(data).hexdigest(),'controlled_native_build':True,'source_commit':commit},indent=2),encoding='utf-8')
    (materials/'BUILDING.md').write_text('Source materials for Zilcho Subs '+version+' at '+commit+'.\n\nExtract application-source.tar.gz and read its Dockerfile, docker/build-native.sh, third_party/native-lock.json and README.md. Controlled native archives/submodule revisions and exact build logs are under sources/native. Debian source packages include DSC files, upstream archives and packaging patches; use dpkg-source and the packaged debian/rules with the recorded build dependencies. Python/Rust archives retain their upstream build files and licenses; Rust sources are mirrored from the included Cargo.lock checksums. The base image is digest-pinned. These materials preserve matching source inputs; no byte-for-byte rebuild guarantee is made. The exact distributable image is separately preserved, so install/publish that archive rather than rebuilding a different candidate.\n',encoding='utf-8')
    # Notices accompany the binary in-image and in the matching source materials.
    shutil.copytree(notices,materials/'notices')
    source_archive=destination/'source-materials.tar.gz'; archive_folder(materials,source_archive)
    source_hash=sha256(source_archive)
    source_url='https://github.com/Zilcho-Labs/zilcho-subs/releases/download/v'+version+'/source-materials.tar.gz'
    delivery={'version':version,'source_commit':commit,'source_archive':'source-materials.tar.gz','source_sha256':source_hash,'proposed_source_url':source_url,'public_source_url_verified':False,'publication_approved':False,'instructions':'Publish the paired source archive and verify anonymous access/hash before distributing this image. Never publish only the image.'}
    overlay=destination/'overlay'
    (overlay/'source-delivery.json').write_text(json.dumps(delivery,indent=2),encoding='utf-8')
    (overlay/'Dockerfile').write_text('FROM '+image+'\nCOPY release-notices /usr/share/zilcho-subs/release-notices\nCOPY source-delivery.json /usr/share/zilcho-subs/source-delivery.json\nLABEL org.opencontainers.image.source="https://github.com/Zilcho-Labs/zilcho-subs" org.opencontainers.image.revision="'+commit+'" org.opencontainers.image.version="'+version+'"\n',encoding='utf-8')
    candidate='zilcho-subs:'+version
    run(['docker','build','--pull=false','-t',candidate,str(overlay)])
    final=image_info(candidate)
    if final['RootFS']['Layers'][:len(original['RootFS']['Layers'])]!=original['RootFS']['Layers']: raise ValueError('Candidate modified the tested runtime layers')
    candidate_smoke(candidate,'zilcho-candidate-check')
    run(['docker','tag',candidate,'zilcho-subs:local'])
    image_archive=destination/'zilcho-subs-image.tar.gz'
    with image_archive.open('wb') as target, gzip.GzipFile(fileobj=target,mode='wb',compresslevel=1,mtime=0) as compressed:
        process=subprocess.Popen(['docker','save',candidate,'zilcho-subs:local'],stdout=subprocess.PIPE)
        try: shutil.copyfileobj(process.stdout,compressed,length=1024*1024)
        finally: process.stdout.close()
        if process.wait()!=0: raise RuntimeError('Image export failed')
    # Verify the actual exported archive restores the same immutable image config.
    run(['docker','image','rm',candidate,'zilcho-subs:local'])
    run(['docker','load','-i',str(image_archive)])
    if image_info(candidate)['Id']!=final['Id']: raise ValueError('Saved image restore identity mismatch')
    identities=inspect_image_archive(image_archive,final['Id'],source_hash,commit)
    archive_folder(evidence,destination/'build-evidence.tar.gz')
    manifest={'version':version,'source_commit':commit,'verified_image_identities':identities,'tested_runtime_image_id':original['Id'],'candidate_image_id':final['Id'],'repo_digests_reported':final.get('RepoDigests',[]),'runtime_layers_unchanged':True,'candidate_auth_smoke_passed':True,'image_archive_preserved':True,'image_restore_verified':True,'matching_source_materials_preserved':True,'publication_approved':False,'public_source_delivery_verified':False,'debian_source_packages':len(debian['collected']),'python_sources':len(python['python']),'rust_sources':len(python['rust']),'files':{p.name:{'sha256':sha256(p),'bytes':p.stat().st_size} for p in (image_archive,source_archive,destination/'build-evidence.tar.gz')}}
    (destination/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (destination/'SOURCE-DELIVERY.md').write_text('# Paired source delivery for '+version+'\n\nThis is a private, preserved candidate, not a public release. Publish source-materials.tar.gz with the image, full notices and SHA256SUMS. Planned source URL: '+source_url+'. Verify anonymous download and its hash before any public image distribution. Hosting must retain the corresponding sources for the applicable distribution obligations; expiring CI artifacts are not a public source host. The repository is currently private; changing visibility or publishing assets/image requires owner approval.\n\nFor personal testing: verify SHA256SUMS, then docker load -i zilcho-subs-image.tar.gz. The archive includes zilcho-subs:local for the editable Unraid template. Set your own password and disposable media/appdata paths. Existing acceptance tests and server-installation approval still apply.\n\nDo not rebuild before publishing: that would create a different image. The embedded /usr/share/zilcho-subs/source-delivery.json pairs this image with the exact source archive SHA256. Docker save/load can alter registry representation while retaining image config/content; record and verify the actual registry manifest digest only after an approved push.\n',encoding='utf-8')
    files=[p for p in destination.iterdir() if p.is_file() and p.name!='SHA256SUMS']
    (destination/'SHA256SUMS').write_text(''.join(sha256(p)+'  '+p.name+'\n' for p in sorted(files)),encoding='utf-8')
    print('Preserved candidate:',candidate,final['Id'],flush=True)
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--evidence',required=True); parser.add_argument('--destination',required=True); parser.add_argument('--image',required=True); parser.add_argument('--commit',required=True); parser.add_argument('--version',required=True)
    args=parser.parse_args(); prepare(args.evidence,args.destination,args.image,args.commit,args.version)
