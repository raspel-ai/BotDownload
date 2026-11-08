"""Real local FFmpeg checks; no claims about live platform/systemd integration."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import pytest
from bot.media import MediaTools,metadata
from bot.files import ready_path,checked,UnsafeFile
from bot.broker import Broker
from bot.urls import validate_url
from bot.telegram import DeliveryError,Telegram

@pytest.fixture
def fixture_media(tmp_path):
    p=tmp_path/'fixture.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=720x480:r=10:d=1','-f','lavfi','-i','sine=frequency=500:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(p)],check=True)
    return p

@pytest.mark.parametrize('code',['480p','MP3'])
def test_real_local_media_pipeline(store,config,fixture_media,monkeypatch,code):
    import bot.media as media
    original=media.run
    async def run(args,**kw):
        if 'bot.tool' in args:
            from bot.files import task_dir
            dest=task_dir(int(args[4]));shutil.copyfile(fixture_media,dest/'source.mp4');return b''
        return await original(args,**kw)
    monkeypatch.setattr(media,'run',run)
    monkeypatch.setattr(media,'require_isolation',lambda c:None)
    from bot.formats import make_plan
    f={'format_id':'fixture','width':720,'height':480,'vcodec':'avc1','acodec':'mp4a.40.2'}
    plan=make_plan(code,None if code=='MP3' else f,f)
    result=asyncio.run(MediaTools(config).download(1,{'format':code,'selection':plan,'url':'https://youtu.be/abcdefghijk'},lambda s:None))
    p=Path('files')/result['filename']
    assert result['validated'] and p.is_file() and result['duration']<=2
    assert not Path('data/tmp/1').exists()
    assert json.loads(p.with_suffix('.json').read_text())==result
    assert result['format']==('mp3' if code=='MP3' else 'mp4')


def test_truthful_portrait_and_fallback():
    info={'formats':[{'format_id':'v','vcodec':'h264','acodec':'aac','width':720,'height':1280}], 'duration':1}
    assert metadata(info)['variants'][0]['code']=='720p'
    assert metadata({})['variants']==[]

@pytest.mark.parametrize('keep',[True,False])
def test_delivery_retention_and_once_quota(store,config,keep):
    from test_state_broker import TG,Client
    config['storage']['keep_downloaded_files']=keep
    i=store.create(123,10,1,2,validate_url('https://youtu.be/abcdefghijk'))
    store.update(i,phase='card');store.select(i,123,10,2,'video',20)
    eid=store.execution(i,'download')
    p=ready_path(eid,False);p.write_bytes(b'tested fixture')
    result={'filename':p.name,'size':p.stat().st_size,'duration':1,'format':'mp4','validated':True,'resolution':480,'selection':None,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    p.with_suffix('.json').write_text(json.dumps(result))
    store.update(i,phase='upload_pending',ready_file=str(p))
    tg=TG();b=Broker(store,Client(),config,tg)
    asyncio.run(b.upload(store.get(i)))
    assert store.get(i)['phase']=='completed' and store.used(123)==1
    assert p.exists()==keep
    assert store.rows('SELECT file_id FROM result_cache')[0]['file_id']=='FILE'


def test_upload_retry_persists_and_respects_429(store,config):
    from test_state_broker import TG,Client
    i=store.create(123,10,1,2,validate_url('https://youtu.be/abcdefghijk'))
    store.update(i,phase='upload_pending',file_id='cached',cache_hit=1,cache_key='k',format='video',metadata=json.dumps({'result':{'validated':True,'size':1,'selection':None,'resolution':480,'format':'mp4','duration':1,'sha256':'a'*64}}))
    class Fail(TG):
        async def send(self,*a,**k):raise DeliveryError(retry_after=500)
    b=Broker(store,Client(),config,Fail())
    import time
    now=time.time();asyncio.run(b.upload(store.get(i)))
    j=store.get(i)
    assert j['upload_attempts']==1 and j['next_upload']>=now+500 and j['phase']=='upload_pending'
    assert store.used(123)==0


def test_symlink_rejected(store,tmp_path):
    root=Path('files');root.mkdir(mode=0o700)
    p=root/'1.mp4';p.symlink_to(tmp_path/'other')
    with pytest.raises((UnsafeFile,OSError)):checked(p,root)


def test_telegram_endpoints(config):
    from bot.config import Settings
    for mode in ('local','cloud'):
        config['telegram']['api_mode']=mode
        t=Telegram(Settings(config,'123456:abcdefghijklmnopqrstuvwxyz123456789'))
        assert t.bot.session.api.is_local==(mode=='local')
        assert config['telegram']['api_base_urls'][mode] in t.bot.session.api.base
        asyncio.run(t.bot.session.close())


@pytest.mark.parametrize('route',['remux','transcode','portrait'])
def test_real_ffmpeg_route_and_exact_final_dimensions(store,config,tmp_path,monkeypatch,route):
    import bot.media as media
    from bot.formats import make_plan
    from bot.tool import formatter
    width,height=(240,426) if route=='portrait' else (720,480)
    codec='mpeg4' if route=='transcode' else 'libx264'
    source=tmp_path/'fixture.mkv'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',f'color=c=blue:s={width}x{height}:r=10:d=1',
        '-f','lavfi','-i','sine=frequency=500:duration=1','-c:v',codec,'-pix_fmt','yuv420p','-c:a','aac','-shortest',str(source)],check=True)
    code=f'{min(width,height)}p'
    f={'format_id':'chosen','width':width,'height':height,'vcodec':'mpeg4' if route=='transcode' else 'avc1',
       'acodec':'mp4a.40.2','ext':'mkv'}
    plan=make_plan(code,f,f)
    original=media.run;commands=[]
    async def run(args,**kw):
        if 'bot.tool' in args:
            assert json.loads(args[-2])==plan
            # Check that the downloader's callable resolves the pinned IDs.
            assert list(formatter(code,plan)({'formats':[f]}))[0]['format_id']=='chosen'
            from bot.files import task_dir
            shutil.copyfile(source,task_dir(int(args[4]))/'source.mkv');return b''
        commands.append(args)
        return await original(args,**kw)
    monkeypatch.setattr(media,'run',run)
    monkeypatch.setattr(media,'require_isolation',lambda c:None)
    result=asyncio.run(MediaTools(config).download(1,{'format':code,'selection':plan,'url':'https://www.youtube.com/watch?v=abcdefghijk'},lambda s:None))
    assert result['resolution']==min(width,height) and result['selection']==plan
    assert any('libx264' in c for c in commands)==(route=='transcode')
    assert any('-c:v' in c and c[c.index('-c:v')+1]=='copy' for c in commands)==(route!='transcode')
    assert result['size']==(Path('files')/result['filename']).stat().st_size
    assert not Path('data/tmp/1').exists()


@pytest.mark.parametrize('failure',['oversize','wrong_final_resolution'])
def test_post_ffmpeg_result_rejected_and_cleaned(store,config,fixture_media,monkeypatch,failure):
    import bot.media as media
    from bot.formats import make_plan
    original=media.run
    f={'format_id':'chosen','width':720,'height':480,'vcodec':'avc1','acodec':'mp4a.40.2'}
    plan=make_plan('480p',f,f)
    probes=0
    async def run(args,**kw):
        nonlocal probes
        if 'bot.tool' in args:
            from bot.files import task_dir
            # MKV filename forces the real FFmpeg remux path.
            shutil.copyfile(fixture_media,task_dir(int(args[4]))/'source.mkv');return b''
        out=await original(args,**kw)
        if args[0]=='ffmpeg' and args[-1].endswith('ready.mp4') and failure=='oversize':
            with Path(args[-1]).open('ab') as stream:
                stream.write(b'\0'*(1024**2))
        if args[0]=='ffprobe':
            probes+=1
            if probes==2 and failure=='wrong_final_resolution':
                data=json.loads(out)
                next(s for s in data['streams'] if s['codec_type']=='video')['width']=1440
                out=json.dumps(data).encode()
        return out
    config['telegram']['max_upload_size_mb'][config['telegram']['api_mode']]=1
    monkeypatch.setattr(media,'run',run)
    monkeypatch.setattr(media,'require_isolation',lambda c:None)
    with pytest.raises(ValueError if failure=='oversize' else RuntimeError,match='file_too_large' if failure=='oversize' else 'quality_unavailable'):
        asyncio.run(MediaTools(config).download(1,{'format':'480p','selection':plan,'url':'https://www.youtube.com/watch?v=abcdefghijk'},lambda s:None))
    assert not list(Path('files').iterdir())
    assert not Path('data/tmp/1').exists()


def test_cancel_direct_copy_closes_files_before_cleanup(store,config,fixture_media,monkeypatch):
    import bot.media as media
    from bot.formats import make_plan
    original=media.run
    f={'format_id':'chosen','width':720,'height':480,'vcodec':'avc1','acodec':'mp4a.40.2'}
    plan=make_plan('480p',f,f)
    async def run(args,**kw):
        if 'bot.tool' in args:
            from bot.files import task_dir
            shutil.copyfile(fixture_media,task_dir(int(args[4]))/'source.mp4');return b''
        return await original(args,**kw)
    def stage(name):
        if name=='processing':
            asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
    monkeypatch.setattr(media,'run',run)
    monkeypatch.setattr(media,'require_isolation',lambda c:None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(MediaTools(config).download(1,{'format':'480p','selection':plan,'url':'https://www.youtube.com/watch?v=abcdefghijk'},stage))
    assert not list(Path('files').iterdir())
    assert not Path('data/tmp/1').exists()


def test_display_matrix_survives_real_ffmpeg_remux(store,config,tmp_path,fixture_media,monkeypatch):
    import bot.media as media
    from bot.formats import make_plan
    rotated=tmp_path/'rotated.mp4'
    help_text=subprocess.run(['ffmpeg','-h','full'],capture_output=True,text=True,check=True).stdout
    rotation_args=['-display_rotation:v:0','90'] if '-display_rotation' in help_text else []
    metadata_args=[] if rotation_args else ['-metadata:s:v:0','rotate=90']
    subprocess.run(['ffmpeg','-nostdin','-v','error',*rotation_args,'-i',str(fixture_media),'-c','copy',*metadata_args,str(rotated)],check=True)
    original=media.run
    f={'format_id':'rotated','width':720,'height':480,'vcodec':'avc1','acodec':'mp4a.40.2'}
    plan=make_plan('480p',f,f)
    async def run(args,**kw):
        if 'bot.tool' in args:
            from bot.files import task_dir
            shutil.copyfile(rotated,task_dir(int(args[4]))/'source.mkv');return b''
        return await original(args,**kw)
    monkeypatch.setattr(media,'run',run)
    monkeypatch.setattr(media,'require_isolation',lambda c:None)
    tools=MediaTools(config)
    result=asyncio.run(tools.download(1,{'format':'480p','selection':plan,'url':'https://www.youtube.com/watch?v=abcdefghijk'},lambda s:None))
    streams,_=asyncio.run(tools.probe(Path('files')/result['filename']))
    video=next(s for s in streams if s.get('codec_type')=='video')
    assert media.display_dimensions(video)==(480,720)
    assert result['resolution']==480
