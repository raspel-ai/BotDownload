import copy
from pathlib import Path
import pytest
from bot.config import validate,ConfigurationError
from bot.isolation import require_isolation,IsolationError
from bot.tool import options,formatter

@pytest.mark.parametrize('change',[
 lambda c:c['storage'].update(keep_downloaded_files='false'),
 lambda c:c['storage'].update(files_dir='../data'),
 lambda c:c['storage'].update(download_dir='old'),
 lambda c:c['telegram'].update(api_mode='auto'),
 lambda c:c['telegram']['api_base_urls'].update(cloud='https://evil.example'),
 lambda c:c['worker'].update(socket='/tmp/unsafe.sock'),
 lambda c:c['limits'].update(max_pending_tasks=True),
])
def test_invalid_config(config,change):
    change(config)
    with pytest.raises(ConfigurationError):validate(config)

def test_isolation_fail_closed(config):
    with pytest.raises(IsolationError):require_isolation(config)

def test_tool_service_options():
    o=options()
    assert o['noplaylist'] and not o['remote_components'] and not o['cachedir']
    assert o['allowed_extractors']==['youtube','tiktok','instagram']
    f={'format_id':'audio','vcodec':'none','acodec':'aac','language':'ru','abr':100}
    ctx={'formats':[f,{'format_id':'en','vcodec':'none','acodec':'aac','language':'en','abr':200}]}
    assert list(formatter('audio')(ctx))[0]['format_id']=='audio'
