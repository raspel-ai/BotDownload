"""Actual yt-dlp/FFmpeg invocation, imported only by the protected worker."""
import json
import hashlib
import math
import os
from pathlib import Path
import shutil
import sys
from .files import task_dir, ready_path, checked, clean_task
from .isolation import require_isolation
from .processes import run, ToolError


from .formats import positive as number, short_side as dimensions, audio as audio_format, estimate

def plans(info):
    result=[]
    for code in ('480p','720p','1080p','MP3'):
        e=estimate(info,code)
        if e is not None:
            result.append({'code':code,'approximate':True,**e})
    return result

def safe_text(v, limit):
    return ''.join(c for c in v if c >= ' ' and c != '\x7f')[:limit] if type(v) is str else ''

def metadata(info):
    if type(info) is not dict or info.get('_type') in {'playlist', 'multi_video'}:
        raise ToolError('unsupported_content', True)
    a = audio_format([f for f in info.get('formats', []) if type(f) is dict])
    return {'title': safe_text(info.get('title'), 200), 'uploader': safe_text(info.get('uploader') or info.get('channel'), 100),
        'duration': info.get('duration') if number(info.get('duration')) else None,
        'upload_date': safe_text(info.get('upload_date'), 8), 'view_count': info.get('view_count') if type(info.get('view_count')) is int and info['view_count'] >= 0 else None,
        'thumbnail': safe_text(info.get('thumbnail'), 2048), 'language': safe_text((a or {}).get('language') or info.get('language'), 40),
        'variants': plans(info)}

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
        root = task_dir(task_id)
        code = payload['format']
        audio = code in {'audio', 'MP3'}
        target = ready_path(task_id, audio, self.config['storage']['files_dir'])
        if target.exists() or target.is_symlink():
            raise ToolError('existing_result_requires_reconciliation', True)
        try:
            await run([sys.executable, '-m', 'bot.tool', 'download', str(task_id), code, payload['url']],
                timeout=self.config['timeouts']['download_timeout_seconds'],output_limit=1024*1024)
            sources = [p for p in root.iterdir() if p.is_file() and not p.is_symlink() and not p.name.endswith(('.part', '.ytdl', '.json'))]
            if len(sources) != 1:
                raise ToolError('invalid_download', True)
            source = checked(sources[0], root)
            streams, duration = await self.probe(source)
            max_duration = self.config['limits']['max_audio_duration_seconds' if audio else 'max_video_duration_seconds']
            if duration > max_duration:
                raise ToolError('duration_exceeded', True)
            limit = self.config['telegram']['max_upload_size_mb'][self.config['telegram']['api_mode']] * 1024*1024
            output = root / ('ready.mp3' if audio else 'ready.mp4')
            videos = [s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
            audios = [s for s in streams if s.get('codec_type') == 'audio']
            if not audios or (not audio and not videos):
                raise ToolError('missing_stream', True)
            if not audio and code.endswith('p') and dimensions(videos[0]) != int(code[:-1]):
                raise ToolError('quality_unavailable', True)
            stage('conversion' if audio else 'processing')
            ff = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n', '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map_metadata', '-1']
            if audio:
                ff += ['-map', '0:a:0', '-vn', '-c:a', 'libmp3lame', '-q:a', '0']
            else:
                compatible = videos[0].get('codec_name') == 'h264' and videos[0].get('pix_fmt') == 'yuv420p'
                ff += ['-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy' if compatible else 'libx264']
                if not compatible:
                    ff += ['-pix_fmt', 'yuv420p', '-preset', 'veryfast', '-b:v', '4000k']
                ff += ['-c:a', 'copy' if audios[0].get('codec_name') == 'aac' and audios[0].get('profile') == 'LC' else 'aac', '-b:a', '192k', '-movflags', '+faststart']
            ff += [str(output)]
            await run(ff, timeout=self.config['timeouts']['ffmpeg_timeout_seconds'])
            checked(output, root, limit)
            final_streams, final_duration = await self.probe(output)
            if final_duration > max_duration:
                raise ToolError('duration_exceeded', True)
            if audio and not any(s.get('codec_name') == 'mp3' for s in final_streams):
                raise ToolError('invalid_mp3', True)
            if not audio and not (any(s.get('codec_name') == 'h264' for s in final_streams) and any(s.get('codec_name') == 'aac' for s in final_streams)):
                raise ToolError('invalid_mp4', True)
            # Decode entire output to detect corrupt/truncated media, still under worker timeout.
            await run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe', '-i', str(output), '-f', 'null', '-'], timeout=self.config['timeouts']['ffmpeg_timeout_seconds'])
            os.replace(output, target)
            os.chmod(target, 0o600)
            with target.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            result = {'filename': target.name, 'size': target.stat().st_size, 'duration': final_duration, 'format': 'mp3' if audio else 'mp4', 'validated': True, 'sha256': digest}
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
