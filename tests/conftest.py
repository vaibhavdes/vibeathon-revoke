import importlib.util
import sys

import pytest
from fastapi.testclient import TestClient

from agent.config import ROOT


def _load(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def peoplehub(tmp_path):
    module = _load("peoplehub_app", ROOT / "systems/peoplehub/main.py")
    module.STATE_FILE = tmp_path / "peoplehub_state.json"   # never touch the running service's state
    return TestClient(module.app)


@pytest.fixture
def ledgerly(tmp_path):
    module = _load("ledgerly_app", ROOT / "systems/ledgerly/main.py")
    module.STATE_FILE = tmp_path / "ledgerly_state.json"
    return TestClient(module.app)
