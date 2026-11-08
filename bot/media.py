"""Actual yt-dlp/FFmpeg invocation, imported only by the protected worker."""
import json
import hashlib
import math
import os
import sys
import asyncio
import time
from .files import task_dir, ready_path, checked, clean_task
from .isolation import require_isolation
from .processes import run, ToolError


from .formats import positive as number, short_side as dimensions, audio as audio_format, candidates, validate_plan

def safe_text(v, limit):
    return ''.join(c for c in v if c >= ' ' and c != '\x7f')[:limit] if type(v) is str else ''

def display_dimensions(stream):
    """Display orientation can live in MP4's matrix instead of encoded dimensions."""
    rotation = next((s['rotation'] for s in stream.get('side_data_list', []) if 'rotation' in s),
                    stream.get('tags', {}).get('rotate', 0))
    try:
        angle = float(rotation) % 360
    except (TypeError, ValueError):
        angle = 0
    w, h = stream.get('width'), stream.get('height')
    return (h, w) if angle in {90, 270} else (w, h)

def metadata(info):
    if type(info) is not dict or info.get('_type') in {'playlist', 'multi_video'}:
        raise ToolError('unsupported_content', True)
    formats = info.get('formats') if type(info.get('formats')) is list else []
    info = info | {'formats':formats}
    variants = candidates(info)
    a = audio_format([f for f in formats if type(f) is dict])
    videos = [f for f in formats if type(f) is dict and f.get('vcodec') not in (None,'none')]
    unknown_quality = bool(videos) and not any(dimensions(f) for f in videos)
    known_formats = bool(variants) or any(dimensions(f) for f in videos)
    return {'title': safe_text(info.get('title'), 200), 'uploader': safe_text(info.get('uploader') or info.get('channel'), 100),
        'duration': info.get('duration') if number(info.get('duration')) else None,
        'upload_date': safe_text(info.get('upload_date'), 8), 'view_count': info.get('view_count') if type(info.get('view_count')) is int and info['view_count'] >= 0 else None,
        'thumbnail': safe_text(info.get('thumbnail'), 2048), 'language': safe_text((a or {}).get('language') or info.get('language'), 40),
        'analysis': 'known' if known_formats and not unknown_quality else 'unknown', 'variants': variants}

class MediaTools:
    def __init__(self, config):
        self.config = config

    async def inspect(self, task_id, payload):
        require_isolation(self.config)
        # Remote code/plugin downloads are intentionally disabled; EJS is installed by the administrator.
        out = await run([sys.executable, '-m', 'bot.tool', 'inspect', str(task_id), 'video', payload['url']],
            timeout=self.config['timeouts']['analysis_timeout_seconds'], output_limit=16*1024*1024)
        return metadata(json.loads(out))

    async def probe(self, path):
        raw = await run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams', '-show_format', '-of', 'json', str(path)],
                        timeout=self.config['timeouts']['ffmpeg_timeout_seconds'])
        info = json.loads(raw)
        streams = info.get('streams', [])
        duration = float(info.get('format', {}).get('duration', 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ToolError('invalid_media', True)
        return streams, duration

    async def download(self, task_id, payload, stage):
        require_isolation(self.config)
        code = payload['format']
        plan = payload.get('selection')
        if code not in {'video', 'audio'}:
            validate_plan(plan, code)
        audio = code in {'audio', 'MP3'}
        target = ready_path(task_id, audio, self.config['storage']['files_dir'])
        if target.exists() or target.is_symlink():
            raise ToolError('existing_result_requires_reconciliation', True)
        root = task_dir(task_id)
        processing_started=None
        def progress(data):
            nonlocal processing_started
            if b'BOTDOWNLOAD_PROCESSING' in data and processing_started is None:
                processing_started=time.monotonic()
                stage('processing')
        try:
            await run([sys.executable, '-m', 'bot.tool', 'download', str(task_id), code] + ([json.dumps(plan, separators=(',', ':'))] if plan else []) + [payload['url']],
                timeout=self.config['timeouts']['download_timeout_seconds'],output_limit=1024*1024,progress=progress,processing_timeout=self.config['timeouts']['ffmpeg_timeout_seconds'])
            sources = [p for p in root.iterdir() if p.is_file() and not p.is_symlink() and not p.name.endswith(('.part', '.ytdl', '.json'))]
            if len(sources) != 1:
                raise ToolError('invalid_download', True)
            source = checked(sources[0], root)
            remaining=self.config['timeouts']['ffmpeg_timeout_seconds']-(time.monotonic()-processing_started if processing_started else 0)
            async with asyncio.timeout(max(0,remaining)):
                streams, duration = await self.probe(source)
                max_duration = self.config['limits']['max_audio_duration_seconds' if audio else 'max_video_duration_seconds']
                if duration > max_duration:
                    raise ToolError('duration_exceeded', True)
                limit = self.config['telegram']['max_upload_size_mb'][self.config['telegram']['api_mode']] * 1024*1024
                output = root / ('ready.mp3' if audio else 'ready.mp4')
                videos = [s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
                audios = [s for s in streams if s.get('codec_type') == 'audio']
                if len(videos) > 1:
                    raise ToolError('quality_unavailable', True)
                audio_count = len(audios)
                if audios:
                    selected=next((s for s in audios if str(s.get('tags',{}).get('language','')).lower() in {'ru','rus'}),None)
                    selected=selected or next((s for s in audios if s.get('disposition',{}).get('default')),audios[0])
                    audios=[selected]
                if not audios or (not audio and not videos):
                    raise ToolError('missing_stream', True)
                if not audio and code.endswith('p') and dimensions(videos[0]) != int(code[:-1]):
                    raise ToolError('quality_unavailable', True)
                if plan and not audio and (plan['video']['width'],plan['video']['height']) not in {(videos[0].get('width'),videos[0].get('height')),display_dimensions(videos[0])}:
                    raise ToolError('quality_unavailable', True)
                stage('conversion' if audio else 'processing')
                ff = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n', '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map_metadata', '-1']
                if audio:
                    ff += ['-map', f"0:{audios[0]['index']}", '-vn', '-c:a', 'libmp3lame', '-q:a', '0']
                else:
                    compatible = videos[0].get('codec_name') == 'h264' and videos[0].get('pix_fmt') == 'yuv420p'
                    ff += ['-map', f"0:{videos[0]['index']}", '-map', f"0:{audios[0]['index']}", '-c:v', 'copy' if compatible else 'libx264']
                    if not compatible:
                        ff += ['-pix_fmt', 'yuv420p', '-preset', 'veryfast', '-b:v', '4000k']
                    ff += ['-c:a', 'copy' if audios[0].get('codec_name') == 'aac' and audios[0].get('profile') == 'LC' else 'aac', '-b:a', '192k', '-movflags', '+faststart']
                ff += [str(output)]
                # A single already-compatible MP4 can be copied byte-for-byte.
                direct = (not audio and compatible and audios[0].get('codec_name') == 'aac'
                          and audios[0].get('profile') == 'LC' and source.suffix == '.mp4' and audio_count == 1)
                if direct:
                    with source.open('rb') as src, output.open('xb') as dst:
                        while chunk := src.read(1024*1024):
                            dst.write(chunk)
                            await asyncio.sleep(0)  # cancellation closes files before cleanup
                else:
                    await run(ff, timeout=self.config['timeouts']['ffmpeg_timeout_seconds'])
                checked(output, root, limit)
                final_streams, final_duration = await self.probe(output)
                final_videos = [s for s in final_streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
                if not audio and (len(final_videos) != 1 or dimensions(final_videos[0]) != dimensions(videos[0])):
                    raise ToolError('quality_unavailable', True)
                if not audio and display_dimensions(final_videos[0]) != display_dimensions(videos[0]):
                    raise ToolError('quality_unavailable', True)
                if final_duration > max_duration:
                    raise ToolError('duration_exceeded', True)
                if audio and not any(s.get('codec_name') == 'mp3' for s in final_streams):
                    raise ToolError('invalid_mp3', True)
                if not audio and not (any(s.get('codec_name') == 'h264' and s.get('pix_fmt') == 'yuv420p' for s in final_streams) and any(s.get('codec_name') == 'aac' and s.get('profile') == 'LC' for s in final_streams)):
                    raise ToolError('invalid_mp4', True)
                # Decode entire output to detect corrupt/truncated media, still under worker timeout.
                await run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe', '-i', str(output), '-f', 'null', '-'], timeout=self.config['timeouts']['ffmpeg_timeout_seconds'])
                os.replace(output, target)
                os.chmod(target, 0o600)
                with target.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                result = {'filename': target.name, 'size': target.stat().st_size, 'duration': final_duration, 'format': 'mp3' if audio else 'mp4', 'validated': True, 'sha256': digest, 'resolution': None if audio else dimensions(final_videos[0]), 'selection': plan}
                manifest = target.with_suffix('.json')
                with manifest.open('x') as stream:
                    json.dump(result, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(manifest, 0o600)
                return result
        except BaseException:
            target.unlink(missing_ok=True)
            target.with_suffix('.json').unlink(missing_ok=True)
            raise
        finally:
            clean_task(task_id)
