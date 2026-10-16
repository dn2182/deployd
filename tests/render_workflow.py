"""Render the GitHub Actions template through setup_bundle for actionlint (CI helper).

Usage: python tests/render_workflow.py OUTPUT_DIR
Writes one workflow per preset plus the automatic-push variant.
"""

import sys
from pathlib import Path

from deployd.github_actions import GitHubActionsSettings, render_workflow

PRESETS = {
    "pnpm": GitHubActionsSettings(kind="pnpm", project_dir="web"),
    "npm": GitHubActionsSettings(kind="npm", build_command="npm run build"),
    "static": GitHubActionsSettings(kind="static"),
    "custom": GitHubActionsSettings(kind="custom", build_command="make site"),
    "automatic": GitHubActionsSettings(kind="pnpm", automatic=True, branch="release/site"),
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    output = Path(argv[1]) / ".github" / "workflows"
    output.mkdir(parents=True, exist_ok=True)
    for name, settings in PRESETS.items():
        text = render_workflow(f"lint-{name}", settings, static_site=name != "custom")
        (output / f"deploy-{name}.yml").write_text(text, encoding="utf-8")
        print(output / f"deploy-{name}.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
