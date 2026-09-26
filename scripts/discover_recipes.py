#!/usr/bin/env python3
"""Discovery step for the Phase 2 CI mechanism (docs/ARCHITECTURE.md).

For every recipes/*/recipe.yaml: resolves the current upstream version per its
tracking.mode, and rebuilds if either that version or the recipe's own tracked
files (Dockerfile, recipe.yaml, rootfs/*) changed since recipes/<name>/.state.json
was last written (missing file == needs a build). This second check exists so a
recipe-only fix (e.g. a Dockerfile bug fix) triggers a rebuild even when upstream's
version hasn't moved -- comparing resolved_ref alone would silently skip it forever.
FORCE_REBUILD (env var, from the workflow's workflow_dispatch input) overrides both
checks: "all" forces every recipe, or a comma-separated list of recipe names forces
just those.

Run standalone for local testing: python3 scripts/discover_recipes.py
In CI, output is also written to $GITHUB_OUTPUT (`recipes`, `build_matrix`,
`has_work`) when $GITHUB_OUTPUT is set.
"""

import hashlib
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


def load_state(recipe_dir: Path) -> dict:
    state_path = recipe_dir / ".state.json"
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text())


def recipe_content_hash(recipe_dir: Path) -> str:
    """Hash of every tracked file under this recipe's own directory (Dockerfile,
    recipe.yaml, rootfs/*, ...) except .state.json itself. Changing any of these
    with no upstream version bump must still trigger a rebuild -- comparing
    resolved_ref alone would silently skip a recipe-only fix forever."""
    hasher = hashlib.sha256()
    for path in sorted(p for p in recipe_dir.rglob("*") if p.is_file() and p.name != ".state.json"):
        hasher.update(str(path.relative_to(recipe_dir)).encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def parse_force(raw: str) -> tuple[bool, set[str]]:
    raw = raw.strip().lower()
    if not raw:
        return False, set()
    if raw == "all":
        return True, set()
    return False, {name.strip() for name in raw.split(",") if name.strip()}


def discover() -> list[dict]:
    force_all, force_names = parse_force(os.environ.get("FORCE_REBUILD", ""))

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
        recipe_hash = recipe_content_hash(recipe_dir)

        state = load_state(recipe_dir)
        unchanged = (
            resolved_ref == state.get("last_built_ref")
            and recipe_hash == state.get("last_built_recipe_hash")
        )
        forced = force_all or recipe["name"] in force_names
        if unchanged and not forced:
            continue

        needs_rebuild.append({
            "name": recipe["name"],
            "resolved_ref": resolved_ref,
            "recipe_hash": recipe_hash,
            "dockerfile": str((recipe_dir / recipe["build"]["dockerfile"]).relative_to(REPO_ROOT)),
            "context": str(recipe_dir.relative_to(REPO_ROOT)),
            "image_name": recipe["image"]["name"],
            "image_description": recipe["image"].get("description", "").strip(),
            "platforms": recipe["image"].get("platforms", ["linux/amd64"]),
            "build_args": substitute_build_args(recipe["build"].get("args"), resolved_ref),
        })
    return needs_rebuild


def build_matrix(recipes: list[dict]) -> list[dict]:
    """One entry per (recipe, platform) -- a multi-platform manifest can't be scanned
    as a single local --load'd image, so each platform is built and scanned on its
    own; the manifest is only assembled afterward, once every platform has passed
    independently (see publish_manifest.py)."""
    entries = []
    for recipe in recipes:
        for platform in recipe["platforms"]:
            entry = dict(recipe)
            entry["platform"] = platform
            entry["platform_slug"] = platform.replace("/", "-")
            entries.append(entry)
    return entries


def main() -> int:
    try:
        recipes = discover()
    except (ValueError, urllib.error.HTTPError, subprocess.CalledProcessError) as exc:
        print(f"discovery failed: {exc}", file=sys.stderr)
        return 1

    matrix = build_matrix(recipes)
    print(json.dumps({"recipes": recipes, "build_matrix": matrix}, indent=2))

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"recipes={json.dumps(recipes)}\n")
            f.write(f"build_matrix={json.dumps(matrix)}\n")
            f.write(f"has_work={'true' if recipes else 'false'}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
