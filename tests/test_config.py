import pytest
from bot.config import ConfigurationError, SCHEMA, _validate


def sample(schema):
    return {k: sample(v) if isinstance(v, dict) else ([] if v is list else ("local" if v is str else 1)) for k, v in schema.items()}


def test_schema_valid():
    _validate(sample(SCHEMA), SCHEMA)


def test_missing_required():
    data = sample(SCHEMA)
    del data["telegram"]["api_mode"]
    with pytest.raises(ConfigurationError, match="telegram.api_mode"):
        _validate(data, SCHEMA)


def test_reject_boolean_as_integer():
    data = sample(SCHEMA)
    data["limits"]["daily_download_limit"] = True
    with pytest.raises(ConfigurationError, match="limits.daily_download_limit"):
        _validate(data, SCHEMA)


def test_reject_unknown():
    data = sample(SCHEMA)
    data["unexpected"] = 1
    with pytest.raises(ConfigurationError, match="неизвестный"):
        _validate(data, SCHEMA)
