"""Release gates reject corrupt or incomplete provenance without Docker/network."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import fetch_debian_sources as deb
import fetch_python_sources as py
import prepare_release as release


def tar(files):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as archive:
        for name,data in files.items():
            entry=tarfile.TarInfo(name);entry.size=len(data);archive.addfile(entry,io.BytesIO(data))
    return output.getvalue()


def test_debian_descriptor_and_download_integrity(tmp_path,monkeypatch):
    upstream=b'exact upstream bytes'
    dsc=('Source: example\nVersion: 1.0-1\nChecksums-Sha256:\n '+hashlib.sha256(upstream).hexdigest()+' '+str(len(upstream))+' example.orig.tar.xz\n').encode()
    files={'example.dsc':dsc,'example.orig.tar.xz':upstream}
    ids={hashlib.sha1(data).hexdigest():(name,data) for name,data in files.items()}
    def fetch(url):
        if url.endswith('/srcfiles'): return json.dumps({'package':'example','version':'1.0-1','result':[{'hash':k} for k in ids]}).encode()
        if url.endswith('/info'):
            name,data=ids[url.split('/')[-2]]
            return json.dumps({'result':[{'name':name,'size':len(data)}]}).encode()
        return ids[url.rsplit('/',1)[1]][1]
    monkeypatch.setattr(deb,'fetch',fetch)
    assert deb.collect_package('example','1.0-1',tmp_path)['dsc_sha256_verified']
    broken=dict(ids)
    original=fetch
    def corrupt(url): return b'corrupt' if '/file/' in url and not url.endswith('/info') else original(url)
    monkeypatch.setattr(deb,'fetch',corrupt)
    with pytest.raises(ValueError,match='download mismatch'): deb.collect_package('example','1.0-1',tmp_path/'fresh')


@pytest.mark.parametrize('source,version',[('../escape','1'),('good','../escape'),('good','..')])
def test_debian_unsafe_identity_rejected(tmp_path,source,version):
    with pytest.raises(ValueError): deb.collect_package(source,version,tmp_path)
    assert not list(tmp_path.iterdir())


def test_python_sdist_hash_is_enforced(tmp_path,monkeypatch):
    inv=tmp_path/'inventory.json';inv.write_text(json.dumps({'python':[{'name':'example','version':'1'}]}))
    def fetch(url):
        if url.endswith('/json'): return json.dumps({'urls':[{'packagetype':'sdist','url':'https://example.invalid/source','filename':'example.tar.gz','digests':{'sha256':'0'*64}}]}).encode()
        return b'wrong bytes'
    monkeypatch.setattr(py,'fetch',fetch)
    with pytest.raises(ValueError,match='hash mismatch'): py.collect(inv,tmp_path/'sources',tmp_path/'notices')


def test_archive_notices_never_extract_paths(tmp_path):
    data=tar({'../../LICENSE':b'MIT copyright retained','pkg/Cargo.lock':b'version = 3\n'})
    assert py.inspect_archive(data,'package',tmp_path/'notices')==[{'version':3}]
    files=list((tmp_path/'notices/package').iterdir())
    assert len(files)==1 and files[0].read_bytes()==b'MIT copyright retained'
    assert not (tmp_path/'LICENSE').exists()
    zipped=io.BytesIO()
    with zipfile.ZipFile(zipped,'w') as archive: archive.writestr('pkg/NOTICE','Apache notice')
    py.inspect_archive(zipped.getvalue(),'zip',tmp_path/'notices')
    assert next((tmp_path/'notices/zip').iterdir()).read_text()=='Apache notice'


def test_unmapped_python_package_fails_closed(tmp_path,monkeypatch):
    inv=tmp_path/'inventory.json';inv.write_text(json.dumps({'python':[{'name':'unknown','version':'1'}]}))
    monkeypatch.setattr(py,'fetch',lambda url:b'{"urls":[]}')
    with pytest.raises(ValueError,match='No verified source mapping'): py.collect(inv,tmp_path/'sources',tmp_path/'notices')


def test_candidate_requires_controlled_native_linkage():
    paths=['/opt/zilcho-native/lib/libavcodec.so.62','/opt/zilcho-native/lib/libctranslate2.so.4','/usr/lib/openblas/libopenblas.so']
    report={'files':[{'path':p} for p in paths]}
    release.validate_native(report)
    for vendor in ('av.libs','numpy.libs','ctranslate2.libs'):
        with pytest.raises(ValueError,match='Opaque'):
            release.validate_native({'files':report['files']+[{'path':'/site-packages/'+vendor+'/lib.so'}]})
    with pytest.raises(ValueError,match='OpenBLAS'): release.validate_native({'files':report['files'][:2]})


def test_candidate_never_overwrites_existing_work(tmp_path):
    target=tmp_path/'existing';target.mkdir();(target/'keep').write_text('unchanged')
    with pytest.raises(ValueError,match='overwrite'): release.prepare(tmp_path,target,'example','a'*40,'0.1.0-rc.1')
    assert (target/'keep').read_text()=='unchanged'


def test_source_fallback_resolves_exact_tag_commit(tmp_path,monkeypatch):
    inv=tmp_path/'inventory.json'; inv.write_text(json.dumps({'python':[{'name':'faster-whisper','version':'1.2.1'}]}))
    archive=tar({'source/LICENSE':b'MIT'})
    seen=[]
    def fetch(url):
        seen.append(url)
        return b'{"urls":[]}' if url.endswith('/json') else archive
    monkeypatch.setattr(py,'fetch',fetch)
    monkeypatch.setattr(py.subprocess,'check_output',lambda *a,**kw:'a'*40+'\trefs/tags/v1.2.1\n')
    report=py.collect(inv,tmp_path/'sources',tmp_path/'notices')
    assert report['python'][0]['upstream_commit']=='a'*40
    assert seen[-1].endswith('/'+'a'*40)
    assert report['all_python_source_mappings_preserved']


def test_controlled_source_version_cannot_be_silently_substituted(tmp_path):
    inv=tmp_path/'inventory.json';inv.write_text(json.dumps({'python':[{'name':'av','version':'999'}]}))
    with pytest.raises(ValueError,match='Controlled source version mismatch'):
        py.collect(inv,tmp_path/'sources',tmp_path/'notices')


def test_onnx_binary_source_revision_must_match():
    sources={'python':[{'name':'onnxruntime','upstream_commit':'abcdef0123'+'a'*30}]}
    release.validate_onnx_source({'onnxruntime_build_info':'ORT Build Info: git-commit-id=abcdef0123, build type=Release'},sources)
    for info in ('','ORT Build Info: git-commit-id=badbadbad'):
        with pytest.raises(ValueError,match='binary commit'):
            release.validate_onnx_source({'onnxruntime_build_info':info},sources)


def test_exported_sources_must_match_the_actual_tested_native_build(tmp_path):
    original=tmp_path/'native-build'; original.mkdir()
    exported=tmp_path/'sources/native/build'; exported.mkdir(parents=True)
    (original/'native-wheels-SHA256SUMS').write_text('exact wheel hashes')
    (exported/'native-wheels-SHA256SUMS').write_text('exact wheel hashes')
    release.validate_build_sources(tmp_path)
    (exported/'native-wheels-SHA256SUMS').write_text('different rebuild')
    with pytest.raises(ValueError,match='do not match'): release.validate_build_sources(tmp_path)


@pytest.mark.parametrize('kind',['config','manifest','index'])
def test_saved_image_identity_types_and_embedded_source_pair(tmp_path,kind):
    from verify_release import inspect_image_archive
    commit='a'*40;source_hash='b'*64
    config=json.dumps({'config':{'Labels':{'org.opencontainers.image.revision':commit},'Env':['PATH=/usr/bin']}}).encode()
    digest=lambda data:'sha256:'+hashlib.sha256(data).hexdigest()
    config_id=digest(config)
    manifest=json.dumps({'config':{'digest':config_id}}).encode();manifest_id=digest(manifest)
    index=json.dumps({'manifests':[{'digest':manifest_id,'platform':{'os':'linux','architecture':'amd64'}}]}).encode();index_id=digest(index)
    delivery=tar({'usr/share/zilcho-subs/source-delivery.json':json.dumps({'source_sha256':source_hash,'source_commit':commit}).encode()})
    blobs={config_id:config,manifest_id:manifest,index_id:index,digest(delivery):delivery}
    path=tmp_path/'image.tar.gz';path.write_bytes(tar({'blobs/sha256/'+key.split(':')[1]:data for key,data in blobs.items()}))
    reported={'config':config_id,'manifest':manifest_id,'index':index_id}[kind]
    identity=inspect_image_archive(path,reported,source_hash,commit)
    assert identity['image_configuration_digest']==config_id
    assert identity['embedded_source_binding_verified']
    if kind=='index': assert identity['reported_identity_kind']=='OCI image index digest'
    with pytest.raises(ValueError,match='source delivery'):inspect_image_archive(path,reported,'c'*64,commit)


def git_repo_tarball(tmp_path):
    """A real git repo (exec bit, subfolder, submodule) and its exported tarball."""
    import subprocess
    repo=tmp_path/'repo'; repo.mkdir()
    git=lambda *a,**kw:subprocess.run(['git','-c','core.autocrlf=false','-c','core.eol=lf',*a],cwd=repo,check=True,capture_output=True,**kw)
    git('init','-q')
    (repo/'README.md').write_bytes(b'hello\n'); (repo/'cmake').mkdir(); (repo/'cmake'/'deps.txt').write_bytes(b'a;b;c\n')
    (repo/'run.sh').write_bytes(b'#!/bin/sh\necho hi\n')
    git('add','.'); git('update-index','--chmod=+x','run.sh')
    git('update-index','--add','--cacheinfo','160000,'+'c'*40+',cmake/external/onnx')
    git('-c','user.name=t','-c','user.email=t@example.invalid','commit','-qm','x')
    tree=git('rev-parse','HEAD^{tree}',text=True).stdout.strip()
    commit=git('rev-parse','HEAD',text=True).stdout.strip()
    data=git('archive','--format=tar.gz','--prefix=project/','HEAD').stdout
    return data,tree,commit


def test_git_tree_hash_matches_git_including_submodules(tmp_path):
    data,tree,_commit=git_repo_tarball(tmp_path)
    assert py.git_tree_id(data,{'cmake/external/onnx':'c'*40})==tree
    assert py.git_tree_id(data)!=tree  # without the pinned submodule it can't match
    changed=tar({'project/README.md':b'hello!\n'})
    assert py.git_tree_id(changed)!=tree


class Response:
    def __init__(self,data): self.data=data
    def read(self): return self.data


def test_archived_source_used_when_github_is_gone_and_must_match_its_tree(tmp_path,monkeypatch):
    import subprocess
    data,tree,commit=git_repo_tarball(tmp_path)
    monkeypatch.setitem(py.ARCHIVED,('faster-whisper','1.2.1'),{'commit':commit,'tree':tree,'submodules':{'cmake/external/onnx':'c'*40}})
    inv=tmp_path/'inventory.json'; inv.write_text(json.dumps({'python':[{'name':'faster-whisper','version':'1.2.1'}]}))
    def gone(*a,**kw): raise subprocess.CalledProcessError(128,a[0])
    served={'archive':data}
    def fetch(url):
        if url.endswith('/json'): return b'{"urls":[]}'
        if '/revision/' in url: return json.dumps({'directory':tree}).encode()
        if url.endswith('/raw/'): return served['archive']
        return json.dumps({'status':'done','fetch_url':url+'raw/'}).encode()
    monkeypatch.setattr(py.subprocess,'check_output',gone)
    monkeypatch.setattr(py,'fetch',fetch)
    monkeypatch.setattr(py,'urlopen',lambda request,timeout:Response(json.dumps({'status':'done','fetch_url':request.full_url+'raw/'}).encode()))
    report=py.collect(inv,tmp_path/'sources',tmp_path/'notices')
    record=report['python'][0]
    assert record['upstream_commit']==commit and record['archived_source']['swhid']=='swh:1:dir:'+tree
    assert record['url'].startswith(py.SWH) and record['sha256']==hashlib.sha256(data).hexdigest()
    # A download that differs by even one file is refused.
    served['archive']=tar({'project/README.md':b'tampered\n'})
    with pytest.raises(ValueError,match='pinned git tree'):
        py.collect(inv,tmp_path/'again',tmp_path/'notices2')


def test_no_archive_mapping_means_a_missing_tag_still_fails(tmp_path,monkeypatch):
    import subprocess
    inv=tmp_path/'inventory.json'; inv.write_text(json.dumps({'python':[{'name':'flatbuffers','version':'9.9.9'}]}))
    monkeypatch.setattr(py,'fetch',lambda url:b'{"urls":[]}')
    monkeypatch.setattr(py.subprocess,'check_output',lambda *a,**kw:(_ for _ in ()).throw(subprocess.CalledProcessError(128,'git')))
    with pytest.raises(ValueError,match='Missing upstream source tag'):
        py.collect(inv,tmp_path/'sources',tmp_path/'notices')
