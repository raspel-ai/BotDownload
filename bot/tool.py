"""Worker child running yt-dlp with service-owned options and format selection."""
import json
from pathlib import Path
import sys
import yt_dlp
from .config import load_settings
from .files import task_dir
from .formats import selection
from .isolation import require_isolation
from .urls import validate_url

class Quiet:
    def debug(self,*a):pass
    def warning(self,*a):pass
    def error(self,*a):pass

def options():
    return {'quiet':True,'no_warnings':True,'logger':Quiet(),'noplaylist':True,'extract_flat':False,
        'allowed_extractors':['youtube','tiktok','instagram'],'cachedir':False,
        'socket_timeout':15,'retries':0,'extractor_retries':0,'fragment_retries':0,
        'js_runtimes':{'deno':{'path':str(Path(sys.executable).parent/'deno')}},
        'remote_components':set(), 'ffmpeg_location':'/usr/bin', 'overwrites':False}

def formatter(code):
    def choose(ctx):
        selected=selection(ctx,code)
        if not selected:return
        if len(selected)==1:
            yield selected[0]
        else:
            yield {'format_id':'+'.join(f['format_id'] for f in selected),'ext':'mkv',
                'requested_formats':selected,'protocol':'+'.join(f['protocol'] for f in selected)}
    return choose

def main():
    # Python API never loads CLI config; explicitly disable dynamic plugin loading.
    from yt_dlp.globals import plugin_dirs
    plugin_dirs.value=[]
    if len(sys.argv)!=5 or sys.argv[1] not in {'inspect','download'}:
        raise SystemExit(2)
    action,eid,code,url=sys.argv[1],int(sys.argv[2]),sys.argv[3],sys.argv[4]
    config=load_settings(secrets=False).data
    require_isolation(config)
    url=validate_url(url).url
    opts=options()
    if action=='download':
        if code not in {'480p','720p','1080p','MP3','video','audio'}:raise SystemExit(2)
        opts.update(format=formatter(code),outtmpl=str(task_dir(eid)/'source.%(ext)s'),merge_output_format='mkv',
            postprocessor_hooks=[lambda event:print('BOTDOWNLOAD_PROCESSING',flush=True) if event.get('status')=='started' else None])
    else:
        opts.update(skip_download=True)
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info=y.extract_info(url,download=action=='download')
        if action=='inspect':
            # The parent sanitizes and bounds the fields crossing IPC.
            print(json.dumps(info,ensure_ascii=False,default=str))
    except Exception as exc:
        text=str(exc).lower()
        if 'no space left' in text:reason='disk_full'
        elif any(x in text for x in ('not available','private video','sign in','login required','unsupported url','video unavailable','copyright','requested format')):reason='source_unavailable'
        else:reason='tool_error'
        print(reason,file=sys.stderr)
        raise SystemExit(1) from None

if __name__=='__main__':main()
