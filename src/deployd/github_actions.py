"""Generate repository setup files without contacting GitHub or running build commands."""

import base64
import io
import re
import zipfile
from importlib.resources import files
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GitHubActionsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pnpm", "npm", "static", "custom"] = "pnpm"
    project_dir: str = Field(default=".", max_length=200)
    output_dir: str = Field(default="dist", max_length=200)
    build_command: str = Field(default="pnpm run build", max_length=4000)
    node_version: Literal["22", "24"] = "22"
    pnpm_version: str = Field(default="10.33.0", pattern=r"^[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,3}$")
    branch: str = Field(default="main", max_length=100)
    automatic: bool = False

    @field_validator("project_dir", "output_dir")
    @classmethod
    def relative_directory(cls, value):
        if value == ".":
            return value
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./ -]*", value) or any(
            part in {"", ".", ".."} or part.startswith(".") for part in value.split("/")
        ):
            raise ValueError("use a repository-relative folder without traversal or hidden folders")
        return value

    @field_validator("branch")
    @classmethod
    def branch_name(cls, value):
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", value)
            or ".." in value
            or "//" in value
            or "@{" in value
            or any(
                part.startswith(".") or part.endswith((".", ".lock")) for part in value.split("/")
            )
            or value.endswith("/")
        ):
            raise ValueError("use a literal Git branch name, without patterns")
        return value

    @field_validator("build_command")
    @classmethod
    def literal_command(cls, value):
        if "${{" in value or any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError(
                "build commands cannot contain GitHub expressions or control characters"
            )
        return value.strip()

    @model_validator(mode="after")
    def build_required(self):
        if self.kind != "static" and not self.build_command:
            raise ValueError("a build command is required")
        if self.kind != "static" and self.output_dir == ".":
            raise ValueError("build into a dedicated output folder, not the project root")
        return self


class _WorkflowDumper(yaml.SafeDumper):
    pass


def _yaml_string(dumper, value):
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None
    )


_WorkflowDumper.add_representer(str, _yaml_string)


ENVIRONMENT_HINT = (
    "    # environment: production   # optional: require reviewers before publishing and deploying"
)


def render_workflow(name: str, settings: GitHubActionsSettings, *, static_site: bool) -> str:
    template = files("deployd").joinpath("templates/deploy.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(template)
    workflow["name"] = f"Deploy {name}"
    if settings.automatic:
        workflow["on"]["push"] = {"branches": [settings.branch]}
    workflow["concurrency"]["group"] = f"deploy-{name}"
    workflow["env"] = {
        "APP_NAME": name,
        "PROJECT_DIR": settings.project_dir,
        "OUTPUT_DIR": "." if settings.kind == "static" else settings.output_dir,
    }
    build = workflow["jobs"]["build"]
    build["if"] = f"github.ref == 'refs/heads/{settings.branch}'"
    steps = build["steps"]
    build_steps = []
    if settings.kind in {"pnpm", "npm"}:
        if settings.kind == "pnpm":
            build_steps.append(
                {
                    "uses": "pnpm/action-setup@ea17c68df8912ef543352723c149a84f56e3d413",
                    "with": {"version": settings.pnpm_version},
                }
            )
        build_steps.extend(
            [
                {
                    "uses": "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
                    "with": {"node-version": settings.node_version},
                },
                {
                    "name": "Install dependencies",
                    "working-directory": settings.project_dir,
                    "run": "pnpm install --frozen-lockfile"
                    if settings.kind == "pnpm"
                    else "npm ci",
                },
            ]
        )
    if settings.kind != "static":
        build_steps.append(
            {
                "name": "Build",
                "working-directory": settings.project_dir,
                "run": settings.build_command,
            }
        )
    if static_site or settings.kind == "static":
        build_steps.append(
            {
                "name": "Check static entry point",
                "run": 'test -s "$PROJECT_DIR/$OUTPUT_DIR/index.html"',
            }
        )
    insert_at = next(
        i for i, step in enumerate(steps) if step.get("name") == "Check artifact contents"
    )
    steps[insert_at:insert_at] = build_steps
    text = yaml.dump(workflow, Dumper=_WorkflowDumper, sort_keys=False, width=1000)
    # PyYAML drops comments, so the optional environment hint is re-inserted under the publish job.
    lines = text.splitlines()
    publish_at = lines.index("  publish:")
    needs_at = next(i for i in range(publish_at, len(lines)) if lines[i] == "    needs: build")
    lines.insert(needs_at + 1, ENVIRONMENT_HINT)
    return "\n".join(lines) + "\n"


def setup_bundle(name: str, repository: str, settings: GitHubActionsSettings, *, static_site: bool):
    workflow_text = render_workflow(name, settings, static_site=static_site)
    instructions = f"""# GitHub Actions setup: {name}

Repository: https://github.com/{repository}

1. Review and copy .github/workflows/deploy.yml and scripts/notify_deploy.py
   into your repository root. Do not overwrite an existing workflow blindly;
   merge or replace it deliberately to avoid duplicate deployments.
2. In Settings > Secrets and variables > Actions, add repository variable
   DEPLOYD_URL (the public HTTPS deployd API address shown in management), and
   repository secret DEPLOYD_SECRET (this app's signing secret, not the admin token).
   No secret values are included in this download.
3. Commit and push the files. Manual runs appear after the workflow is on the
   repository's default branch. In Actions, choose Deploy {name}, Run workflow,
   and select {settings.branch}.

Trigger: {"push to " + settings.branch + " and manual" if settings.automatic else "manual only"}.
Only {settings.branch} can deploy. First verify a manual deployment before opting
into push deployment. Downloading or saving settings does not update GitHub;
commit the newly generated workflow whenever these settings change.

Build commands run in GitHub Actions, never on the deployd server. Node presets
install locked dependencies; custom commands must install their own toolchains.
Only the selected output folder is packaged. For plain HTML, the source folder
is the output. Keep backend code and private files outside that folder.
Packaging rejects symlinks, hidden files, node_modules and common private-key
files. Review your output for other sensitive content before deploying.

The build job runs read-only and uploads the packaged artifact. The publish job
creates a commit-specific release asset using github.token with Contents: write,
notifies deployd, and prunes deploy-{name}-* releases beyond the newest 10.
For private repositories, configure a read-only GitHub token in deployd. No
GitHub write token or pull-request integration is needed in deployd.
An existing artifact is never overwritten; a different build for the same commit
fails. Push a new commit if build inputs or artifacts need to change.
Run the workflow manually with dry_run checked to build and package without
publishing. Uncomment the environment line in the publish job to require
reviewers before anything is published or deployed.

This does not change Nginx or connect your local site path. Verify the first
release and web-server permissions before the initial site cutover.
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(".github/workflows/deploy.yml", workflow_text)
        archive.writestr(
            "scripts/notify_deploy.py",
            files("deployd").joinpath("templates/notify_deploy.py.txt").read_bytes(),
        )
        archive.writestr("DEPLOYD-SETUP.md", instructions)
    return {
        "filename": f"{name}-github-actions.zip",
        "content_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
    }
