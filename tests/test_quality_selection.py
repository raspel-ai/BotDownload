"""Quality/size contract: deterministic metadata, real local FFmpeg in media tests.
No live platforms or Telegram are contacted here.
"""
import asyncio
import copy
import json

import pytest

from bot.broker import Broker
from bot.formats import (allocate, audio, candidates, estimate, make_plan,
                         selection, validate_plan)
from bot.media import metadata
from bot.protocol import ProtocolError, encode, request
from bot.state import cache_key
from bot.urls import validate_url
from test_state_broker import TG, Client


def combined(height, *, size=10000, ident=None, **kw):
    return {'format_id':ident or str(height),'width':height*2,'height':height,
            'vcodec':'avc1','acodec':'mp4a.40.2','ext':'mp4','pix_fmt':'yuv420p',
            'filesize':size,'tbr':height,'abr':128,**kw}


def info(heights, **kw):
    return {'duration':60,'formats':[combined(h) for h in heights],**kw}


def codes(data, limit=10**9):
    return [v['code'] for v in allocate(candidates(data),limit) if v['code'] != 'MP3']


@pytest.mark.parametrize('heights,expected',[
    ([480,720,1080],['480p','720p','1080p']),([240],['240p']),
    ([680,1080],['680p','1080p']),([800],['800p']),
    ([600,800,1200],['600p','800p','1200p']),
    ([360,720,1080],['360p','720p','1080p']),
    ([360,600,720,1080],['360p','720p','1080p']),
    ([240,360,480,600,680,720,800,1080,1200],['480p','720p','1080p']),
    ([680,800],['680p','800p']),([240,360,600,800],['360p','600p','800p']),
])
def test_assignment(heights,expected):
    # Equal-distance tie for target480 chooses360 over600 after exact720/1080.
    # Make audio independent, so all combined encodings can be compared equally.
    data=info(heights)
    assert codes(data)==expected
    assert codes(data)==codes(data|{'formats':list(reversed(data['formats']))})
    assert len(set(codes(data)))==len(codes(data))<=3


def test_audio_prefers_real_audio_not_combined_tbr_and_preserves_language():
    a={'format_id':'a','vcodec':'none','acodec':'opus','abr':128,'language':'ru-RU'}
    huge=combined(1080,tbr=99999,language='ru')
    assert audio([huge,a])==a
    other=a|{'format_id':'other','audio_track_id':'other','abr':999}
    assert audio([huge,other,a])==a  # retain the first Russian track
    data={'formats':[huge,a]}
    plan=next(v['plan'] for v in candidates(data) if v['code']=='1080p')
    assert plan['audio']['id']=='a'
    assert [f['format_id'] for f in selection(data,'1080p',plan)]==['1080','a']
    assert audio([combined(480),huge])==huge  # Russian before unknown language
    assert audio([combined(480,language='en'),a])==a
    original={'format_id':'original','vcodec':'none','acodec':'aac','abr':64,'format_note':'original'}
    assert audio([combined(1080),original])==original


def test_audio_combined_bitrate_does_not_rank_as_audio():
    a=combined(480,ident='a',abr=None,tbr=100)
    b=combined(1080,ident='b',abr=None,tbr=99999)
    assert audio([b,a])['format_id']=='a'


def test_stream_specific_size_remux_transcode_and_vbr():
    a={'format_id':'a','vcodec':'none','acodec':'mp4a.40.2','filesize':1000}
    v1=combined(480,size=10000,acodec='none')
    v2=combined(720,size=20000,acodec='none')
    data={'duration':60,'formats':[v1,v2,a]}
    x,y=estimate(data,v1,a),estimate(data,v2,a)
    assert (x['size'],y['size'])==(11000,21000)
    assert x['size_kind']=='approximate' and x['peak_bytes']==22000
    converted=estimate(data,v1|{'vcodec':'vp9'},a)
    assert converted['size'] is None and converted['peak_bytes']>11000
    mp3=estimate(data,None,a)
    assert mp3['size'] is None and mp3['size_kind']=='unknown'
    assert mp3['peak_bytes']>=1000+60*320000//8
    direct=estimate(data,v1|{'acodec':'mp4a.40.2'},v1|{'acodec':'mp4a.40.2'})
    assert direct['size']==10000 and direct['size_kind']=='known'


def test_unknown_and_boundary_not_proof_of_oversize():
    v=combined(480,filesize=None,filesize_approx=10001)
    data={'duration':60,'formats':[v]}
    assert codes(data,10000)==['480p']
    assert candidates(data)[0]['size_kind']=='approximate'
    assert codes(info([480]),10000)==['480p'] # equality permitted
    assert not codes(info([480]),9999)        # direct-copy exact size
    unknown=v|{'filesize_approx':None,'tbr':None}
    assert candidates({'formats':[unknown]})[0]['size'] is None


def test_filter_before_assignment_and_multiple_encodings():
    a=combined(480,size=20000,ident='a480',abr=192)
    smaller=a|{'format_id':'b480','filesize':5000,'tbr':1}
    data={'duration':60,'formats':[a,combined(360,size=5000,abr=192)]}
    assert codes(data,10000)==['360p']
    result=allocate(candidates(data|{'formats':[a,smaller]}),10000)
    assert result[0]['code']=='480p' and result[0]['plan']['video']['id']=='b480'


def test_pinned_stream_never_reselected_or_silently_replaced():
    data=info([680,720])
    v=next(v for v in candidates(data) if v['code']=='680p')
    chosen=selection(data,'680p',v['plan'])
    assert chosen[0]['format_id']=='680'
    changed=copy.deepcopy(data)
    changed['formats'][0]['format_id']='replacement680'
    with pytest.raises(ValueError,match='quality_unavailable'):
        selection(changed,'680p',v['plan'])
    changed=copy.deepcopy(data)
    changed['formats'][0]['pix_fmt']='yuv420p10le'
    with pytest.raises(ValueError,match='quality_unavailable'):
        selection(changed,'680p',v['plan'])
    changed=copy.deepcopy(data)
    changed['formats'][0]['width']=1400
    with pytest.raises(ValueError,match='quality_unavailable'):
        selection(changed,'680p',v['plan'])


def frame(plan):
    return {'version':1,'request_id':'pinned','task_id':1,'action':'download',
            'payload':{'url':'https://www.youtube.com/watch?v=abcdefghijk','platform':'youtube',
                       'resource_id':'abcdefghijk','format':plan['code'],'selection':plan}}


@pytest.mark.parametrize('bad',[
    lambda p:p.update(extra='ignored'),lambda p:p['video'].update(id='bestvideo+bestaudio'),
    lambda p:p['video'].update(id='https://example.com'),lambda p:p['video'].update(width=True),
    lambda p:p.update(code='720p'),lambda p:p.update(processing='shell'),
    lambda p:p['audio'].update(acodec='none'),lambda p:p.update(audio=None),
])
def test_strict_ipc_descriptions(bad):
    p=candidates(info([680]))[0]['plan']
    valid=frame(p)
    assert request(encode(valid))==valid
    bad(p)
    with pytest.raises((ProtocolError,ValueError)):
        request(encode(valid))


class Capture(TG):
    def __init__(self):
        super().__init__();self.statuses=[]
    async def status(self,c,m,t,k=None):
        self.statuses.append((t,k));return m


def card(store,config,data):
    jid=store.create(123,10,1,2,validate_url('https://youtu.be/abcdefghijk'))
    tg=Capture();b=Broker(store,Client(),config,tg)
    asyncio.run(b.card(store.get(jid),data))
    return jid,b,tg


@pytest.mark.parametrize('mode,limit', [('cloud',1),('local',3)])
def test_card_uses_live_config_and_unknown_audio(store,config,mode,limit):
    config['telegram']['api_mode']=mode
    config['telegram']['max_upload_size_mb'][mode]=limit
    data=metadata({'duration':60,'formats':[combined(360,size=10000),combined(480,size=2*1024**2,abr=192)]})
    jid,b,tg=card(store,config,data)
    offered=[v['code'] for v in json.loads(store.get(jid)['metadata'])['variants']]
    assert ('480p' in offered)==(limit==3)
    assert '360p' in offered
    assert 'размер неизвестен' in str(tg.statuses[-1][1])


@pytest.mark.parametrize('only_mp3',[False,True])
def test_all_oversize_and_mp3_only_without_fallback(store,config,only_mp3):
    source=info([480,720,1080])
    source['formats'].insert(0,combined(240,acodec='none')|{'width':None,'height':None})
    data=metadata(source)
    assert data['analysis']=='known'
    for v in data['variants']:
        v.update(size=10**12,size_kind='known')
    if only_mp3:
        data['variants'][-1].update(size=1)
    jid,b,tg=card(store,config,data)
    text,buttons=tg.statuses[-1]
    if only_mp3:
        assert [code for _,code in buttons]==['MP3']
        assert 'Видеоформаты превышают лимит' in text
    else:
        assert store.get(jid)['phase']=='failed' and not buttons
        assert 'Нет доступных форматов' in text
    assert not store.select(jid,123,10,2,'video',20)
    assert not store.select(jid,123,10,2,'audio',20)


@pytest.mark.parametrize('unknown_first',[True,False])
def test_known_resolutions_survive_dimensionless_stream(store,config,unknown_first):
    source=info([480,720])
    unknown=combined(240,acodec='none')|{'width':None,'height':None}
    source['formats'].insert(0 if unknown_first else len(source['formats']),unknown)
    data=metadata(source)
    assert data['analysis']=='known'
    jid,b,tg=card(store,config,data)
    assert [code for _,code in tg.statuses[-1][1]]==['480p','720p','MP3']


def test_success_without_formats_distinct_from_inspect_failure(store,config):
    jid,b,tg=card(store,config,{'analysis':'known','variants':[]})
    assert not tg.statuses[-1][1]
    asyncio.run(b.card(store.get(jid),{},True))
    assert [c for _,c in tg.statuses[-1][1]]==['video','audio']


def actual(plan,size=1000):
    return {'filename':'1.mp4','size':size,'duration':60,'format':'mp4',
            'validated':True,'sha256':'a'*64,'resolution':680,'selection':plan}


def seed_cache(store,plan,result):
    key=cache_key('youtube','abcdefghijk','680p',plan)
    with store.db() as db:
        db.execute('INSERT OR REPLACE INTO result_cache VALUES(?,?,1,?)',(key,'CACHED',json.dumps({'result':result})))
    return key


def test_cache_checks_actual_size_and_rechecks_limit_at_delivery(store,config):
    data=metadata(info([680]));plan=data['variants'][0]['plan']
    seed_cache(store,plan,actual(plan,size=2*1024**2))
    config['telegram']['max_upload_size_mb']['cloud']=3
    jid,b,tg=card(store,config,data)
    assert store.select(jid,123,10,2,'680p',20,3*1024**2)
    assert store.get(jid)['cache_hit']
    config['telegram']['max_upload_size_mb']['cloud']=1
    asyncio.run(b.upload(store.get(jid)))
    assert not tg.sent and store.used(123)==0
    assert '1 МиБ' in tg.statuses[-1][0]
    assert '680p' not in [v['code'] for v in json.loads(store.get(jid)['metadata'])['variants']]


@pytest.mark.parametrize('damage',['missing_size','wrong_resolution','wrong_track','too_big'])
def test_cache_cannot_bypass_result_validation(store,config,damage):
    data=metadata(info([680]));p=data['variants'][0]['plan'];r=actual(p)
    if damage=='missing_size':r.pop('size')
    if damage=='wrong_resolution':r['resolution']=720
    if damage=='wrong_track':r['selection']=copy.deepcopy(p);r['selection']['audio']['track']='different'
    if damage=='too_big':r['size']=10**12
    seed_cache(store,p,r)
    jid,b,tg=card(store,config,data)
    # Oversize cache evidence removes this button before selection.
    if damage=='too_big':
        assert not store.select(jid,123,10,2,'680p',20,10**9)
    else:
        assert store.select(jid,123,10,2,'680p',20,10**9)
        assert not store.get(jid)['cache_hit']


def test_cache_key_distinguishes_stream_track_and_processing():
    p=candidates(info([680]))[0]['plan']
    keys={cache_key('youtube','abcdefghijk','680p',p)}
    for name,field,value in [('video','id','other'),('audio','track','ru2')]:
        q=copy.deepcopy(p);q[name][field]=value
        keys.add(cache_key('youtube','abcdefghijk','680p',q))
    keys.add(cache_key('youtube','abcdefghijk','720p',p))
    assert len(keys)==4


def test_callbacks_ownership_duplicates_cancel_and_no_reinspect(store,config):
    jid,b,tg=card(store,config,metadata(info([680])))
    assert not store.select(jid,999,10,2,'680p',20)
    assert not store.select(jid,123,10,2,'bestvideo;cmd',20)
    assert store.select(jid,123,10,2,'680p',20)
    assert not store.select(jid,123,10,2,'680p',20)
    payload=b.payload(store.get(jid),'download')
    assert payload['selection']['video']['id']=='680'
    assert not b.client.calls
    assert asyncio.run(b.cancel_all(123))==(1,0)
    assert not store.select(jid,123,10,2,'680p',20)


def test_unknown_disk_threshold_and_known_extra_processing(store,config):
    data=metadata(info([680],duration=None))
    data['variants'][0].update(size=None,size_kind='unknown',peak_bytes=None,known_peak_bytes=4*1024**3)
    jid,b,tg=card(store,config,data)
    assert store.select(jid,123,10,2,'680p',20)
    threshold=config['storage']['unknown_size_fallback_mib']*1024**2
    assert not b.disk_allows(store.get(jid),threshold)
    assert not b.disk_allows(store.get(jid),threshold+1)
    assert b.disk_allows(store.get(jid),4*1024**3)


@pytest.mark.parametrize('reason',['file_too_large','quality_unavailable'])
def test_final_failure_is_explicit_no_silent_substitute(store,config,reason):
    jid,b,tg=card(store,config,metadata(info([680,1080])))
    assert store.select(jid,123,10,2,'680p',20)
    asyncio.run(b.reconcile(store.get(jid),{'status':'failed','error':{'code':reason,'permanent':True}}))
    if reason=='quality_unavailable':
        assert store.get(jid)['phase']=='failed'
        assert 'Повторите анализ' in tg.statuses[-1][0]
    else:
        assert store.get(jid)['phase']=='card'
        assert 'превышает лимит' in tg.statuses[-1][0]
        assert '680p' not in [v['code'] for v in json.loads(store.get(jid)['metadata'])['variants']]
    assert not tg.sent


@pytest.mark.parametrize('url',[
    'https://www.youtube.com/watch?v=abcdefghijk',
    'https://www.instagram.com/reel/AbCdEf/',
    'https://www.tiktok.com/@user/video/1234567890123456789',
])
def test_same_quality_pipeline_preserves_platform_rules(store,config,url):
    u=validate_url(url)
    jid=store.create(123,10,1,2,u)
    b=Broker(store,Client(),config,Capture())
    asyncio.run(b.card(store.get(jid),metadata(info([680]))))
    assert store.select(jid,123,10,2,'680p',20)
    p=b.payload(store.get(jid),'download')
    assert request(encode(frame(p['selection'])|{'payload':p}))['payload']['platform']==u.platform


def test_known_missing_audio_is_not_unknown_inspect_or_false_oversize(store,config):
    data=metadata({'formats':[combined(680,acodec='none')]})
    assert data['analysis']=='known' and not data['variants']
    jid,b,tg=card(store,config,data)
    assert not tg.statuses[-1][1]
    assert 'превышают лимит' not in tg.statuses[-1][0]
    assert store.get(jid)['phase']=='failed'


def test_unknown_resolution_uses_only_generic_buttons(store,config):
    data=metadata({'formats':[combined(680)|{'width':None,'height':None}]})
    jid,b,tg=card(store,config,data)
    assert [code for _,code in tg.statuses[-1][1]]==['video','audio']


def test_split_streams_formatter_and_pinned_ipc():
    from bot.tool import formatter
    v=combined(680,acodec='none',protocol='https')
    a={'format_id':'audio','vcodec':'none','acodec':'mp4a.40.2','abr':128,'protocol':'https'}
    data={'formats':[v,a]}
    p=candidates(data)[0]['plan']
    assert p['audio']['id']=='audio'
    fmt=list(formatter('680p',p)(data))[0]
    assert fmt['format_id']=='680+audio'
    assert fmt['requested_formats'][0]['acodec']=='none'
    assert fmt['requested_formats'][1]['vcodec']=='none'
    assert request(encode(frame(p)))['payload']['selection']==p


def test_retry_unknown_alternatives_and_dedup_do_not_reoffer_failed(store,config):
    jid,b,tg=card(store,config,metadata(info([680,1080])))
    assert store.select(jid,123,10,2,'680p',20)
    asyncio.run(b.alternatives(store.get(jid),'file_too_large'))
    assert store.select(jid,123,10,2,'1080p',20)
    asyncio.run(b.alternatives(store.get(jid),'file_too_large'))
    assert [v['code'] for v in json.loads(store.get(jid)['metadata'])['variants']]==['MP3']
    assert not store.select(jid,123,10,2,'680p',20)


def test_oversize_without_alternatives(store,config):
    jid,b,tg=card(store,config,metadata(info([680])))
    # Remove MP3 to exercise an exhausted choice set.
    meta=json.loads(store.get(jid)['metadata']);meta['variants']=meta['variants'][:1]
    store.update(jid,metadata=json.dumps(meta))
    assert store.select(jid,123,10,2,'680p',20)
    asyncio.run(b.alternatives(store.get(jid),'file_too_large'))
    assert store.get(jid)['phase']=='failed'
    assert not tg.statuses[-1][1] and 'Других доступных форматов нет' in tg.statuses[-1][0]


def test_pinned_result_recovered_after_worker_restart_without_download(store,config):
    import hashlib
    from bot.files import ready_path
    from bot.state import Store
    jid,b,tg=card(store,config,metadata(info([680])))
    assert store.select(jid,123,10,2,'680p',20)
    plan=b.payload(store.get(jid),'download')['selection']
    eid=store.execution(jid,'download')
    p=ready_path(eid,False);p.write_bytes(b'verified result fixture')
    result=actual(plan,p.stat().st_size)|{'filename':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    p.with_suffix('.json').write_text(json.dumps(result))
    store.update(jid,phase='running')
    recovered=Store(store.path)
    resumed=Broker(recovered,Client(),config,tg)
    asyncio.run(resumed.reconcile(recovered.get(jid),{'status':'not_found'}))
    assert recovered.get(jid)['phase']=='upload_pending'
    assert json.loads(recovered.get(jid)['metadata'])['result']==result
    asyncio.run(resumed.upload(recovered.get(jid)))
    assert recovered.get(jid)['phase']=='completed' and recovered.used(123)==1
    assert not resumed.client.calls


def test_direct_copy_requires_codec_and_pixel_format_evidence():
    v=combined(480,vcodec='avc1.4d401f',pix_fmt=None)
    assert estimate({'duration':60},v,v)['size_kind']=='known'
    for altered in (v|{'vcodec':'avc1.6e0033'},v|{'pix_fmt':'yuv420p10le'},v|{'acodec':'aac_he'}):
        assert estimate({'duration':60},altered,altered)['size_kind']!='known'


def test_size_filter_known_approximate_and_unknown_boundaries():
    from bot.formats import too_large
    limit=50*1024**2
    for kind,size,expected in [
        ('known',limit,False),('known',limit+1,True),
        ('approximate',limit+1,False),('approximate',55*1024**2,False),
        ('approximate',55*1024**2+1,True),('unknown',2353*1024**2,False),
        ('unknown',None,False),
    ]:
        assert too_large({'size_kind':kind,'size':size},limit)==expected


@pytest.mark.parametrize('has_alternatives',[True,False])
def test_mp3_oversize_reuses_card_size_evidence(store,config,has_alternatives):
    from bot.files import ready_path
    config['telegram']['api_mode']='cloud'
    config['telegram']['max_upload_size_mb']['cloud']=50
    limit=50*1024**2
    data=metadata(info([480,720,1080],duration=70*60+25))
    for v,mib in zip(data['variants'],[423,678,2353]):
        v.update(size=mib*1024**2,size_kind='approximate')
    if has_alternatives:
        data['variants'][1].update(size=55*1024**2)
        data['variants'][2].update(size=None,size_kind='unknown')
    jid,b,tg=card(store,config,data)
    assert [c for _,c in tg.statuses[-1][1]]==(['720p','1080p','MP3'] if has_alternatives else ['MP3'])
    assert store.select(jid,123,10,2,'MP3',20)
    eid=store.execution(jid,'download')
    p=ready_path(eid,True)
    with p.open('wb') as stream:
        stream.truncate(limit+1)  # sparse ready-file fixture, no media download
    store.update(jid,phase='upload_pending',ready_file=str(p))
    asyncio.run(b.upload(store.get(jid)))
    text,buttons=tg.statuses[-1]
    assert 'превышает лимит Telegram — 50 МиБ' in text
    assert not tg.sent and not b.client.calls and not p.exists()
    assert 'MP3' not in [v['code'] for v in json.loads(store.get(jid)['metadata'])['variants']]
    if has_alternatives:
        assert [c for _,c in buttons]==['720p','1080p']
        assert '≈ 55.0 МиБ' in buttons[0][0] and 'размер неизвестен' in buttons[1][0]
    else:
        assert not buttons and 'Других доступных форматов нет' in text
        assert 'Выберите' not in text and store.get(jid)['error']=='file_too_large'
