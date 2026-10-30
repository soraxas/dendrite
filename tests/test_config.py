"""Which backend a Lab resolves to: arguments, environment, dendrite.toml, or local."""

import pytest

from dendrite.config import Settings, find_project_file, write_project_file

ENV = ("DENDRITE_DB_URL", "DENDRITE_STORE_URL", "DENDRITE_SERVER", "DENDRITE_PROJECT", "DENDRITE_HOME")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_no_configuration_is_local(clean_env):
    s = Settings.resolve()
    assert not s.hosted and s.source == "local"
    assert s.db_url == f"sqlite:///{clean_env / '.dendrite' / 'lineage.db'}"


def test_project_file_found_upwards(clean_env):
    write_project_file(clean_env, "https://lineage.team/", "recon")
    nested = clean_env / "pipelines" / "deep"
    nested.mkdir(parents=True)
    path, conf = find_project_file(nested)
    assert path == clean_env / "dendrite.toml" and conf["server"]["project"] == "recon"
    assert conf == {"server": {"url": "https://lineage.team", "project": "recon"}}  # nothing secret in it

    import os

    os.chdir(nested)
    s = Settings.resolve()
    assert s.hosted and s.server == "https://lineage.team" and s.project == "recon"
    assert s.source == str(clean_env / "dendrite.toml")


def test_environment_beats_the_file(clean_env, monkeypatch):
    write_project_file(clean_env, "https://lineage.team", "recon")
    monkeypatch.setenv("DENDRITE_DB_URL", "sqlite:///x.db")
    s = Settings.resolve()
    assert not s.hosted and s.db_url == "sqlite:///x.db" and s.source == "environment"

    monkeypatch.delenv("DENDRITE_DB_URL")
    monkeypatch.setenv("DENDRITE_SERVER", "https://other.team")
    monkeypatch.setenv("DENDRITE_PROJECT", "splats")
    s = Settings.resolve()
    assert (s.server, s.project, s.source) == ("https://other.team", "splats", "environment")


def test_arguments_beat_everything(clean_env, monkeypatch):
    monkeypatch.setenv("DENDRITE_SERVER", "https://other.team")
    monkeypatch.setenv("DENDRITE_PROJECT", "splats")
    assert not Settings.resolve(db_url="sqlite:///y.db").hosted
    s = Settings.resolve(server="https://mine.team", project="p")
    assert (s.server, s.project, s.source) == ("https://mine.team", "p", "arguments")


def test_server_without_project_is_an_error(clean_env, monkeypatch):
    monkeypatch.setenv("DENDRITE_SERVER", "https://lineage.team")
    with pytest.raises(ValueError, match="no project"):
        Settings.resolve()
    with pytest.raises(ValueError, match="must be a URL"):
        Settings.resolve(server="lineage.team", project="p")


def test_init_writes_the_project_file(clean_env):
    from dendrite.cli import main

    main(["init", "--server", "https://lineage.team", "--project", "recon"])
    assert find_project_file()[1] == {"server": {"url": "https://lineage.team", "project": "recon"}}
    with pytest.raises(SystemExit):
        main(["init", "--server", "https://lineage.team", "--project", "recon"])  # refuses to overwrite
