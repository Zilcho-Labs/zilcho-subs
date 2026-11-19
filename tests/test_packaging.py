from pathlib import Path
import xml.etree.ElementTree as ET
import re
import os
import subprocess
import sys
from zilcho_subs.core import Store
from zilcho_subs.pipeline import local_environment, model_ready
from zilcho_subs.runner import Runner

ROOT=Path(__file__).resolve().parents[1]

def test_template_and_static_assets():
    tree=ET.parse(ROOT/'unraid/zilcho-subs.xml').getroot()
    configs={e.attrib['Name']:e for e in tree.findall('Config')}
    assert configs['Media Library'].attrib['Target']=='/media' and configs['Media Library'].attrib['Mode']=='rw'
    assert '/downloads' not in {e.attrib['Target'] for e in configs.values()}  # never used by the app
    public='https://raw.githubusercontent.com/Zilcho-Labs/zilcho-subs/main/'
    assert tree.findtext('Repository').startswith('ghcr.io/zilcho-labs/zilcho-subs:')
    assert tree.findtext('TemplateURL')==public+'unraid/zilcho-subs.xml'
    assert tree.findtext('Icon')==public+'zilcho_subs/static/branding/app-logo.png' and (ROOT/'zilcho_subs/static/branding/app-logo.png').is_file()
    profile=ET.parse(ROOT/'ca_profile.xml').getroot()  # Unraid Community Applications needs a non-empty Profile
    assert profile.tag=='CommunityApplications' and len(profile.findtext('Profile').strip())>50
    assert configs['Web UI Password'].attrib['Mask']=='true'
    assert tree.findtext('Privileged')=='false'
    html=(ROOT/'zilcho_subs/static/index.html').read_text(encoding='utf-8')
    js=(ROOT/'zilcho_subs/static/app.js').read_text(encoding='utf-8')
    ids=set(re.findall(r'id="([^"]+)"',html))
    for id in re.findall(r"\$\('#([a-z-]+)'\)",js): assert id in ids,id
    assert 'innerHTML' not in js
    assert (ROOT/'docker/entrypoint.sh').read_bytes().startswith(b'#!/bin/sh'+bytes([10]))
    assert bytes([13,10]) not in (ROOT/'docker/entrypoint.sh').read_bytes()
    assert 'chown -R' not in (ROOT/'docker/entrypoint.sh').read_text()

def test_offline_environment(tmp_path,monkeypatch):
    for key in ('HF_HUB_OFFLINE','ARGOS_MODEL_PROVIDER','ARGOS_CHUNK_TYPE','ARGOS_PACKAGES_DIR'): monkeypatch.delenv(key,raising=False)
    local_environment(tmp_path,threads=2)
    assert os.environ['HF_HUB_OFFLINE']=='1'
    assert os.environ['ARGOS_MODEL_PROVIDER']=='OPENNMT' and os.environ['ARGOS_CHUNK_TYPE']=='NONE'
    assert not model_ready(tmp_path,'medium')

def test_actual_worker_failure_is_recorded(tmp_path):
    store=Store(tmp_path/'config'); id=store.enqueue('model',{'model':'not-a-model'}); store.claim()
    env=os.environ.copy(); env['ZILCHO_CONFIG']=str(store.config)
    result=subprocess.run([sys.executable,'-m','zilcho_subs.worker',id],env=env,cwd=ROOT,capture_output=True,timeout=15)
    assert result.returncode==0
    assert store.job(id)['status']=='failed' and 'Invalid model' in store.job(id)['error']

def test_terminate_actual_worker(tmp_path):
    runner=Runner(Store(tmp_path/'config'),tmp_path/'media')
    p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=os.name!='nt')
    try:
        runner.terminate(p)
        assert p.poll() is not None
    finally:
        if p.poll() is None: p.kill(); p.wait()


def test_language_package_install_is_staged_and_rejects_traversal(tmp_path):
    import json
    import zipfile
    import pytest
    from zilcho_subs.pipeline import install_archive
    store=Store(tmp_path/'config'); archive=tmp_path/'pair.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('../escaped.txt','bad')
    with pytest.raises(ValueError,match='Unsafe'): install_archive(archive,store.config,'en','es','testjob')
    assert not (store.config/'escaped.txt').exists()
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('translate-en_es/metadata.json',json.dumps({'from_code':'en','to_code':'es'}))
        z.writestr('translate-en_es/model/model.bin','synthetic model')
    install_archive(archive,store.config,'en','es','testjob')
    assert (store.config/'packages/translate-en_es/model/model.bin').is_file()
    with pytest.raises(ValueError,match='already installed'): install_archive(archive,store.config,'en','es','testjob')
    assert not list((store.config/'work').iterdir())

def test_credit_links_and_license_collection(tmp_path,monkeypatch):
    import importlib.util
    spec=importlib.util.spec_from_file_location("collect_licenses",ROOT/"tools/collect_licenses.py")
    collect_licenses=importlib.util.module_from_spec(spec); spec.loader.exec_module(collect_licenses)
    import json
    html=(ROOT/'zilcho_subs/static/index.html').read_text(encoding='utf-8')
    for path in re.findall(r'href="(/static/licenses/[^\"]+)"',html):
        assert (ROOT/'zilcho_subs'/path.lstrip('/')).is_file()
    assert 'About &amp; credits' in html
    original=tmp_path/'original-LICENSE'; original.write_text('Copyright Example. Permission granted.',encoding='utf-8')
    class Info(dict):
        def get_all(self,key,default): return default
        def __str__(self): raise AssertionError("Do not reserialize email metadata with multiline headers")
    class Distribution:
        metadata=Info(Name='example-package',License='MIT'); version='1.2.3'
        files=['example.dist-info/LICENSE','example.dist-info/licenses/MIT.txt']
        def locate_file(self,entry): return original
        def read_text(self,name): return "Name: example-package"
    monkeypatch.setattr(collect_licenses.metadata,'distributions',lambda:[Distribution()])
    monkeypatch.setattr(collect_licenses.shutil,'which',lambda _:None)
    output=tmp_path/'bundle'; result=collect_licenses.collect(output)
    package=result['python'][0]
    assert package['version']=='1.2.3' and not package['needs_notice_review']
    assert len(package['notices'])==2
    copied=output/package['notices'][0]['bundled_path']
    assert copied.read_bytes()==original.read_bytes()
    assert json.loads((output/'inventory.json').read_text(encoding='utf-8'))['review_required'] is True


def test_one_version_source_and_changelog():
    from zilcho_subs import __version__
    assert re.fullmatch(r'\d+\.\d+\.\d+',__version__)
    project=(ROOT/'pyproject.toml').read_text(encoding='utf-8')
    assert 'dynamic = ["version"]' in project and 'attr = "zilcho_subs.__version__"' in project
    assert not re.search(r'^version\s*=\s*"',project,re.M)
    workflow=(ROOT/'.github/workflows/test.yml').read_text(encoding='utf-8')
    assert not re.search(r'--version "\d',workflow)
    assert '## '+__version__+' ' in (ROOT/'CHANGELOG.md').read_text(encoding='utf-8')
    sys.path.insert(0,str(ROOT/'tools'))
    from app_version import app_version
    assert app_version()==__version__
