"""Collect installed-image attribution evidence. This is not a license clearance tool.
Run only during the image build; never inspect media, environment secrets or app data.
"""
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys


def collect(destination):
    destination=Path(destination); destination.mkdir(parents=True,exist_ok=True)
    packages=[]
    for dist in sorted(metadata.distributions(),key=lambda d:d.metadata.get('Name','').lower()):
        name=dist.metadata.get('Name','unknown'); version=dist.version
        folder=destination/'python'/name; folder.mkdir(parents=True,exist_ok=True)
        notices=[]
        for entry in dist.files or []:
            path=PurePosixPath(str(entry))
            is_notice=any(x in path.name.lower() for x in ('license','copying','notice','copyright'))
            in_license_folder=any(part.lower() in ('license','licenses') for part in path.parts[:-1])
            if not (is_notice or in_license_folder): continue
            original=Path(dist.locate_file(entry))
            if not original.is_file(): continue
            # Numbering avoids collisions and prevents distribution paths escaping our bundle.
            target=folder/(str(len(notices))+'-'+path.name)
            shutil.copyfile(original,target)
            notices.append({'installed_path':str(entry),'bundled_path':target.relative_to(destination).as_posix()})
        (folder/'METADATA.txt').write_text(dist.read_text('METADATA') or dist.read_text('PKG-INFO') or '',encoding='utf-8')
        packages.append({'name':name,'version':version,'license_expression':dist.metadata.get('License-Expression'),
            'declared_license':dist.metadata.get('License'),'project_urls':dist.metadata.get_all('Project-URL',[]),
            'notices':notices,'needs_notice_review':not notices})
    result={'python':packages,'debian':[],'review_required':True,
        'scope':'Installed metadata and available notices only. Not an SBOM standard or a complete source/compliance bundle.'}
    if shutil.which('dpkg-query'):
        query=subprocess.run(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'],capture_output=True,text=True,check=True)
        for row in query.stdout.splitlines():
            name,version,source,source_version=row.split('\t')
            copyright=Path('/usr/share/doc')/name.split(':')[0]/'copyright'
            target=destination/'debian'/name.replace(':','_')/'copyright'; copied=copyright.is_file()
            if copied:
                target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(copyright,target)
            result['debian'].append({'name':name,'version':version,'source':source,'source_version':source_version,'copyright_bundled':copied,'copyright_path':target.relative_to(destination).as_posix() if copied else None})
        common=Path('/usr/share/common-licenses')
        if common.is_dir(): shutil.copytree(common,destination/'common-licenses',dirs_exist_ok=True)
    for program,args in [('ffmpeg',['-version']),('ffmpeg',['-L']),('ffprobe',['-version'])]:
        if shutil.which(program):
            output=subprocess.run([program,*args],capture_output=True,text=True,check=True)
            (destination/(program+'-'+args[0].lstrip('-')+'.txt')).write_text(output.stdout+output.stderr,encoding='utf-8')
    (destination/'inventory.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    return result

if __name__=='__main__': collect(sys.argv[1])
