from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

@pytest.fixture
def config():
    from bot.config import load_settings
    return load_settings(Path(__file__).resolve().parents[1],secrets=False).data

@pytest.fixture
def store(tmp_path,monkeypatch,config):
    from bot.state import Store
    monkeypatch.chdir(tmp_path)
    tmp_path.chmod(0o700)
    s=Store();s.initialize(config['backup'])
    return s
