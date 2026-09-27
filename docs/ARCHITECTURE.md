# Architecture

This document describes how curated-images works: the recipe-authoring process, the
automated build/scan/publish pipeline, the standing constraints every recipe follows, and
the open questions that aren't settled yet. See `README.md` for a one-line project summary
and `docs/THREAT_MODEL.md` for the threat model and scanning design.

## Status

At the time of writing, `master` contains only `README.md` and `LICENSE` — no `recipes/`,
GitHub Actions workflows, or scanning pipeline exist yet. Check the repository tree itself
for the current state; this section describes what existed when this document was written,
not a live status.

Three prototype branches exist, each validating one specific build pattern before any real
recipe is written, none merged into `master`:

- `prototype/nginx-hardened-build` — a compiled C project (nginx) built from source,
  running as a fixed non-root identity with a read-only root filesystem.
- `prototype/mealie-hardened-build` — a Python/Node application stack whose own upstream
  image assumes it needs root at startup; validates that the same non-root, read-only
  pattern applies to a more complex build.
- `prototype/technitium-hardened-build` — a service that needs to bind a privileged port
  (53) as a non-root user, using a build-time capability grant rather than root.

All three were validated only under Docker's default `runc` runtime, not under `runsc`
(gVisor). gVisor validation is a requirement for future prototypes and recipes (see
below), but these three are treated as closed and are not being retroactively re-tested.

## Design constraints

1. **No self-hosted GitHub Actions runners, ever.** This is a public repo; only
   GitHub-hosted runners are used, for both building and scanning. Nothing in this
   pipeline runs anywhere outside GitHub Actions — whatever deploys these images is a
   pure consumer, not part of the pipeline.
2. **Fixed non-root identity: UID:GID 65532:65532, username `nonroot`** (no home directory,
   no login shell). This is the Google-distroless/gVisor `nonroot` convention — UID and
   username both — and is a repo-wide invariant, not a per-recipe configurable value. A
   distroless base already ships this exact account; other bases (e.g. `slim`) must create
   it explicitly (`groupadd -g 65532 nonroot && useradd -u 65532 -g nonroot ... nonroot`).
3. **Read-only root filesystem**, with explicit, narrow writable paths only (a tmpfs mount
   for `/tmp`, a volume for the application's own data directory).
4. **Distroless by default, gVisor (`runsc`) tested by default**, for every new prototype
   and recipe. Both are hard-attempt requirements: if either isn't achievable for a given
   project, the recipe documents why, rather than silently skipping it.
5. **Multi-stage builds.** Compilers, package managers, and build caches never end up in
   the runtime image.
6. **Explicit upstream pinning** via `recipe.yaml`'s tracking mode (see schema below) —
   never build against an untracked moving target. A recipe records *how* to find the
   latest version automatically (release/tag tracking preferred), not a version number
   that has to be bumped by hand.
7. **Image tags mirror upstream's exact released version string, and are mutable.** A
   rebuild triggered only by a base-image change (not an upstream version bump) reuses
   the same tag and simply overwrites it — **no build-revision suffix**. Decided
   2026-09-26: a tag was never the reproducibility guarantee here: `GITHUB_TOKEN`-signed
   build provenance and the exact digest are. Anyone who genuinely needs "the same image
   every time" pins the digest (`ghcr.io/<owner>/curated-technitium@sha256:...`), not the
   tag — a tag overwrite is invisible to a digest pin. A suffix scheme would only protect
   against a problem digest-pinning already solves, for a cost (bookkeeping, `.state.json`
   complexity) with no compensating benefit.
8. **Terminology discipline** (full detail in `docs/THREAT_MODEL.md`): "quarantine, staging,
   policy passed, promoted" — never "clean, safe, trusted." A pass means "no known
   CVE/secret/policy violation found," not a safety guarantee.
9. **Promotion operates on the exact digest that was scanned, never a mutable tag.**
   Mechanically: build each platform locally on the runner as an OCI-layout directory
   (nothing pushed yet), scan it directly, and only on a pass, push that platform under
   an arch-suffixed tag — never the recipe's real tag. Once every platform a recipe
   declares has independently passed, a separate step assembles and pushes the real,
   user-facing multi-arch manifest tag from those exact scanned digests. A failed scan on
   any platform means no manifest for that recipe this run.
10. **GHCR only, not Docker Hub.** `GITHUB_TOKEN` can push without a stored long-lived
    credential, there's no pull-rate-limiting for public images, and GitHub's build
    provenance attaches natively.
11. **Repo and images are both public, deliberately.** Nothing secret is ever allowed
    inside an image. External contributions/PRs are not accepted — this repo is
    solo-maintained (see `CONTRIBUTING.md`).
12. **Least-privilege GitHub Actions permissions per job.** A public repo's `GITHUB_TOKEN`
    defaults to read-only, so only the step that actually publishes gets
    `packages: write`.

## The two-phase workflow

**Phase 1 — recipe authoring.** Happens once per project, offline, before any automation
runs. Read the upstream project's own Dockerfile/build docs/CI config to learn its real
build steps. Prototype it in a disposable branch: get it running as the fixed non-root
identity, read-only rootfs, attempt a distroless base, test under `runsc`, and document
whatever doesn't fit and why. Once it works, write the real `recipes/<name>/Dockerfile` +
`recipe.yaml`. The exact bar for "prototype done, ready to become a recipe" beyond
distroless/gVisor validation is still a judgment call (see "Open questions" below).

**Phase 2 — the CI mechanism.** Generic and mechanical, runs indefinitely once Phase 1
exists. One shared, reusable GitHub Actions workflow, not one file per recipe:

- **Discovery job**: loops over every `recipes/*/recipe.yaml`, resolves each project's
  current upstream version per its `tracking.mode` (via the GitHub API — no image build
  needed just to check), and rebuilds a recipe if *either* that resolved version *or* a
  hash of the recipe's own tracked files (`Dockerfile`, `recipe.yaml`, `rootfs/*`) differs
  from what `.state.json` (machine-written, never hand-edited) last recorded — comparing
  the upstream version alone would silently never rebuild a recipe-only fix (e.g. a
  Dockerfile bug fix) whose upstream version hasn't moved. A manual `workflow_dispatch`
  run also accepts a `force` input (`all`, or a comma-separated recipe name list) to
  rebuild regardless of either check, for cases neither one catches on its own (e.g. only
  the base image changed, with no tracked-file diff and no upstream version bump).
- **Build job**, matrixed over that list *and* each recipe's declared platforms (one leg
  per recipe-platform pair, since a multi-platform manifest can't be scanned as a single
  locally-loaded image): `docker build` locally on the runner (image not pushed yet) →
  scan the local single-platform image (Trivy + Dockle, see `docs/THREAT_MODEL.md`) →
  only on a pass, push that platform under an arch-suffixed tag. A separate job then
  assembles and pushes the real, multi-arch manifest tag for a recipe only once *every*
  platform it declares has independently passed — a partial platform failure means no
  manifest at all for that recipe this run, and `.state.json` is updated only then.
- A failed build or failed scan means that recipe's job simply fails. Nothing gets pushed,
  and the previously-promoted image is untouched — see "When an upstream project changes
  and the build breaks" below.
- This scales the same at 3 recipes or 30 — the discovery step is cheap regardless of
  count, and only recipes that actually changed get the expensive build treatment.

## `recipe.yaml` schema

```yaml
name: mealie
description: Mealie recipe manager, compiled from source, fixed non-root identity

upstream:
  repository: https://github.com/mealie-recipes/mealie.git
  tracking:
    mode: github_release   # github_release | git_tag_pattern | branch
    # github_release  -> resolved via GitHub's Releases API, newest release wins
    # git_tag_pattern -> for projects that tag but don't use GitHub Releases
    #                    formally (e.g. nginx tags like `release-1.27.3`);
    #                    needs a `pattern:` field
    # branch          -> for projects with no release/tag practice at all;
    #                    needs a `branch:` field, tracks HEAD

image:
  name: ghcr.io/<owner>/curated-mealie   # ghcr.io/<owner>/curated-<name> confirmed as
                                          # the actual convention (recipes/technitium
                                          # uses ghcr.io/zbalint/curated-technitium);
                                          # <owner> is a real value per repo, not a
                                          # literal placeholder left in recipe.yaml
  platforms:
    - linux/amd64                        # declare every platform this recipe actually
                                          # needs; the CI mechanism builds and scans
                                          # each declared platform independently and
                                          # only assembles the multi-arch manifest once
                                          # every one of them has passed (see below)

build:
  dockerfile: Dockerfile
  args:
    MEALIE_VERSION: "${resolved_ref}"   # workflow injects whatever the
                                        # discovery step resolved this run

runtime:
  base: slim              # distroless | slim | other
  base_exception_reason: >
    <required if base != distroless: why not, what would be needed>
  gvisor_tested: false
  gvisor_exception_reason: >
    <required if gvisor_tested == false: why not, what's needed>
```

`recipes/<name>/.state.json` is separate, machine-written only:

```json
{
  "last_built_ref": "v3.2.1",
  "last_built_recipe_hash": "sha256 of every tracked file under this recipe's own directory",
  "last_built_digest": "sha256:...",
  "last_built_at": "2026-09-26T00:00:00Z"
}
```

UID:GID 65532 is deliberately **not** a `recipe.yaml` field — it's a fixed, repo-wide
invariant (constraint 2 above), not a per-recipe knob.

## When an upstream project changes and the build breaks

This will happen — upstream restructures their build system, renames a directory the
Dockerfile assumes exists, changes their entrypoint's runtime requirements, etc.

- The scheduled run's build/scan job for that recipe fails. GitHub Actions marks it red;
  a scheduled-workflow failure triggers GitHub's own default email notification.
- **The previously-promoted image is never touched.** Since nothing is pushed to GHCR
  until build and scan both pass, a failure just means this run didn't produce a new
  image — consumers keep using whatever was last successfully built and promoted. No
  silent regression, no partial or broken state ever reaches GHCR.
- `.state.json`'s `last_built_ref` doesn't update on a failure, so the next scheduled run
  detects the same "new version available" state and tries again, failing (and notifying)
  again, until the recipe is fixed.
- Fixing it is manual work — someone has to look at what upstream actually changed and
  update the recipe's Dockerfile. This is ordinary maintenance for a project that tracks
  upstream automatically, not a pipeline defect.
- Worth adding once there are enough recipes that email alone gets noisy: filing a GitHub
  Issue automatically on failure instead of relying solely on email — more discoverable,
  and could auto-close when a later run succeeds. Not needed at today's scale.

## Open questions

These aspects of the design are not yet finalized:

- The full prototype-to-recipe graduation bar beyond "distroless attempted + gVisor
  tested."
- **GHCR image-naming convention confirmed as `ghcr.io/<owner>/curated-<name>`**
  (`recipes/technitium` uses `ghcr.io/zbalint/curated-technitium`) — but `<owner>` is a
  real value each `recipe.yaml` must set, never left as a literal placeholder: an
  unsubstituted `<owner>` string is not a valid Docker reference component, so a
  `docker push` against it fails immediately (invalid reference format) rather than
  just "not being public yet."
- No rebuild trigger currently exists for a **base-image-only** change (the base image's
  tag gets a new digest upstream, with no change to this recipe's own tracked files and
  no upstream *application* version bump) — the `workflow_dispatch` `force` input covers
  this manually, but nothing detects it automatically yet.
- **GHCR package visibility**: a package pushed via `GITHUB_TOKEN` does not inherit the
  source repo's public visibility automatically — it defaults to private regardless, and
  someone has to manually flip it to public in that package's own Settings page (a
  one-time, irreversible action per package). Action item for whoever runs the first
  successful publish of each recipe, not something the pipeline can do for itself.
- Whether and when to add automatic issue-filing on build failure.
