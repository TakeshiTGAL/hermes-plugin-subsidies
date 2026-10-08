"""Load the repo root as a package (as Hermes does) and stub write guard + HERMES_HOME."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PACKAGE = "subsidies_plugin"


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def plugin():
    return sys.modules.get(PACKAGE) or _load_plugin()


@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home


@pytest.fixture(autouse=True)
def _stub_hermes_home(_isolate_hermes_home):
    """plugin_root resolves get_hermes_home() before plugin_data_dir mkdir's."""
    home = _isolate_hermes_home
    mod = types.ModuleType("hermes_constants")
    mod.get_hermes_home = lambda: home
    previous = sys.modules.get("hermes_constants")
    sys.modules["hermes_constants"] = mod
    try:
        yield mod
    finally:
        if previous is not None:
            sys.modules["hermes_constants"] = previous
        else:
            sys.modules.pop("hermes_constants", None)


@pytest.fixture(autouse=True)
def _stub_hermes_profiles():
    """Schedule needs a loaded profiles API. Tests that break it override this."""
    profiles = types.ModuleType("hermes_cli.profiles")
    profiles.get_active_profile_name = lambda: "default"
    previous_pkg = sys.modules.get("hermes_cli")
    previous_profiles = sys.modules.get("hermes_cli.profiles")
    created = False
    pkg = previous_pkg
    if pkg is None or not isinstance(pkg, types.ModuleType):
        pkg = types.ModuleType("hermes_cli")
        sys.modules["hermes_cli"] = pkg
        created = True
    previous_attr = getattr(pkg, "profiles", None)
    sys.modules["hermes_cli.profiles"] = profiles
    pkg.profiles = profiles
    try:
        yield profiles
    finally:
        if previous_profiles is not None:
            sys.modules["hermes_cli.profiles"] = previous_profiles
        else:
            sys.modules.pop("hermes_cli.profiles", None)
        if created:
            sys.modules.pop("hermes_cli", None)
        elif isinstance(pkg, types.ModuleType):
            if previous_attr is not None:
                pkg.profiles = previous_attr
            elif hasattr(pkg, "profiles"):
                delattr(pkg, "profiles")


@pytest.fixture(autouse=True)
def _stub_plugin_data_dir(_isolate_hermes_home):
    """State uses plugin_data_dir. Tests stub it onto the isolated home."""
    home = _isolate_hermes_home

    def plugin_data_dir(name: str):
        if not isinstance(name, str) or not name or "/" in name or ".." in name:
            raise ValueError(f"invalid plugin name for storage: {name!r}")
        root = home / "plugin-data" / name
        root.mkdir(parents=True, exist_ok=True)
        return root

    storage = types.ModuleType("plugins.plugin_storage")
    storage.plugin_data_dir = plugin_data_dir
    previous_plugins = sys.modules.get("plugins")
    previous_storage = sys.modules.get("plugins.plugin_storage")
    plugins_mod = previous_plugins
    created_plugins = False
    if plugins_mod is None or not isinstance(plugins_mod, types.ModuleType):
        plugins_mod = types.ModuleType("plugins")
        sys.modules["plugins"] = plugins_mod
        created_plugins = True
    sys.modules["plugins.plugin_storage"] = storage
    setattr(plugins_mod, "plugin_storage", storage)
    try:
        yield storage
    finally:
        if previous_storage is not None:
            sys.modules["plugins.plugin_storage"] = previous_storage
        else:
            sys.modules.pop("plugins.plugin_storage", None)
        if created_plugins:
            sys.modules.pop("plugins", None)
        elif previous_plugins is not None and isinstance(previous_plugins, types.ModuleType):
            if previous_storage is not None:
                setattr(previous_plugins, "plugin_storage", previous_storage)
            elif hasattr(previous_plugins, "plugin_storage"):
                delattr(previous_plugins, "plugin_storage")


@pytest.fixture(autouse=True)
def _stub_hermes_write_guard():
    previous_agent = sys.modules.get("agent")
    previous_fs = sys.modules.get("agent.file_safety")
    fake = types.ModuleType("agent")
    fake_fs = types.ModuleType("agent.file_safety")
    fake_fs.get_write_denied_error = lambda path, **kwargs: None
    sys.modules["agent"] = fake
    sys.modules["agent.file_safety"] = fake_fs
    try:
        yield
    finally:
        if previous_fs is not None:
            sys.modules["agent.file_safety"] = previous_fs
        else:
            sys.modules.pop("agent.file_safety", None)
        if previous_agent is not None:
            sys.modules["agent"] = previous_agent
        else:
            sys.modules.pop("agent", None)


@pytest.fixture
def fake_api(plugin, monkeypatch):
    class Fake:
        def __init__(self):
            self.calls = []
            self.search = fixture("search_ok.json")
            self.detail = fixture("detail_ok.json")
            self.fail_search_with = None
            self.fail_detail_with = None
            self.raise_on_search = None

        def __call__(self, path, params=None):
            self.calls.append((path, dict(params or {})))
            if path == "/subsidies":
                if self.raise_on_search is not None:
                    raise self.raise_on_search
                if self.fail_search_with is not None:
                    return self.fail_search_with
                return self.search
            if path.startswith("/subsidies/id/"):
                if self.fail_detail_with is not None:
                    return self.fail_detail_with
                return self.detail
            raise AssertionError(f"unexpected path {path}")

    fake = Fake()
    monkeypatch.setattr(plugin.client, "call", fake)
    return fake
