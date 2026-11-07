"""Real Unix IPC + real FFmpeg, with source-network and Telegram fakes explicitly injected."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
from bot.broker import Broker
from bot.client import WorkerClient
from bot.media import MediaTools
from bot.worker import Controller,connection


def test_link_card_choice_media_delivery_and_cache(store,config,tmp_path,monkeypatch):
    import bot.media as media
    fixture=tmp_path/'fixture.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=720x480:r=10:d=1','-f','lavfi','-i','sine=frequency=500:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(fixture)],check=True)
    original=media.run
    downloads=[]
    async def tools(args,**kw):
        if 'bot.tool' in args:
            if args[3]=='inspect':
                return json.dumps({'title':'Видео','duration':1,'formats':[{'format_id':'v','width':720,'height':480,'vcodec':'avc1','acodec':'mp4a.40.2','language':'ru','filesize':fixture.stat().st_size}]}).encode()
            from bot.files import task_dir
            downloads.append(args[4]);shutil.copyfile(fixture,task_dir(int(args[4]))/'source.mp4');return b''
        return await original(args,**kw)
    monkeypatch.setattr(media,'run',tools)
    monkeypatch.setattr(media,'require_isolation',lambda c:None)
    class TG:
        def __init__(self):self.sent=[];self.statuses=[]
        async def notify(self,*a):pass
        async def status(self,chat,message,text,buttons=None):
            self.statuses.append((text,buttons));return message or 100+len(self.statuses)
        async def send(self,job,source,cache=False):
            if not cache:assert Path(source).is_file()
            self.sent.append(cache);return 'TELEGRAM_FILE_ID'
    async def scenario():
        c=Controller(config,MediaTools(config))
        path=tmp_path/'worker.sock'
        server=await asyncio.start_unix_server(lambda r,w:connection(r,w,c),path=path,limit=65536)
        os.chmod(path,0o600)
        tg=TG();b=Broker(store,WorkerClient(path),config,tg)
        b.disk_allows=lambda j:True
        async def until(jid,phase):
            async with asyncio.timeout(20):
                while store.get(jid)['phase']!=phase:
                    await b.tick();await asyncio.sleep(.02)
        try:
            result,i,_=b.accept(123,10,1,'https://youtu.be/abcdefghijk');assert result=='accepted'
            await until(i,'card')
            j=store.get(i)
            assert store.select(i,123,10,j['message_id'],'480p',20)
            await until(i,'completed')
            assert len(downloads)==1 and store.used(123)==1 and tg.sent==[False]
            assert not list(Path('files').glob('*.mp4'))
            result,k,_=b.accept(123,10,2,'https://youtu.be/abcdefghijk')
            await until(k,'card');j=store.get(k)
            assert store.select(k,123,10,j['message_id'],'480p',20)
            assert store.get(k)['phase']=='upload_pending'
            await until(k,'completed')
            assert len(downloads)==1 and tg.sent==[False,True] and store.used(123)==1
        finally:
            server.close();await server.wait_closed();await c.close()
    asyncio.run(scenario())
