"""One deterministic selection policy for inspection and download."""
import math

def positive(v):
    return type(v) in (int,float) and math.isfinite(v) and v>0

def short_side(f):
    w,h=f.get('width'),f.get('height')
    return int(min(w,h)) if positive(w) and positive(h) else None

def audio(formats):
    pool=[f for f in formats if f.get('acodec') not in (None,'none')]
    ru=[f for f in pool if str(f.get('language') or '').lower().split('-')[0]=='ru']
    if ru:
        # First available Russian track; choose best encoding of that language/track.
        first=ru[0]
        track=(first.get('language'),first.get('format_note'))
        pool=[f for f in ru if (f.get('language'),f.get('format_note'))==track]
    else:
        original=[f for f in pool if 'original' in str(f.get('format_note') or '').lower() or f.get('language_preference')==10]
        pool=original or pool
    return max(pool,key=lambda f:f.get('abr') or f.get('tbr') or 0) if pool else None

def selection(info,code):
    formats=[f for f in info.get('formats',[]) if type(f) is dict and type(f.get('format_id')) is str]
    a=audio(formats)
    if not a:return None
    if code in {'MP3','audio'}:return [a]
    height=int(code[:-1]) if code.endswith('p') else None
    videos=[f for f in formats if f.get('vcodec') not in (None,'none') and (height is None or short_side(f)==height)]
    if not videos:return None
    v=max(videos,key=lambda f:(short_side(f) or 0,f.get('tbr') or 0))
    if v['format_id']==a['format_id']:return [v]
    if v.get('acodec') not in (None,'none') and a.get('vcodec') not in (None,'none'):
        # A combined stream with the selected audio is preferable to duplicate video.
        compatible=[f for f in videos if f['format_id']==a['format_id']]
        if compatible:return [compatible[0]]
    return [v|{'acodec':'none'},a|{'vcodec':'none'}]

def stream_size(f,duration):
    for k in ('filesize','filesize_approx'):
        if positive(f.get(k)):return int(f[k])
    if positive(duration) and positive(f.get('tbr')):return int(duration*f['tbr']*1000/8)
    return None

def estimate(info,code):
    selected=selection(info,code)
    if not selected:return None
    sizes=[stream_size(f,info.get('duration')) for f in selected]
    if not all(sizes):return {'size':None,'peak_bytes':None}
    source=sum(sizes)
    duration=info.get('duration')
    if not positive(duration):return {'size':None,'peak_bytes':None}
    if code in {'MP3','audio'}:
        output=int(duration*320000/8) # libmp3lame VBR max 320 kbit/s, not a quality claim
    else:
        video=selected[0]
        compatible=str(video.get('vcodec','')).startswith(('avc1','h264'))
        # Transcoding uses the service's fixed 4 Mbit/s target plus AAC 192 kbit/s.
        output=max(source,int(duration*(4000000+192000)/8)) if compatible else int(duration*(4000000+192000)/8)
    merge=source+source if len(selected)>1 else source
    # Actual stages: downloaded input(s)+merged copy, then merged input+converted output.
    return {'size':output,'peak_bytes':max(merge,source+output)}
