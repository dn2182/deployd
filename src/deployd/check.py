"""`deployd check`: validate runtime configuration without starting the service."""

import argparse
import os
import shutil
import sys
from pathlib import Path

from .config import AppSpec, get_app_github_token, get_app_registry, get_app_secret, get_settings
from .worker import directory_layout


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _writable_dir(path: Path) -> bool:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return os.access(probe, os.W_OK)


def _command_resolves(command: list[str] | None) -> bool:
    if not command:
        return True
    executable = command[0]
    if os.sep in executable or (os.altsep and os.altsep in executable):
        return Path(executable).is_file() and os.access(executable, os.X_OK)
    return shutil.which(executable) is not None


def _check_app(name: str, spec: AppSpec, report: Report) -> None:
    label = f"app {name}"
    secret = get_app_secret(name)
    if not secret:
        report.error(f"{label}: no signing secret configured")
    elif len(secret.encode()) < 32:
        report.error(f"{label}: signing secret is shorter than 32 bytes")
    if not _writable_dir(spec.releases_dir):
        report.error(f"{label}: releases_dir {spec.releases_dir} is not writable")
    if spec.release_layout == "symlink" and not _writable_dir(spec.current_link.parent):
        report.error(f"{label}: cannot write current link in {spec.current_link.parent}")
    if spec.release_layout == "directory":
        if sys.platform not in ("linux", "darwin"):
            report.error(f"{label}: directory layout needs Linux or macOS")
        elif spec.releases_dir.is_dir():
            try:
                directory_layout.records(spec)
            except (OSError, ValueError, RuntimeError) as exc:
                report.error(f"{label}: {exc}")
    for what, command in (
        ("restart", spec.restart.command),
        ("migrate", spec.migrate.command),
        ("before_cutover hook", spec.hooks.before_cutover),
        ("after_health hook", spec.hooks.after_health),
    ):
        if not _command_resolves(command):
            report.error(f"{label}: {what} executable not found: {command[0]}")
    if spec.github_repository and not get_app_github_token(name):
        report.warn(f"{label}: no GitHub token; only public release assets will download")
    if not spec.health.url:
        report.warn(
            f"{label}: no health URL, HTTP availability is not checked; command-failure rollback remains enabled"
        )
    if spec.frozen:
        report.warn(f"{label}: frozen, CI deploys are rejected")


def run_checks() -> Report:
    report = Report()
    try:
        settings = get_settings()
    except Exception as exc:
        report.error(f"settings: {exc}")
        return report
    if not settings.admin_token:
        report.warn("DEPLOYD_ADMIN_TOKEN is empty; the management API is disabled")
    if not _writable_dir(settings.db_path.parent):
        report.error(f"state directory {settings.db_path.parent} is not writable")
    if settings.db_path.exists():
        if not settings.db_path.is_file() or not os.access(settings.db_path, os.R_OK | os.W_OK):
            report.error(f"state database {settings.db_path} is not a readable, writable file")
    else:
        report.warn("state database does not exist; service startup will create it")
    if settings.secrets_file.exists():
        mode = settings.secrets_file.stat().st_mode & 0o777
        if os.name != "nt" and mode & 0o077:
            report.error(f"secrets file {settings.secrets_file} is readable by others ({mode:o})")
    try:
        registry = get_app_registry()
    except Exception as exc:
        report.error(f"apps config: {exc}")
        return report
    if not registry:
        report.warn("no applications registered")
    for name, spec in registry.items():
        _check_app(name, spec, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deployd check")
    parser.add_argument("--quiet", action="store_true", help="print only problems")
    args = parser.parse_args(argv)
    report = run_checks()
    for message in report.errors:
        print(f"ERROR   {message}")
    for message in report.warnings:
        print(f"WARNING {message}")
    if not args.quiet:
        print(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return 1 if report.errors else 0
