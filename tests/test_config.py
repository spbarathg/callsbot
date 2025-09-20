from importlib import reload
import os
import config.config as cfg


def test_validate_required_config_missing(monkeypatch):
    monkeypatch.setenv("API_ID", "0")
    monkeypatch.setenv("API_HASH", "")
    monkeypatch.setenv("TARGET_GROUP", "")
    reload(cfg)
    try:
        cfg.validate_required_config()
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "Missing required configuration" in str(e)


