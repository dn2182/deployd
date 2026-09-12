import base64
import io
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from deployd.github_actions import GitHubActionsSettings, setup_bundle


def bundle(**settings):
    result = setup_bundle(
        "bluedatos", "acme/site", GitHubActionsSettings(**settings), static_site=True
    )
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(result["content_base64"])))
    return archive, yaml.safe_load(archive.read(".github/workflows/deploy.yml"))


@pytest.mark.parametrize("kind", ["pnpm", "npm", "static", "custom"])
def test_generated_presets(kind):
    archive, workflow = bundle(kind=kind, project_dir="frontend", build_command="echo build")
    assert set(archive.namelist()) == {
        ".github/workflows/deploy.yml",
        "scripts/notify_deploy.py",
        "DEPLOYD-SETUP.md",
    }
    assert (
        archive.read("scripts/notify_deploy.py")
        == (Path(__file__).parents[1] / "examples/notify_deploy.py").read_bytes()
    )
    assert workflow["on"] == {"workflow_dispatch": {}}
    job = workflow["jobs"]["deploy"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["env"]["APP_NAME"] == "bluedatos"
    assert job["env"]["PROJECT_DIR"] == "frontend"
    assert job["env"]["OUTPUT_DIR"] == ("." if kind == "static" else "dist")
    steps = job["steps"]
    assert steps[0]["with"]["persist-credentials"] is False
    installs = [step for step in steps if step.get("name") == "Install dependencies"]
    assert len(installs) == (1 if kind in {"pnpm", "npm"} else 0)
    builds = [step for step in steps if step.get("name") == "Build"]
    assert len(builds) == (0 if kind == "static" else 1)
    if builds:
        assert builds[0]["working-directory"] == "frontend"
        assert builds[0]["run"] == "echo build"
    assert "${{ secrets.DEPLOYD_SECRET }}" in str(steps)
    for step in steps:
        if "uses" in step:
            assert len(step["uses"].split("@")[1]) == 40
        if "run" in step and shutil.which("bash"):
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
        if "run" in step and shutil.which("shellcheck"):
            subprocess.run(
                ["shellcheck", "--shell=bash", "-"], input=step["run"], text=True, check=True
            )


def test_automatic_is_explicit_and_scoped_to_literal_branch():
    _, workflow = bundle(automatic=True, branch="release/site")
    assert workflow["on"] == {"workflow_dispatch": {}, "push": {"branches": ["release/site"]}}
    assert workflow["jobs"]["deploy"]["if"] == "github.ref == 'refs/heads/release/site'"
    assert workflow["concurrency"] == {"group": "deploy-bluedatos", "cancel-in-progress": False}


@pytest.mark.parametrize(
    "settings",
    [
        {"project_dir": "../outside"},
        {"project_dir": "/etc"},
        {"project_dir": "a/../../b"},
        {"output_dir": "${{ secrets.TOKEN }}"},
        {"output_dir": ".env"},
        {"output_dir": "."},
        {"branch": "main' || true || '"},
        {"branch": "main\npush: {}"},
        {"branch": "release/*"},
        {"branch": "../main"},
        {"pnpm_version": "latest"},
        {"build_command": ""},
        {"build_command": "echo ${{ secrets.DEPLOYD_SECRET }}"},
        {"build_command": "echo\x00bad"},
        {"unexpected": "ignored"},
    ],
)
def test_rejects_unsafe_or_ambiguous_settings(settings):
    with pytest.raises(ValidationError):
        GitHubActionsSettings(**settings)


def test_multiline_commands_are_yaml_data_not_structure():
    command = "echo 'name: example'\nprintf '%s' \"$HOME\"\nnpm run build"
    _, workflow = bundle(build_command=command, project_dir="web client")
    build = next(
        step for step in workflow["jobs"]["deploy"]["steps"] if step.get("name") == "Build"
    )
    assert build["run"] == command
    assert build["working-directory"] == "web client"


def run_step(workflow, name, workspace, temporary, **extra):
    if not shutil.which("bash"):
        pytest.skip("generated workflow runs on Ubuntu with Bash")
    step = next(step for step in workflow["jobs"]["deploy"]["steps"] if step.get("name") == name)
    env = {
        **os.environ,
        **workflow["jobs"]["deploy"]["env"],
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_TEMP": str(temporary),
        "GITHUB_OUTPUT": str(temporary / "outputs"),
        **extra,
    }
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=workspace,
        env=env,
        text=True,
        capture_output=True,
    )


def test_artifact_preflight_rejects_private_files_and_symlinks(tmp_path):
    _, workflow = bundle(kind="static", project_dir=".")
    (tmp_path / "index.html").write_text("site")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("ignored metadata")
    assert run_step(workflow, "Check artifact contents", tmp_path, tmp_path).returncode == 0
    for filename in [".env", "private.key", "id_rsa", "node_modules"]:
        path = tmp_path / filename
        path.write_text("private")
        assert run_step(workflow, "Check artifact contents", tmp_path, tmp_path).returncode != 0
        path.unlink()
    try:
        (tmp_path / "linked.html").symlink_to(tmp_path / "index.html")
    except OSError:
        pytest.skip("symlinks unavailable")
    assert run_step(workflow, "Check artifact contents", tmp_path, tmp_path).returncode != 0


def test_package_is_repeatable_readable_and_excludes_git(tmp_path):
    if not shutil.which("tar") or "GNU tar" not in subprocess.check_output(
        ["tar", "--version"], text=True
    ):
        pytest.skip("GNU tar packaging is tested on Linux CI")
    source, temporary = tmp_path / "repo", tmp_path / "runner"
    source.mkdir()
    temporary.mkdir()
    (source / "index.html").write_text("site")
    (source / "index.html").chmod(0o600)
    (source / "assets").mkdir(mode=0o700)
    (source / "assets/app.js").write_text("app")
    (source / ".git").mkdir()
    (source / ".git/config").write_text("private git metadata")
    _, workflow = bundle(kind="static", project_dir=".")
    assert run_step(workflow, "Package artifact", source, temporary).returncode == 0
    artifact = temporary / "deployd-artifact.tar.gz"
    first = artifact.read_bytes()
    os.utime(source / "index.html", (100, 100))
    assert run_step(workflow, "Package artifact", source, temporary).returncode == 0
    assert artifact.read_bytes() == first
    with tarfile.open(artifact) as archive:
        assert set(archive.getnames()) == {".", "./index.html", "./assets", "./assets/app.js"}
        assert archive.getmember("./index.html").mode == 0o644
        assert archive.getmember("./assets").mode == 0o755
