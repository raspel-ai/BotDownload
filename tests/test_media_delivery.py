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
    result=asyncio.run(MediaTools(config).download(1,{'format':code,'url':'https://youtu.be/abcdefghijk'},lambda s:None))
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
    result={'filename':p.name,'size':p.stat().st_size,'duration':1,'format':'mp4','validated':True,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
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
    store.update(i,phase='upload_pending',file_id='cached',cache_hit=1,cache_key='k')
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
