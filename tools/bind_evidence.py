"""Bind collected CI evidence to an image config digest and source commit, then hash files.
A local image ID can identify a config, manifest or index, depending on the engine.
Only inspection of the preserved archive establishes the exact identity kind; it does not establish public registry publication.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main(folder,image,commit):
    folder=Path(folder)
    data=json.loads(subprocess.check_output(['docker','image','inspect',image]))[0]
    identity={k:data.get(k) for k in ('Id','RepoDigests','Architecture','Os','RootFS')}
    identity.update(source_commit=commit,identity_kind='Docker reported local image ID; engine-dependent config, manifest or index digest',
        registry_manifest_digest_available=bool(data.get('RepoDigests')),
        image_archive_preserved=False,distribution_cleared=False,
        limitations='Rebuilds may differ. Before distribution, preserve the candidate image and bind its actual registry manifest digest plus matching sources. These CI artifacts alone cannot recreate the image.')
    (folder/'image-identity.json').write_text(json.dumps(identity,indent=2),encoding='utf-8')
    lines=[]
    for path in sorted(folder.rglob('*')):
        if path.is_file() and path.name!='SHA256SUMS':
            lines.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.relative_to(folder).as_posix())
    (folder/'SHA256SUMS').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__': main(*sys.argv[1:])
