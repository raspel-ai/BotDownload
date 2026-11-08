"""Bounded format descriptions shared by broker and protected media worker.

Format IDs are opaque lookup keys, never yt-dlp expressions. Estimates describe
output bytes; disk peaks and proof of oversize are deliberately separate.
"""
import math
import re

TARGETS = (480, 720, 1080)


def positive(v):
    return type(v) in (int, float) and math.isfinite(v) and v > 0


def short_side(f):
    w, h = f.get('width'), f.get('height')
    return int(min(w, h)) if positive(w) and positive(h) else None


def valid_code(code):
    return type(code) is str and (code in {'MP3', 'video', 'audio'} or re.fullmatch(r'[1-9][0-9]{0,4}p', code) is not None)


def audio(formats):
    pool = [f for f in formats if type(f.get('format_id')) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', f['format_id']) and f.get('acodec') not in (None, 'none')]
    ru = [f for f in pool if str(f.get('language') or '').lower().split('-')[0] in {'ru', 'rus'}]
    if ru:
        first = ru[0]
        # Audio IDs identify tracks; format_note often describes video quality.
        track = (first.get('language'), first.get('audio_track_id'))
        pool = [f for f in ru if (f.get('language'), f.get('audio_track_id')) == track]
    else:
        original = [f for f in pool if 'original' in str(f.get('format_note') or '').lower() or f.get('language_preference') == 10]
        pool = original or pool
    separate = [f for f in pool if f.get('vcodec') in (None, 'none')]
    pool = separate or pool
    # tbr on combined formats includes video and cannot rank audio quality.
    return min(pool, key=lambda f: (-(f.get('abr') if positive(f.get('abr')) else f.get('tbr', 0) if separate and positive(f.get('tbr')) else 0), str(f.get('format_id', '')))) if pool else None


def descriptor(f):
    return {'id': f['format_id'], 'width': int(f['width']) if positive(f.get('width')) else None,
            'height': int(f['height']) if positive(f.get('height')) else None,
            'vcodec': str(f.get('vcodec') or 'none')[:80], 'acodec': str(f.get('acodec') or 'none')[:80],
            'language': str(f.get('language') or '')[:40], 'track': str(f.get('audio_track_id') or '')[:128],
            'pix_fmt': str(f.get('pix_fmt') or '')[:40], 'ext': str(f.get('ext') or '')[:16]}


def make_plan(code, v, a):
    return {'code': code, 'video': descriptor(v) if v else None, 'audio': descriptor(a),
            'processing': 'mp3-vbr0' if v is None else 'mp4-h264-yuv420p-copy-or-4000k-aac-lc-copy-or-192k'}


def validate_plan(p, code):
    if type(p) is not dict or set(p) != {'code', 'video', 'audio', 'processing'} or p['code'] != code or not valid_code(code) or code in {'video', 'audio'}:
        raise ValueError('invalid_selection')
    if p['processing'] != ('mp3-vbr0' if code == 'MP3' else 'mp4-h264-yuv420p-copy-or-4000k-aac-lc-copy-or-192k'):
        raise ValueError('invalid_processing')
    for name in ('video', 'audio'):
        d = p[name]
        if name == 'video' and code == 'MP3' and d is None:
            continue
        if type(d) is not dict or set(d) != {'id', 'width', 'height', 'vcodec', 'acodec', 'language', 'track', 'pix_fmt', 'ext'}:
            raise ValueError('invalid_stream')
        if type(d['id']) is not str or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', d['id']) is None:
            raise ValueError('invalid_stream_id')
        for k, limit in (('vcodec', 80), ('acodec', 80), ('language', 40), ('track', 128), ('pix_fmt',40), ('ext',16)):
            if type(d[k]) is not str or len(d[k]) > limit or any(ord(c) < 32 for c in d[k]):
                raise ValueError('invalid_stream_text')
        for k in ('width', 'height'):
            if d[k] is not None and (type(d[k]) is not int or not 0 < d[k] < 100000):
                raise ValueError('invalid_dimensions')
    if code == 'MP3' and p['video'] is not None:
        raise ValueError('invalid_audio_selection')
    if p['audio']['acodec'] == 'none' or (code != 'MP3' and (p['video']['vcodec'] == 'none' or short_side(p['video']) != int(code[:-1]))):
        raise ValueError('invalid_stream_role')
    return p


def selection(info, code, plan=None):
    formats = [f for f in info.get('formats', []) if type(f) is dict and type(f.get('format_id')) is str]
    if plan is not None:
        validate_plan(plan, code)
        selected = []
        for d in (plan['video'], plan['audio']):
            if d is None:
                continue
            f = next((f for f in formats if descriptor(f) == d), None)
            if f is None:
                raise ValueError('quality_unavailable')
            if not any(x['format_id'] == f['format_id'] for x in selected):
                selected.append(f)
        return selected
    if code not in {'video', 'audio'}:
        raise ValueError('selection_required')
    a = audio(formats)
    if not a:
        return None
    if code == 'audio':
        return [a]
    videos = [f for f in formats if short_side(f) and f.get('vcodec') not in (None, 'none')]
    if not videos:
        return None
    v = max(videos, key=lambda f: (short_side(f), f.get('tbr') if positive(f.get('tbr')) else 0, f['format_id']))
    return [v] if v['format_id'] == a['format_id'] else [v, a]


def stream_size(f, duration):
    for k in ('filesize', 'filesize_approx'):
        if positive(f.get(k)):
            return int(f[k])
    if positive(duration) and positive(f.get('tbr')):
        return int(duration * f['tbr'] * 1000 / 8)
    return None


def estimate(info, v, a):
    streams = [a] if v is None or v['format_id'] == a['format_id'] else [v, a]
    sizes = [stream_size(f, info.get('duration')) for f in streams]
    known_input = sum(s or 0 for s in sizes)
    source = sum(sizes) if all(sizes) else None
    # AVC baseline/main/extended/high signal 8-bit 4:2:0 even when yt-dlp
    # omits pix_fmt; higher-bit-depth/chroma profiles do not justify direct copy.
    codec = str((v or {}).get('vcodec','')).lower()
    avc_420 = re.fullmatch(r'avc1\.(?:42|4d|58|64)[0-9a-f]{4}', codec) is not None
    pixels = (v or {}).get('pix_fmt')
    copy_pixels = pixels == 'yuv420p' or pixels is None and avc_420
    likely_copy = copy_pixels or pixels is None and codec in {'avc1','h264'}
    # Compatible remux uses stream-specific bytes as an approximation. Neither
    # VBR MP3 nor transcoding has an honest predicted size from these metadata.
    compatible = v is not None and codec.startswith(('avc1','h264')) and likely_copy and a.get('acodec') in {'mp4a.40.2','aac'}
    size = source if compatible else None
    direct = (compatible and len(streams) == 1 and v.get('ext') == 'mp4' and copy_pixels
              and a.get('acodec') == 'mp4a.40.2' and type(v.get('filesize')) is int and v['filesize'] > 0)
    # Direct copy preserves bytes exactly. For remux the source file's container
    # bytes are not a proven lower bound on output bytes, especially at the limit.
    kind = 'known' if direct else 'approximate' if size else 'unknown'
    duration = info.get('duration')
    output_bound = size
    if positive(duration):
        if v is None:
            output_bound = int(duration * 320000 / 8)  # disk planning only, NOT an average
        elif not compatible:
            output_bound = int(duration * (4000000 + 192000) / 8)
    # Disk peaks are estimates of files coexisting at each actual stage. With
    # incomplete input/output data, retain both the threshold and known needs.
    peak_known = max(known_input * (2 if len(streams) > 1 else 1), known_input + (output_bound or 0))
    return {'size': size, 'size_kind': kind, 'peak_bytes': peak_known if source and output_bound else None,
            'known_peak_bytes': peak_known}


def candidates(info):
    formats = [f for f in info.get('formats', []) if type(f) is dict and type(f.get('format_id')) is str]
    a = audio(formats)
    if not a:
        return []
    result = []
    for v in formats:
        side = short_side(v)
        if side and v.get('vcodec') not in (None, 'none'):
            # With combined-only sources, reuse the selected video's equivalent
            # audio instead of downloading another entire video for the same track.
            same_track = all(v.get(k) == a.get(k) for k in ('language','audio_track_id','abr','acodec'))
            separate = a.get('vcodec') in (None,'none')
            chosen_audio = v if not separate and same_track and ('original' in str(v.get('format_note','')).lower()) == ('original' in str(a.get('format_note','')).lower()) else a
            p = make_plan(f'{side}p', v, chosen_audio)
            try:
                validate_plan(p, p['code'])
            except ValueError:
                continue
            result.append({'code': p['code'], 'plan': p, **estimate(info, v, chosen_audio),
                           'rank': v.get('tbr') if positive(v.get('tbr')) else 0})
    p = make_plan('MP3', None, a)
    try:
        validate_plan(p, 'MP3')
    except ValueError:
        return result
    result.append({'code': 'MP3', 'plan': p, **estimate(info, None, a), 'rank': 0})
    return result


def too_large(v, limit):
    return v.get('size_kind') == 'known' and type(v.get('size')) is int and v['size'] > limit


def allocate(variants, limit):
    by_code = {}
    for v in variants:
        if too_large(v, limit):
            continue
        code = v['code']
        # Prefer a compatible copy route, then better encoding; stable ID tie.
        rank = (v.get('size_kind') == 'known', v.get('size') is not None, v.get('rank', 0), v['plan']['video']['id'] if v['plan']['video'] else v['plan']['audio']['id'])
        if code not in by_code or rank > by_code[code][0]:
            by_code[code] = (rank, v)
    available = {int(c[:-1]): v[1] for c, v in by_code.items() if c != 'MP3'}
    fixed = {t: available.pop(t) for t in TARGETS if t in available}
    targets = [t for t in TARGETS if t not in fixed]
    # DP over at most three target slots. Each resolution is visited once, so
    # no permutation explosion even on sources with many encodings.
    dp = {0: (0, ())}
    for h in sorted(available):
        nxt = dict(dp)
        for mask, (cost, pairs) in dp.items():
            for i, t in enumerate(targets):
                if mask & (1 << i):
                    continue
                new = (cost + abs(t - h), tuple(sorted(pairs + ((i, h),))))
                m = mask | (1 << i)
                if m not in nxt or new < nxt[m]:
                    nxt[m] = new
        dp = nxt
    best = min(dp, key=lambda m: (-m.bit_count(), dp[m]))
    chosen = list(fixed.values()) + [available[h] for _, h in dp[best][1]]
    chosen.sort(key=lambda v: int(v['code'][:-1]))
    if 'MP3' in by_code:
        chosen.append(by_code['MP3'][1])
    return chosen


def offered(meta):
    return [{'code': 'video'}, {'code': 'audio'}] if meta.get('analysis', 'unknown') == 'unknown' else meta.get('variants', [])
