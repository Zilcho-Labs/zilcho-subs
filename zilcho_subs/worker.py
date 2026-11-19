import os
import sys
import traceback
from .core import Store, JobPaused
from .pipeline import process_subtitle, process_asset


def main():
    store=Store(os.environ.get('ZILCHO_CONFIG','/config')); id=sys.argv[1]
    try:
        job=store.job(id)
        if job['cancel']: raise InterruptedError('Cancelled')
        if job['kind']=='subtitle': process_subtitle(store,job,os.environ.get('ZILCHO_MEDIA','/media'))
        elif job['kind']=='lyrics':
            from .lyrics import process_lyrics
            process_lyrics(store,job,os.environ.get('ZILCHO_MEDIA','/media'))
        elif job['kind']=='text':
            from .text_subtitles import process_text_job
            process_text_job(store,job,os.environ.get('ZILCHO_MEDIA','/media'))
        else: process_asset(store,job)
    except JobPaused:
        store.finish_pause(id)
    except InterruptedError:
        store.update(id,status='cancelled',stage='Cancelled')
    except Exception as e:
        store.update(id,status='failed',stage='Failed',error=str(e)[:600])
        traceback.print_exc()
        print(type(e).__name__+': '+str(e)[:600],file=sys.stderr)

if __name__=='__main__': main()
