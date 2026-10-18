from app.config import get_secret, load_settings, setting


def test_env_var_wins_over_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("MY_SECRET=from-file\n")
    monkeypatch.setattr("app.config.find_env_file", lambda: env_file)
    monkeypatch.setenv("MY_SECRET", "from-env")
    assert get_secret("MY_SECRET") == "from-env"


def test_env_file_parsing(monkeypatch, tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("# comment\n\nexport A=\"quoted\"\nB='single'\nC=plain=with=equals\n")
    monkeypatch.setattr("app.config.find_env_file", lambda: env_file)
    for name in "ABC":
        monkeypatch.delenv(name, raising=False)
    assert (get_secret("A"), get_secret("B"), get_secret("C")) == ("quoted", "single", "plain=with=equals")
    assert get_secret("MISSING") is None


def test_env_file_is_ignored_in_lambda(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "btr-worker")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert get_secret("OPENROUTER_API_KEY") is None


def test_settings_path_override(monkeypatch, tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("worker:\n  top_accounts: 7\n")
    monkeypatch.setenv("SETTINGS_PATH", str(path))
    settings = load_settings()
    assert setting(settings, "worker.top_accounts", 5) == 7
    assert setting(settings, "worker.missing", 5) == 5
    assert setting(settings, "agent.models.cheap", "d") == "d"


def test_repo_settings_use_openrouter_model_ids():
    models = setting(load_settings(), "agent.models", {})
    assert models and all("/" in m for m in models.values())
