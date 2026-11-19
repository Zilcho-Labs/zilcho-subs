from types import SimpleNamespace
import json
from pathlib import Path
import pytest
from zilcho_subs import local_packs


def test_whole_cue_matches_argos_prefix_and_beam_settings():
    seen={}
    class Pack:
        target_prefix='[es]'
        def encode(self,text): assert text=='A complete cue. Second sentence.'; return ['A','cue']
        def decode(self,tokens): assert tokens==['translated']; return '[es] Hola'
    class Translator:
        def translate_batch(self,tokens,**kwargs):
            seen.update(kwargs); assert tokens==[['A','cue']]
            return [SimpleNamespace(hypotheses=[['translated']])]
    assert local_packs.translate_cue(Pack(),'A complete cue. Second sentence.',Translator())=='Hola'
    assert seen['target_prefix']==[['[es]']] and seen['beam_size']==4 and seen['length_penalty']==0.2


def test_catalog_excludes_sentence_segmenters_and_invalid_pairs():
    valid={'from_code':'en','to_code':'es','links':['https://example.invalid/model']}
    assert local_packs.validate_catalog([valid,{**valid,'type':'sbd'},{**valid,'to_code':'../../escape'}])==[valid]
    with pytest.raises(ValueError): local_packs.validate_catalog({})


def test_download_rejects_local_and_insecure_sources(tmp_path):
    for url in ('file:///etc/passwd','http://example.invalid/model','https://user:secret@example.invalid/model'):
        with pytest.raises(ValueError): local_packs.download(url,tmp_path/'download')
    assert not list(tmp_path.iterdir())


def test_vendored_bpe_keeps_upstream_attribution():
    path=Path(local_packs.__file__).parent/'vendor/argos_bpe.py'
    text=path.read_text(encoding='utf-8')
    assert 'Rico Sennrich' in text and 'Joerg Tiedemann' in text and 'Permission is hereby granted' in text
