"""Read saved Docker/OCI archives without executing or extracting them."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile


def inspect_image_archive(path,reported_id,source_hash,commit):
    documents={}; delivery=None
    with tarfile.open(path,mode='r|gz') as archive:
        for item in archive:
            if not item.isfile() or item.size>2_000_000: continue
            data=archive.extractfile(item).read()
            digest='sha256:'+hashlib.sha256(data).hexdigest()
            try: documents[digest]=json.loads(data)
            except (ValueError,UnicodeDecodeError): pass
            try:
                with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as layer:
                    for member in layer:
                        if member.isfile() and member.name.lstrip('./')=='usr/share/zilcho-subs/source-delivery.json':
                            delivery=json.load(layer.extractfile(member))
            except tarfile.ReadError: pass
    if reported_id not in documents: raise ValueError('Reported image identity not preserved in archive')
    document=documents[reported_id]
    if 'manifests' in document:
        kind='OCI image index digest'
        platform=next(d for d in document['manifests'] if d.get('platform',{}).get('os')=='linux' and d.get('platform',{}).get('architecture')=='amd64')
        manifest_id=platform['digest']; manifest=documents[manifest_id]
        config_id=manifest['config']['digest']
    elif 'config' in document and 'digest' in document['config']:
        kind='OCI/Docker image manifest digest';manifest_id=reported_id;config_id=document['config']['digest']
    else:
        kind='Docker image configuration digest';manifest_id=None;config_id=reported_id
    config=documents[config_id]
    if config['config']['Labels']['org.opencontainers.image.revision']!=commit: raise ValueError('Saved image revision mismatch')
    if not delivery or delivery['source_sha256']!=source_hash or delivery['source_commit']!=commit: raise ValueError('Embedded source delivery does not match paired source archive')
    sensitive={'ZILCHO_PASSWORD','CLOUDFLARE_API_TOKEN','GITHUB_TOKEN'}
    if any(e.split('=',1)[0] in sensitive for e in config['config'].get('Env',[])): raise ValueError('Runtime secret environment key found in saved image')
    return {'docker_reported_image_id':reported_id,'reported_identity_kind':kind,'platform_manifest_digest':manifest_id,'image_configuration_digest':config_id,'embedded_source_binding_verified':True,'source_commit':commit,'registry_publication_verified':False}


def verify(folder):
    folder=Path(folder)
    def sha(path):
        result=hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''):result.update(block)
        return result.hexdigest()
    for line in (folder/'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        expected,name=line.split('  ',1)
        if Path(name).name!=name or sha(folder/name)!=expected: raise ValueError('Release checksum mismatch: '+name)
    manifest=json.loads((folder/'release-manifest.json').read_text(encoding='utf-8'))
    return inspect_image_archive(folder/'zilcho-subs-image.tar.gz',manifest['candidate_image_id'],manifest['files']['source-materials.tar.gz']['sha256'],manifest['source_commit'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder');args=parser.parse_args()
    print(json.dumps(verify(args.folder),indent=2))
