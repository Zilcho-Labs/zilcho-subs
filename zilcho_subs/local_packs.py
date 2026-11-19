"""Argos-format OpenNMT packs, without optional sentence-segmentation runtimes.
Whole-cue tokenization/decoding is adapted from Argos Translate 1.11.0 (MIT),
Copyright (c) 2020 Argos Open Technologies, LLC. See static/licenses/argos-translate.txt.
"""
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request,urlopen

CATALOG='https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json'
CODE=re.compile(r'[a-z]{2,3}(?:_[A-Z]{2})?')


def download(url,destination,limit=2_000_000_000):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password: raise ValueError('Language downloads require a public HTTPS URL')
    size=0
    request=Request(url,headers={"User-Agent":"ArgosTranslate ZilchoSubs/0.1"})
    with urlopen(request,timeout=120) as source, Path(destination).open('wb') as out:
        if urlparse(source.url).scheme!='https': raise ValueError('Insecure download redirect')
        for block in iter(lambda:source.read(1024*1024),b''):
            size+=len(block)
            if size>limit: raise ValueError('Language download is too large')
            out.write(block)
    return Path(destination)


def catalog(config,refresh=False):
    path=Path(config)/'language-index.json'
    if refresh:
        temp=path.with_suffix('.tmp'); download(CATALOG,temp,10_000_000)
        entries=json.loads(temp.read_text(encoding='utf-8')); validated=validate_catalog(entries)
        temp.replace(path); return validated
    if not path.is_file(): raise ValueError('Refresh the language catalog first')
    return validate_catalog(json.loads(path.read_text(encoding='utf-8')))


def validate_catalog(entries):
    if not isinstance(entries,list) or len(entries)>20000: raise ValueError('Invalid language catalog')
    output=[]
    for item in entries:
        if not isinstance(item,dict) or item.get('type','translate')!='translate': continue
        if not all(isinstance(item.get(key),str) and CODE.fullmatch(item[key]) for key in ('from_code','to_code')): continue
        if not isinstance(item.get('links'),list) or not item['links']: continue
        output.append(item)
    return output


class Pack:
    def __init__(self,path):
        self.package_path=Path(path)
        data=json.loads((self.package_path/'metadata.json').read_text(encoding='utf-8'))
        self.from_code=data.get('from_code'); self.to_code=data.get('to_code'); self.type=data.get('type','translate')
        self.target_prefix=data.get('target_prefix',''); self.processor=None; self.bpe=None
    def encode(self,text):
        sp=self.package_path/'sentencepiece.model'
        if sp.is_file():
            if self.processor is None:
                from sentencepiece import SentencePieceProcessor
                self.processor=SentencePieceProcessor(model_file=str(sp))
            return self.processor.encode(text,out_type=str)
        if (self.package_path/'bpe.model').is_file():
            self.load_bpe(); normalizer,tokenizer,_detokenizer,bpe=self.bpe
            return bpe.segment_tokens(' '.join(tokenizer.tokenize(normalizer.normalize(text))).strip().split(' '))
        raise ValueError('This pack has no supported SentencePiece/BPE tokenizer')
    def decode(self,tokens):
        if self.processor is not None: return self.processor.decode_pieces(tokens).replace(chr(9601),' ').replace('_',' ')
        self.load_bpe(); return self.bpe[2].detokenize(' '.join(tokens).replace('@@ ','').split(' '))
    def load_bpe(self):
        if self.bpe is not None: return
        from sacremoses import MosesTokenizer, MosesDetokenizer, MosesPunctNormalizer
        from .vendor.argos_bpe import BPE
        with (self.package_path/'bpe.model').open(encoding='utf-8') as source: bpe=BPE(source)
        self.bpe=(MosesPunctNormalizer(self.from_code),MosesTokenizer(self.from_code),MosesDetokenizer(self.to_code),bpe)


def installed(config):
    output=[]
    for path in sorted((Path(config)/'packages').iterdir()):
        if path.is_symlink() or not path.is_dir() or not (path/'metadata.json').is_file(): continue
        try: output.append(Pack(path))
        except (OSError,ValueError): continue
    return output


def translate_cue(pack,text,translator):
    tokens=pack.encode(text)
    result=translator.translate_batch([tokens],target_prefix=[[pack.target_prefix]] if pack.target_prefix else None,
        replace_unknowns=True,max_batch_size=32,batch_type='tokens',beam_size=4,num_hypotheses=1,length_penalty=0.2,return_scores=True)
    if not result or not result[0].hypotheses: raise ValueError('Local translation returned no result')
    value=pack.decode(result[0].hypotheses[0])
    if pack.target_prefix and value.startswith(pack.target_prefix): value=value[len(pack.target_prefix):]
    return value[1:] if value.startswith(' ') else value
