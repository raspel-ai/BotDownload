import json
import pytest
from bot.urls import validate_url,InvalidURL
from bot.protocol import request,encode,ProtocolError,MAX_MESSAGE

@pytest.mark.parametrize('url,platform,rid',[
 ('https://youtube.com/watch?v=abcdefghijk','youtube','abcdefghijk'),
 ('https://www.youtube.com/watch?v=abcdefghijk','youtube','abcdefghijk'),
 ('https://m.youtube.com/watch?v=abcdefghijk','youtube','abcdefghijk'),
 ('https://music.youtube.com/watch?v=abcdefghijk','youtube','abcdefghijk'),
 ('https://youtu.be/abcdefg_-12','youtube','abcdefg_-12'),
 ('https://instagram.com/reel/Ab_123-/','instagram','Ab_123-'),
 ('https://www.instagram.com/p/Ab_123-/','instagram','Ab_123-'),
 ('https://tiktok.com/@user.name/video/123456789','tiktok','123456789'),
 ('https://www.tiktok.com/@user/video/123','tiktok','123'),
 ('https://m.tiktok.com/@user/video/123','tiktok','123'),
])
def test_valid(url,platform,rid,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:pytest.fail('DNS forbidden'))
    u=validate_url(url)
    assert (u.platform,u.resource_id)==(platform,rid)
    assert u.url==(f'https://www.youtube.com/watch?v={rid}' if platform=='youtube' else url)

@pytest.mark.parametrize('url',[
 '',None,'https://youtube.com/watch?v=abcdefghijk&v=abcdefghijk',
 'https://youtube.com/watch?v=abcdefghijk&t=2','https://youtube.com/watch?v=abcdefghijk&list=',
 'https://youtu.be/abcdefghijk?si=','https://youtu.be/abcdefghijk?',
 'https://youtube.com/watch?v=abc','https://youtube.com/playlist?list=x',
 'https://youtube.com/shorts/abcdefghijk/extra','https://user@youtube.com/watch?v=abcdefghijk',
 'https://youtube.com:443/watch?v=abcdefghijk','http://youtu.be/abcdefghijk',
 'https://127.0.0.1/abcdefghijk','https://[::1]/abcdefghijk',
 'https://youtube.com.evil/watch?v=abcdefghijk','https://evil.youtube.com/watch?v=abcdefghijk',
 'https://vm.tiktok.com/a','https://vt.tiktok.com/a','https://instagr.am/p/abc/',
 'https://instagram.com/stories/abc/','https://instagram.com/p/abc/?utm_source=x',
 'https://tiktok.com/@a/video/a','https://tiktok.com/@a/video/123?x=1',
 'https://youtu.be/abcdefghijk#','https://youtu.be/abcdefghijk#x',
 'https://youtu.be/abcdef%67hijk','https://youtu.be/abcdefghijk\n',
 ' https://youtu.be/abcdefghijk','https://youtu.be/abcdefghijk https://youtu.be/abcdefghijk',
 'https://youtu.be/abcdefghijk\\a','https://youtu.be/abcdefghijk\x7f',
 'https://youtu.be/abcdefghijk'+'a'*2048,
])
def test_invalid(url):
    with pytest.raises(InvalidURL):validate_url(url)

def envelope(action='inspect'):
    return {'version':1,'request_id':'abc','task_id':1,'action':action,'payload':{'url':'https://www.youtube.com/watch?v=abcdefghijk','platform':'youtube','resource_id':'abcdefghijk'}}

def test_protocol_exact():
    v=envelope();assert request(encode(v))==v
    for change in ({'version':True},{'version':2},{'task_id':False},{'task_id':0},{'action':'execute_media'},{'shell':'x'}):
        with pytest.raises(ValueError):request(encode(v|change))
    with pytest.raises(ValueError):request(encode(v|{'payload':v['payload']|{'proxy':'x'}}))
    with pytest.raises(ValueError):request(b'{"version":1,"version":1}\n')
    with pytest.raises(ValueError):request(b'x'*MAX_MESSAGE+b'\n')
    with pytest.raises(ValueError):request(encode(v)[:-1])
    with pytest.raises(ValueError):request(encode(v|{'payload':v['payload']|{'resource_id':'other'}}))
