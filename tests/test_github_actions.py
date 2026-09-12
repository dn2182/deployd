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

from deployd.github_actions import (
    ENVIRONMENT_HINT,
    GitHubActionsSettings,
    render_workflow,
    setup_bundle,
)

ROOT = Path(__file__).parents[1]
EXAMPLE_WORKFLOW = ROOT / "examples/github-actions-deploy.yml"
EXAMPLE_SETTINGS = {"kind": "pnpm", "project_dir": "web", "automatic": True}


def bundle(**settings):
    result = setup_bundle(
        "bluedatos", "acme/site", GitHubActionsSettings(**settings), static_site=True
    )
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(result["content_base64"])))
    return archive, yaml.safe_load(archive.read(".github/workflows/deploy.yml"))


def steps_named(job, name):
    return [step for step in job["steps"] if step.get("name") == name]


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
        == (ROOT / "examples/notify_deploy.py").read_bytes()
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["dry_run"]["type"] == "boolean"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"] == {
        "APP_NAME": "bluedatos",
        "PROJECT_DIR": "frontend",
        "OUTPUT_DIR": "." if kind == "static" else "dist",
    }
    build, publish = workflow["jobs"]["build"], workflow["jobs"]["publish"]
    assert build["if"] == "github.ref == 'refs/heads/main'"
    assert build["permissions"] == {"contents": "read"}
    assert publish["permissions"] == {"contents": "write"}
    assert publish["needs"] == "build"
    assert publish["if"] == "success() && inputs.dry_run != true"
    assert build["steps"][0]["with"]["persist-credentials"] is False
    assert len(steps_named(build, "Install dependencies")) == (1 if kind in {"pnpm", "npm"} else 0)
    builds = steps_named(build, "Build")
    assert len(builds) == (0 if kind == "static" else 1)
    if builds:
        assert builds[0]["working-directory"] == "frontend"
        assert builds[0]["run"] == "echo build"
    build_names = [step.get("name") for step in build["steps"]]
    assert build_names.index("Check artifact contents") > build_names.index(
        "Check static entry point"
    )
    assert "Publish immutable artifact" not in build_names
    publish_names = [step.get("name") for step in publish["steps"]]
    assert publish_names[-3:] == [
        "Publish immutable artifact",
        "Deploy and wait for result",
        "Prune old deployment releases",
    ]
    assert "${{ secrets.DEPLOYD_SECRET }}" in str(publish["steps"])
    for step in build["steps"] + publish["steps"]:
        if "uses" in step:
            assert len(step["uses"].split("@")[1]) == 40
        if "run" in step:
            assert "${{" not in step["run"], "expressions must pass through env:"
        if "run" in step and shutil.which("bash"):
            subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
        if "run" in step and shutil.which("shellcheck"):
            subprocess.run(
                ["shellcheck", "--shell=bash", "-"], input=step["run"], text=True, check=True
            )


def test_environment_hint_is_rendered_as_comment():
    text = render_workflow("bluedatos", GitHubActionsSettings(), static_site=True)
    lines = text.splitlines()
    assert ENVIRONMENT_HINT in lines
    assert lines[lines.index(ENVIRONMENT_HINT) - 1] == "    needs: build"
    assert "environment" not in yaml.safe_load(text)["jobs"]["publish"]


def test_automatic_is_explicit_and_scoped_to_literal_branch():
    _, workflow = bundle(automatic=True, branch="release/site")
    assert workflow["on"]["push"] == {"branches": ["release/site"]}
    assert "inputs" in workflow["on"]["workflow_dispatch"]
    assert workflow["jobs"]["build"]["if"] == "github.ref == 'refs/heads/release/site'"
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
    build = steps_named(workflow["jobs"]["build"], "Build")[0]
    assert build["run"] == command
    assert build["working-directory"] == "web client"


def run_step(workflow, name, workspace, temporary, **extra):
    if not shutil.which("bash"):
        pytest.skip("generated workflow runs on Ubuntu with Bash")
    step = steps_named(workflow["jobs"]["build"], name)[0]
    env = {
        **os.environ,
        **workflow["env"],
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
    (tmp_path / ".well-known").mkdir()
    (tmp_path / ".well-known/security.txt").write_text("Contact: mailto:ops@example.com")
    (tmp_path / ".htaccess").write_text("Options -Indexes")
    (tmp_path / ".nojekyll").write_text("")
    assert run_step(workflow, "Check artifact contents", tmp_path, tmp_path).returncode == 0
    for filename in [".env", ".env.production", ".npmrc", ".netrc", "private.key", "id_rsa"]:
        path = tmp_path / filename
        path.write_text("private")
        result = run_step(workflow, "Check artifact contents", tmp_path, tmp_path)
        assert result.returncode != 0 and filename in result.stderr
        path.unlink()
    for dirname in [".ssh", ".aws", ".docker", "node_modules"]:
        path = tmp_path / dirname
        path.mkdir()
        assert run_step(workflow, "Check artifact contents", tmp_path, tmp_path).returncode != 0
        path.rmdir()
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
    (source / ".htaccess").write_text("Options -Indexes")
    _, workflow = bundle(kind="static", project_dir=".")
    assert run_step(workflow, "Package artifact", source, temporary).returncode == 0
    artifact = temporary / "deployd-artifact.tar.gz"
    first = artifact.read_bytes()
    os.utime(source / "index.html", (100, 100))
    assert run_step(workflow, "Package artifact", source, temporary).returncode == 0
    assert artifact.read_bytes() == first
    with tarfile.open(artifact) as archive:
        assert set(archive.getnames()) == {
            ".",
            "./index.html",
            "./assets",
            "./assets/app.js",
            "./.htaccess",
        }
        assert archive.getmember("./index.html").mode == 0o644
        assert archive.getmember("./assets").mode == 0o755


def example_workflow_text() -> str:
    """The committed example is the rendered template behind a fixed comment header."""
    header = []
    for line in EXAMPLE_WORKFLOW.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#") and line.strip():
            break
        header.append(line)
    rendered = render_workflow(
        "example-site", GitHubActionsSettings(**EXAMPLE_SETTINGS), static_site=True
    )
    return "\n".join(header) + "\n" + rendered


def test_example_workflow_is_generated_from_template():
    assert EXAMPLE_WORKFLOW.read_text(encoding="utf-8") == example_workflow_text()
