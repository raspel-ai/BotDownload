"""Approved share links are validated in full before deterministic canonicalization."""
from itertools import permutations
import socket
import urllib.request
import pytest
from bot.urls import validate_url,InvalidURL,YOUTUBE_AUX_MAX_LENGTH

REAL_CASES=[
    ('https://youtu.be/r55tdz1qjxk?si=raxvqCfo00Zwisyq','r55tdz1qjxk'),
    ('https://youtu.be/Qqz5-xcELsc?si=Z68q7lFodncDAAN_','Qqz5-xcELsc'),
    ('https://www.youtube.com/shorts/nwrcBMU_RAE','nwrcBMU_RAE'),
    ('https://youtube.com/shorts/B8jsas3KrPo?si=ANl-R937lXGTt-HQ','B8jsas3KrPo'),
    ('https://www.youtube.com/watch?v=S-h8z0zgX_U&list=RDS-h8z0zgX_U&start_radio=1','S-h8z0zgX_U'),
]
ID='abcdefghijk'
CANONICAL=f'https://www.youtube.com/watch?v={ID}'
HOSTS=('youtube.com','www.youtube.com','m.youtube.com','music.youtube.com')

@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail('URL validation must never access the network')
    for method in ('getaddrinfo','gethostbyname','gethostbyname_ex','gethostbyaddr','create_connection','socket'):
        monkeypatch.setattr(socket,method,forbidden)
    monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',forbidden)


@pytest.mark.parametrize('raw,rid',REAL_CASES)
def test_five_user_examples(raw,rid):
    u=validate_url(raw)
    assert (u.url,u.platform,u.resource_id)==(f'https://www.youtube.com/watch?v={rid}','youtube',rid)
    assert validate_url(u.url)==u


@pytest.mark.parametrize('host',HOSTS)
@pytest.mark.parametrize('path',[
    f'/watch?v={ID}',f'/watch?list=PL_valid_-&v={ID}',
    f'/shorts/{ID}',f'/shorts/{ID}?si=TOKEN_-',
])
def test_allowed_hosts_forms_and_canonical_idempotency(host,path):
    u=validate_url(f'https://{host}{path}')
    assert u.url==CANONICAL and u.resource_id==ID
    assert validate_url(u.url)==u


@pytest.mark.parametrize('parts',list(permutations([f'v={ID}','list=RD_valid_-','start_radio=1'])))
def test_query_order_does_not_change_video(parts):
    assert validate_url('https://m.youtube.com/watch?'+'&'.join(parts)).url==CANONICAL


@pytest.mark.parametrize('suffix',['','?si=x','?si=_','?si=-','?si=TOKEN_-'])
def test_short_url(suffix):
    assert validate_url(f'https://youtu.be/{ID}{suffix}').url==CANONICAL


@pytest.mark.parametrize('prefix',[
    f'https://youtu.be/{ID}?si=',f'https://youtube.com/shorts/{ID}?si=',
    f'https://youtube.com/watch?v={ID}&list=',
])
def test_opaque_identifier_bound(prefix):
    assert validate_url(prefix+'x'*YOUTUBE_AUX_MAX_LENGTH).url==CANONICAL
    for value in ('','x'*(YOUTUBE_AUX_MAX_LENGTH+1),'a.b','a/b','a:b','https://evil.example','a+b','a=b'):
        with pytest.raises(InvalidURL):validate_url(prefix+value)


BAD_URLS=[
    f'https://youtube.com/watch?v={ID}&v={ID}',
    f'https://youtu.be/{ID}?si=a&si=b',
    f'https://youtube.com/shorts/{ID}?si=a&si=a',
    f'https://youtube.com/watch?v={ID}&list=RDx&list=RDx',
    f'https://youtube.com/watch?v={ID}&list=RDx&start_radio=1&start_radio=1',
    f'https://youtube.com/watch?v={ID}&start_radio=1',
    f'https://youtube.com/watch?v={ID}&list=RDx&start_radio=0',
    f'https://youtube.com/watch?v={ID}&list=RDx&start_radio=01',
    f'https://youtube.com/watch?v={ID}&list=RDx&start_radio=true',
    f'https://youtube.com/watch?v={ID}&list=RDx&start_radio=',
    f'https://youtube.com/watch?v={ID}&list=',
    f'https://youtube.com/watch?v={ID}&si=TOKEN',
    f'https://youtube.com/watch?v={ID}&t=10',
    f'https://youtube.com/watch?v={ID}&start=10',
    f'https://youtube.com/watch?v={ID}&index=2',
    f'https://youtube.com/watch?v={ID}&feature=share',
    f'https://youtube.com/watch?v={ID}&utm_source=share',
    f'https://youtube.com/watch?v={ID}&unknown=x',
    f'https://youtu.be/{ID}?v={ID}',
    f'https://youtu.be/{ID}?list=RDx',
    f'https://youtube.com/shorts/{ID}?list=RDx',
    f'https://youtube.com/shorts/{ID}?v={ID}',
    f'https://youtube.com/shorts/{ID}?start_radio=1',
    f'https://youtu.be/{ID}?si=',f'https://youtu.be/{ID}?si',
    f'https://youtu.be/{ID}?si=a&',f'https://youtu.be/{ID}?&si=a',
    f'https://youtu.be/{ID}?si=a&&',f'https://youtu.be/{ID}?si=a=b',
    f'https://youtu.be/{ID}?si=a;v={ID}',f'https://youtu.be/{ID}?si=a?si=b',
    f'https://youtu.be/{ID}?SI=a',f'https://youtu.be/{ID}?=a',
    f'https://youtube.com/watch?&v={ID}',f'https://youtube.com/watch?v={ID}&',
    f'https://youtube.com/watch?v={ID}&&list=RDx',
    f'https://youtube.com/watch?list=RDx',
    f'https://youtube.com/watch?list=RDx&start_radio=1',
    'https://youtube.com/playlist?list=RDx',
    'https://youtu.be/abcdefghij','https://youtu.be/abcdefghijkl',
    'https://youtube.com/watch?v=abcdefghij','https://youtube.com/watch?v=abcdefghijkl',
    'https://youtube.com/shorts/abcdefghij','https://youtube.com/shorts/abcdefghijkl',
    'https://youtube.com/watch?v=https://evil.example',
    f'https://youtube.com/watch?v={ID}&list=https://evil.example',
    f'https://youtu.be/{ID}?si=https://evil.example',
    f'https://youtu.be/{ID[:-1]}.',f'https://youtube.com/shorts/{ID[:-1]}+',
    f'https://youtube.com/shorts/{ID}/',f'https://youtube.com/shorts/{ID}/extra',
    f'https://youtu.be/{ID}/extra',f'https://youtube.com/embed/{ID}',
    f'https://youtube.com/live/{ID}',f'https://youtube.com/watch/?v={ID}',
    f'https://youtu.be/{ID}?',f'https://youtube.com/shorts/{ID}?',
    f'https://youtube.com.evil/shorts/{ID}',f'https://sub.youtube.com/shorts/{ID}',
    f'https://youtu.be.evil/{ID}',f'http://youtu.be/{ID}?si=a',
    f'https://127.0.0.1/shorts/{ID}',f'https://[::1]/shorts/{ID}',
    f'https://localhost/shorts/{ID}',f'https://192.168.0.1/shorts/{ID}',
    f'https://youtu.be:443/{ID}',f'https://youtu.be:8443/{ID}',
    f'https://user@youtu.be/{ID}',f'https://user:pass@youtube.com/shorts/{ID}',
    f'https://youtu.be/{ID}?si=%41',f'https://youtu.be/{ID}%3Fsi=a',
    f'https://youtu.be/{ID}#',f'https://youtube.com/shorts/{ID}#x',
    f'https://youtu.be/{ID}?si=тест',f'https://youtu.be/{ID}?si=a b',
    f'https://youtu.be/{ID}?si=a\nb',f'https://youtu.be/{ID}?si=a\tb',
    f'https://youtu.be/{ID}?si=a\rb',f'https://youtu.be/{ID}?si=a\x00b',
    f'https://youtu.be/{ID}?si=a\x7fb',f'https://youtu.be/{ID}\\evil',
    f'https://youtu.be/{ID} https://youtu.be/{ID}',
    f'https://youtu.be/{ID}?si='+'a'*2048,
]

@pytest.mark.parametrize('raw',BAD_URLS)
def test_malformed_unknown_ambiguous_or_unsafe_rejected(raw):
    with pytest.raises(InvalidURL):validate_url(raw)


@pytest.mark.parametrize('raw',[
    'https://instagram.com/p/Ab_c-1/', 'https://www.instagram.com/reel/Ab_c-1/',
    'https://tiktok.com/@user.name/video/123456789',
    'https://www.tiktok.com/@user/video/123','https://m.tiktok.com/@user/video/123',
])
def test_other_platforms_unchanged(raw):
    assert validate_url(raw).url==raw
    for suffix in ('?','?si=token','?list=RDx'):
        with pytest.raises(InvalidURL):validate_url(raw+suffix)
