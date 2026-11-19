"""Integrity checks for release evidence, without network or Docker access."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]

def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tools'/(name+'.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def test_upstream_notice_snapshot_hashes():
    root=ROOT/'third_party/upstream-notices'
    reviews=json.loads((root/'review.json').read_text(encoding='utf-8'))
    assert {r['package'] for r in reviews}=={'ctranslate2','flatbuffers','sentencepiece','tokenizers'}
    for review in reviews:
        assert not review['tree_truncated'] and len(review['commit'])==40
        for file in review['files']:
            path=root/review['package']/review['version']/file['path']
            assert hashlib.sha256(path.read_bytes()).hexdigest()==file['sha256']


@pytest.mark.parametrize('tamper',[False,True])
def test_exact_source_verification(tmp_path,monkeypatch,tamper):
    module=load('fetch_ffmpeg_source')
    version='7:5.1.9-0+deb12u1'; content=b'upstream source fixture'
    sha256=hashlib.sha256(content).hexdigest()
    if tamper: sha256='0'*64
    name='ffmpeg_5.1.9.orig.tar.xz'
    descriptor=('Source: ffmpeg\nVersion: '+version+'\nChecksums-Sha256:\n '+sha256+' '+str(len(content))+' '+name+'\n').encode()
    payloads={hashlib.sha1(content).hexdigest():(name,content),hashlib.sha1(descriptor).hexdigest():('ffmpeg.dsc',descriptor)}
    def fetch(url):
        if url.endswith('/srcfiles'): return json.dumps({'result':[{'hash':key} for key in payloads]}).encode()
        if url.endswith('/info'):
            key=url.split('/')[-2]; filename,data=payloads[key]
            return json.dumps({'result':[{'name':filename,'size':len(data)}]}).encode()
        return payloads[url.split('/')[-1]][1]
    monkeypatch.setattr(module,'fetch',fetch)
    inventory=tmp_path/'inventory.json'
    inventory.write_text(json.dumps({'debian':[{'name':'ffmpeg','source':'ffmpeg','source_version':version}]}),encoding='utf-8')
    if tamper:
        with pytest.raises(ValueError,match='DSC SHA256 mismatch'): module.collect(inventory,tmp_path/'source')
    else:
        result=module.collect(inventory,tmp_path/'source')
        assert result['dsc_sha256_verified'] and not result['distribution_cleared']


def test_image_binding_omits_environment_and_does_not_invent_registry_digest(tmp_path,monkeypatch):
    module=load('bind_evidence'); (tmp_path/'evidence.txt').write_text('evidence',encoding='utf-8')
    image={'Id':'sha256:'+'a'*64,'RepoDigests':[],'RootFS':{'Layers':['sha256:example']},'Config':{'Env':['SECRET=never-export-this']}}
    monkeypatch.setattr(module.subprocess,'check_output',lambda _:json.dumps([image]).encode())
    module.main(tmp_path,'local:test','b'*40)
    identity=(tmp_path/'image-identity.json').read_text(encoding='utf-8')
    assert 'SECRET' not in identity and 'never-export' not in identity
    assert not json.loads(identity)['registry_manifest_digest_available']
    assert not json.loads(identity)['image_archive_preserved']
    for line in (tmp_path/'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        digest,relative=line.split('  ',1)
        assert hashlib.sha256((tmp_path/relative).read_bytes()).hexdigest()==digest
