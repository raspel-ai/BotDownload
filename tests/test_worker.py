import asyncio
from pathlib import Path
import pytest
from bot.worker import Controller,connection
from bot.client import WorkerClient
from bot.protocol import encode

class Tools:
    def __init__(self):self.active=0;self.calls=[];self.stopped=False;self.inspect_done=False
    async def inspect(self,tid,p):self.calls.append(('inspect',tid));self.inspect_done=True;return {'title':'ok','variants':[]}
    async def download(self,tid,p,stage):
        self.calls.append(('download',tid));self.active+=1
        try:await asyncio.Event().wait()
        finally:self.active-=1;self.stopped=True

def req(i,action='download'):
    p={'url':'https://youtu.be/abcdefghijk','platform':'youtube','resource_id':'abcdefghijk'}
    if action=='download':p['format']='video'
    if action in {'cancel','status'}:p={}
    return {'version':1,'request_id':'abc','task_id':i,'action':action,'payload':p}

def test_admission_concurrency_and_cancel(config):
    async def scenario():
        tools=Tools();c=Controller(config,tools)
        assert (await c.dispatch(req(1)))['status']=='accepted'
        await asyncio.sleep(0)
        assert (await c.dispatch(req(1)))['status']=='running'
        assert (await c.dispatch(req(2)))['error']['code']=='worker_busy'
        assert (await c.dispatch(req(3,'inspect')))['status']=='accepted'
        await asyncio.sleep(0)
        assert tools.inspect_done and tools.active==1
        assert (await c.dispatch(req(1,'cancel')))['status']=='cancelled'
        assert tools.stopped and tools.active==0
        assert len([a for a in tools.calls if a[0]=='download'])==1
        await c.close()
    asyncio.run(scenario())

def test_real_unix_socket(config,tmp_path):
    async def scenario():
        import os
        tools=Tools();c=Controller(config,tools)
        path=tmp_path/'worker.sock'
        server=await asyncio.start_unix_server(lambda r,w:connection(r,w,c),path=path,limit=65536)
        os.chmod(path,0o600)
        try:
            client=WorkerClient(path)
            assert (await client.call(9,'status'))['status']=='not_found'
            assert (await client.call(9,'inspect',req(9,'inspect')['payload']))['status']=='accepted'
            await asyncio.sleep(.01)
            assert (await client.call(9,'status'))['status']=='completed'
            r,w=await asyncio.open_unix_connection(path)
            w.write(encode(req(10)|{'payload':req(10)['payload']|{'args':['evil']}}));await w.drain()
            assert await r.read()==b''
            assert ('download',10) not in tools.calls
            w.close();await w.wait_closed()
        finally:
            server.close();await server.wait_closed();await c.close()
    asyncio.run(scenario())

def test_process_tree_cancel(tmp_path):
    import sys,os,signal
    from bot.processes import run
    async def scenario():
        pidfile=tmp_path/'pid'
        code="import subprocess,time,pathlib; p=subprocess.Popen(['sleep','60']); pathlib.Path('pid').write_text(str(p.pid)); time.sleep(60)"
        t=asyncio.create_task(run([sys.executable,'-c',code],timeout=20,cwd=tmp_path))
        for _ in range(100):
            if pidfile.exists():break
            await asyncio.sleep(.01)
        assert pidfile.exists()
        child=int(pidfile.read_text());t.cancel()
        with pytest.raises(asyncio.CancelledError):await t
        # A dead orphan may remain a zombie until the container's init reaps it.
        proc=Path(f'/proc/{child}/stat')
        if proc.exists():assert proc.read_text().split()[2]=='Z'
    asyncio.run(scenario())
