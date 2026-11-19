"""CI-only native linkage evidence from the installed Linux image; no media is read."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def command(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=60)
    return {'args':args,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def capture(destination):
    import av
    import av._core
    import ctranslate2
    import ctranslate2._ext
    import numpy
    import onnxruntime
    if not Path('/proc/self/maps').exists(): raise RuntimeError('Run linkage evidence collection inside the Linux container')
    package=Path(av.__file__).resolve().parent
    mapped=set()
    for line in Path('/proc/self/maps').read_text().splitlines():
        fields=line.split(None,5)
        if len(fields)==6 and fields[5].startswith('/') and '.so' in fields[5]: mapped.add(fields[5])
    paths={Path(p) for p in mapped if Path(p).is_file()}
    bundled=sorted(p.resolve() for p in (package.parent/'av.libs').glob('*') if p.is_file())
    paths.update(bundled)
    paths.add(Path(av._core.__file__).resolve())
    paths.add(Path(ctranslate2._ext.__file__).resolve())
    for program in ('ffmpeg','ffprobe'):
        located=shutil.which(program)
        if located: paths.add(Path(located).resolve())
    records=[]
    for path in sorted(paths):
        record={'path':str(path),'sha256':sha256(path),'bytes':path.stat().st_size}
        record['debian_ownership']=command(['dpkg-query','-S',str(path)])
        # Probe trusted binaries installed in the CI image, never uploaded user files.
        record['ldd']=command(['ldd',str(path)])
        records.append(record)
    report={'ctranslate2_version':ctranslate2.__version__,'numpy_version':numpy.__version__,'onnxruntime_version':onnxruntime.__version__,'onnxruntime_build_info':onnxruntime.get_build_info(),'av_version':av.__version__,'av_module':str(package),'av_library_versions':av.library_versions,
        'av_library_meta':getattr(av,'library_meta',{}),'mapped_library_paths':sorted(mapped),
        'av_wheel_library_paths':[str(p) for p in bundled],'files':records,
        'ffmpeg_buildconf':command(['ffmpeg','-buildconf']),
        'scope':'Runtime mappings, hashes, dpkg ownership and ldd from this image. Not a source/license clearance.'}
    Path(destination).write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    return report

if __name__=='__main__': capture(sys.argv[1])
