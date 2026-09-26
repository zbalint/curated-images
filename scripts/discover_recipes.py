#!/usr/bin/env python3
"""Discovery step for the Phase 2 CI mechanism (docs/ARCHITECTURE.md).

For every recipes/*/recipe.yaml: resolves the current upstream version per its
tracking.mode, compares it against recipes/<name>/.state.json's last_built_ref
(missing file == needs a build), and prints a JSON array of recipes that need
rebuilding this run -- each entry carries everything the build job needs
(dockerfile path, image name/platforms, build args with ${resolved_ref}
substituted).

Run standalone for local testing: python3 scripts/discover_recipes.py
In CI, output is also written to $GITHUB_OUTPUT as `matrix=<json>` when that
env var is set.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
GITHUB_API = "https://api.github.com"


def _github_api_get(path: str) -> dict:
    url = f"{GITHUB_API}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _owner_repo_from_url(repo_url: str) -> tuple[str, str]:
    match = re.search(r"github\.com/([^/]+)/([^/.]+?)(?:\.git)?/?$", repo_url)
    if not match:
        raise ValueError(f"cannot parse owner/repo from upstream.repository: {repo_url}")
    return match.group(1), match.group(2)


def _version_key(tag: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", tag))


def resolve_github_release(upstream: dict) -> str:
    owner, repo = _owner_repo_from_url(upstream["repository"])
    data = _github_api_get(f"/repos/{owner}/{repo}/releases/latest")
    return data["tag_name"]


def resolve_git_tag_pattern(upstream: dict) -> str:
    pattern = upstream["tracking"]["pattern"]
    out = subprocess.run(
        ["git", "ls-remote", "--tags", upstream["repository"]],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout
    tags = []
    for line in out.splitlines():
        ref = line.split("\t", 1)[-1]
        tag = ref.removeprefix("refs/tags/").removesuffix("^{}")
        if re.fullmatch(pattern, tag):
            tags.append(tag)
    if not tags:
        raise ValueError(f"no tag matching pattern {pattern!r} found for {upstream['repository']}")
    return max(tags, key=_version_key)


def resolve_branch(upstream: dict) -> str:
    branch = upstream["tracking"]["branch"]
    out = subprocess.run(
        ["git", "ls-remote", upstream["repository"], f"refs/heads/{branch}"],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout
    if not out.strip():
        raise ValueError(f"branch {branch!r} not found on {upstream['repository']}")
    return out.split()[0]


RESOLVERS = {
    "github_release": resolve_github_release,
    "git_tag_pattern": resolve_git_tag_pattern,
    "branch": resolve_branch,
}


def substitute_build_args(args: dict, resolved_ref: str) -> dict:
    return {k: v.replace("${resolved_ref}", resolved_ref) for k, v in (args or {}).items()}


def last_built_ref(recipe_dir: Path) -> str | None:
    state_path = recipe_dir / ".state.json"
    if not state_path.exists():
        return None
    return json.loads(state_path.read_text()).get("last_built_ref")


def discover() -> list[dict]:
    needs_rebuild = []
    for recipe_yaml in sorted(REPO_ROOT.glob("recipes/*/recipe.yaml")):
        recipe_dir = recipe_yaml.parent
        recipe = yaml.safe_load(recipe_yaml.read_text())
        upstream = recipe["upstream"]
        mode = upstream["tracking"]["mode"]
        resolver = RESOLVERS.get(mode)
        if resolver is None:
            raise ValueError(f"{recipe_yaml}: unknown tracking.mode {mode!r}")
        resolved_ref = resolver(upstream)

        if resolved_ref == last_built_ref(recipe_dir):
            continue

        needs_rebuild.append({
            "name": recipe["name"],
            "resolved_ref": resolved_ref,
            "dockerfile": str((recipe_dir / recipe["build"]["dockerfile"]).relative_to(REPO_ROOT)),
            "context": str(recipe_dir.relative_to(REPO_ROOT)),
            "image_name": recipe["image"]["name"],
            "platforms": recipe["image"].get("platforms", ["linux/amd64"]),
            "build_args": substitute_build_args(recipe["build"].get("args"), resolved_ref),
        })
    return needs_rebuild


def main() -> int:
    try:
        matrix = discover()
    except (ValueError, urllib.error.HTTPError, subprocess.CalledProcessError) as exc:
        print(f"discovery failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(matrix, indent=2))

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"matrix={json.dumps(matrix)}\n")
            f.write(f"has_work={'true' if matrix else 'false'}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
