from pathlib import Path

import pytest
from pydantic import ValidationError

from deployd.config import AppSpec, ArtifactRules, Settings, validate_app_name


def test_github_token_is_redacted(monkeypatch):
    monkeypatch.setenv("DEPLOYD_GITHUB_TOKEN", "github_pat_test_secret")
    settings = Settings()
    assert settings.github_token.get_secret_value() == "github_pat_test_secret"
    assert "github_pat_test_secret" not in repr(settings)
    assert "github_pat_test_secret" not in settings.model_dump_json()


def app_spec(tmp_path, **overrides):
    data = {
        "releases_dir": tmp_path / "releases",
        "current_link": tmp_path / "current",
        "artifact": {"allowed_url_prefix": "https://github.com/acme/"},
        "restart": {"command": ["true"]},
        "health": {"url": "http://127.0.0.1:8000/healthz"},
    }
    data.update(overrides)
    return AppSpec.model_validate(data)


def test_artifact_url_matches_parsed_origin_and_path():
    rules = ArtifactRules.model_validate(
        {
            "allowed_url_prefix": "https://github.com/acme/",
            "allowed_redirect_hosts": ["release-assets.githubusercontent.com"],
        }
    )
    assert rules.allows_initial_url("https://github.com/acme/app/releases/a.zip")
    assert not rules.allows_initial_url("https://github.com.evil/acme/app.zip")
    assert not rules.allows_initial_url("https://github.com/other/app.zip")
    assert rules.allows_redirect_url("https://release-assets.githubusercontent.com/a.zip")
    assert not rules.allows_redirect_url("https://127.0.0.1/a.zip")


def test_ambiguous_artifact_prefix_is_rejected():
    with pytest.raises(ValidationError, match="end with"):
        ArtifactRules(allowed_url_prefix="https://example.com/releases")


def test_managed_paths_must_be_safe_and_absolute(tmp_path):
    with pytest.raises(ValidationError, match="absolute"):
        app_spec(tmp_path, releases_dir=Path("relative/releases"))
    with pytest.raises(ValidationError, match="outside"):
        app_spec(tmp_path, current_link=tmp_path / "releases" / "current")


def test_runtime_bounds_are_validated(tmp_path):
    with pytest.raises(ValidationError, match="between 1 and 100"):
        app_spec(tmp_path, keep_releases=0)
    with pytest.raises(ValidationError, match="too_short"):
        app_spec(tmp_path, restart={"command": []})


def test_retention_choice_and_legacy_conversion(tmp_path):
    assert app_spec(tmp_path).keep_previous is None
    assert app_spec(tmp_path, keep_previous=0).keep_previous == 0
    assert app_spec(tmp_path, keep_previous=3).keep_previous == 3
    assert app_spec(tmp_path, keep_releases=5).keep_previous == 4
    assert app_spec(tmp_path, keep_releases=5, keep_previous=None).keep_previous is None
    assert "keep_releases" not in app_spec(tmp_path, keep_releases=5).model_dump()
    for invalid in (-1, 100):
        with pytest.raises(ValidationError):
            app_spec(tmp_path, keep_previous=invalid)


@pytest.mark.parametrize("name", ["app", "a", "app-1"])
def test_valid_app_names(name):
    assert validate_app_name(name) == name


@pytest.mark.parametrize("name", ["Bad", "with space", "a" * 65, "under_score", "app-"])
def test_invalid_app_names(name):
    with pytest.raises(ValueError, match="app name"):
        validate_app_name(name)
