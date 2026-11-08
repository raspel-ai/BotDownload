"""Trusted link exemption must not bypass idempotency, URL or queue admission."""
import time
import pytest
from bot.broker import Broker
from bot.state import Store
from test_state_broker import Client,TG

URL='https://youtu.be/abcdefghijk'
TRUSTED=123
REGULAR=456


def broker(store,config,trusted=True):
    config['trusted_users']=[TRUSTED] if trusted else []
    return Broker(store,Client(),config,TG())


def count(store,table,user):
    return store.rows(f'SELECT count(*) n FROM {table} WHERE user_id=?',(user,))[0]['n']


def test_trusted_links_exceed_limit_without_attempts_or_ban(store,config):
    b=broker(store,config)
    for update in range(config['rate_limit']['link_attempts']+5):
        result,jid,error=b.accept(TRUSTED,10,update,URL)
        assert result=='accepted' and jid is not None and not error
    assert count(store,'attempts',TRUSTED)==0
    assert count(store,'bans_v1',TRUSTED)==0


def test_regular_sixth_link_blocks_with_same_duration(store,config,monkeypatch):
    now=1000
    monkeypatch.setattr('bot.state.time.time',lambda:now)
    b=broker(store,config)
    for update in range(5):
        assert b.accept(REGULAR,10,update,URL)[0]=='accepted'
    assert b.accept(REGULAR,10,5,URL)[:2]==('blocked',None)
    assert count(store,'attempts',REGULAR)==5
    assert store.rows('SELECT until FROM bans_v1 WHERE user_id=?',(REGULAR,))[0]['until']==now+3600
    now+=600
    assert b.accept(REGULAR,10,6,URL)[0]=='blocked'
    now=4600
    assert b.accept(REGULAR,10,7,URL)[0]=='accepted'


@pytest.mark.parametrize('trusted',[False,True])
def test_existing_ban_respected_only_for_regular_users(store,config,trusted):
    now=time.time()
    with store.db() as db:
        db.execute('INSERT INTO bans_v1 VALUES(?,?)',(TRUSTED,now+3600))
        db.execute('INSERT INTO attempts VALUES(?,?)',(TRUSTED,now))
    before_bans=store.rows('SELECT * FROM bans_v1')
    before_attempts=store.rows('SELECT * FROM attempts')
    b=broker(Store(),config,trusted=trusted)
    result,jid,_=b.accept(TRUSTED,10,10,URL)
    assert result==('accepted' if trusted else 'blocked')
    assert (jid is not None)==trusted
    assert store.rows('SELECT * FROM bans_v1')==before_bans
    assert store.rows('SELECT * FROM attempts')==before_attempts


def test_adding_trust_after_ban_keeps_received_update_deduplication(store,config):
    b=broker(store,config,trusted=False)
    for update in range(5):
        assert b.accept(TRUSTED,10,update,URL)[0]=='accepted'
    assert b.accept(TRUSTED,10,5,URL)[0]=='blocked'
    b=broker(Store(),config,trusted=True)  # simulate a restart with the updated config
    assert b.accept(TRUSTED,10,5,URL)[:2]==('duplicate',None)
    assert b.accept(TRUSTED,10,6,URL)[0]=='accepted'
    assert count(store,'attempts',TRUSTED)==5
    assert count(store,'bans_v1',TRUSTED)==1


@pytest.mark.parametrize('trusted',[False,True])
def test_duplicate_update_never_creates_second_job_or_attempt(store,config,trusted):
    b=broker(store,config,trusted=trusted)
    assert b.accept(TRUSTED,10,1,URL)[0]=='accepted'
    assert b.accept(TRUSTED,10,1,URL)[:2]==('duplicate',None)
    assert store.rows('SELECT count(*) n FROM jobs')[0]['n']==1
    assert store.rows('SELECT count(*) n FROM received_updates')[0]['n']==1
    assert count(store,'attempts',TRUSTED)==(0 if trusted else 1)


@pytest.mark.parametrize('trusted',[False,True])
def test_invalid_urls_still_rejected_and_deduplicated(store,config,trusted):
    b=broker(store,config,trusted=trusted)
    result,jid,error=b.accept(TRUSTED,10,1,'https://evil.example/video')
    assert result=='invalid' and jid is None and error
    assert b.accept(TRUSTED,10,1,'https://evil.example/video')[:2]==('duplicate',None)
    assert not store.rows('SELECT * FROM jobs') and not b.client.calls
    assert count(store,'attempts',TRUSTED)==(0 if trusted else 1)


@pytest.mark.parametrize('trusted',[False,True])
def test_standalone_rate_check_uses_same_trust_policy(store,config,trusted):
    for _ in range(5):
        assert store.rate_allow(TRUSTED,config['rate_limit'],now=1000)
    assert not store.rate_allow(TRUSTED,config['rate_limit'],now=1000)
    before=store.rows('SELECT * FROM bans_v1')
    users=[TRUSTED] if trusted else []
    assert Store().rate_allow(TRUSTED,config['rate_limit'],now=1001,trusted_users=users)==trusted
    assert store.rows('SELECT * FROM bans_v1')==before
    assert count(store,'attempts',TRUSTED)==5


def test_removing_trust_restores_regular_counting(store,config):
    b=broker(store,config)
    for update in range(10):
        assert b.accept(TRUSTED,10,update,URL)[0]=='accepted'
    b=broker(Store(),config,trusted=False)
    for update in range(10,15):
        assert b.accept(TRUSTED,10,update,URL)[0]=='accepted'
    assert b.accept(TRUSTED,10,15,URL)[0]=='blocked'
    assert count(store,'attempts',TRUSTED)==5


def test_trust_does_not_bypass_download_queue_limit(store,config):
    b=broker(store,config)
    jobs=[]
    for update in range(2):
        _,jid,_=b.accept(TRUSTED,10,update,URL)
        store.update(jid,phase='card',message_id=100+update)
        jobs.append(jid)
    assert store.select(jobs[0],TRUSTED,10,100,'video',1)
    with pytest.raises(OverflowError):
        store.select(jobs[1],TRUSTED,10,101,'video',1)
