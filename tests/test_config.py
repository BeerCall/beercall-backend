import os
import pytest
from unittest import mock

def test_config_missing_secret():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="BEERCALL_SECRET_KEY is missing"):
            import core.config
            import importlib
            importlib.reload(core.config)

def test_config_with_secret():
    with mock.patch.dict(os.environ, {"BEERCALL_SECRET_KEY": "supersecret"}, clear=True):
        import core.config
        import importlib
        importlib.reload(core.config)
        assert core.config.SECRET_KEY == "supersecret"
