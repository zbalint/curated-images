# Threat Model

This document is the authoritative threat model and scanning/promotion design for
curated-images. If anything here conflicts with `docs/ARCHITECTURE.md`, this file wins for
security-specific claims; `docs/ARCHITECTURE.md` wins for project state/process. For how to
report a vulnerability, see `SECURITY.md` at the repository root.

## Threat model

Assume every upstream source repository this project builds from can be malicious or
compromised — not "probably fine, mostly." Specifically assume any of the following could
be hostile: npm/pip install or postinstall hooks, Makefile targets, shell scripts, build
tooling, dependencies, source code, or generated binaries.

Because of this:

- **Build-time execution only ever happens on GitHub-hosted, disposable runners** — never
  any self-hosted infrastructure, never a self-hosted runner of any kind (this repo is public; a
  self-hosted runner on a public repo is exactly the misconfiguration GitHub itself warns
  against, since a malicious contribution could execute arbitrary code on it — moot here
  since no external PRs are accepted, but the rule holds regardless).
- **The fact that an image builds successfully, or passes every configured scanner, must
  never be interpreted as proof the software is safe.** Every tool below works from known
  signatures or known vulnerability databases. A novel, deliberately-hidden backdoor
  written specifically to evade detection will pass all of them. Passing scans catch the
  overwhelming majority of *real-world* supply-chain incidents (which are almost always
  known-CVE or known-malware-signature based) — they do not, and cannot, prove absence of
  a targeted attack.

## Terminology discipline

Use precisely: **quarantine**, **staging**, **policy passed**, **promoted**. Never:
**clean**, **safe**, **trusted** — unless the meaning is narrowly and explicitly defined in
the same sentence.

- **"Promoted"** means: this exact image digest passed the currently-configured policy
  checks, at this point in time. It does not mean the software is safe, free of
  vulnerabilities, or free of malicious logic.
- **Scanning upstream's own published image** (as a before/after comparison metric, to
  show a rebuild-from-source actually helped) is in scope, using the same registry-native
  tools as everything else. One precision worth holding onto: these tools **fetch** an
  image's layer bytes to inspect installed packages — they never **execute** it. "We don't
  pull it in" is close enough in spirit to what actually matters (never execute), but the
  literal fetch does happen; don't let that get overstated as "we never touch it at all."

## The digest-based promotion invariant

Promotion decisions always operate on the exact digest that was scanned — never a mutable
tag (`latest`, a version tag). The mechanism, now that build and scan both happen in the
same GitHub Actions job (no separate external scanner — see `docs/ARCHITECTURE.md`):

```
docker build -t local:candidate .          <- image exists only locally on the runner
        |
Trivy + Dockle scan local:candidate        <- scans the local image directly, no push needed
        |
   pass?  ──── no ──→ job fails, nothing is ever pushed, nothing public exists for this run
        |
       yes
        |
docker push ghcr.io/.../<name>:<version>   <- the ONLY push, and only for a build that passed
```

This avoids the race the original design was worried about (scanning `:latest`, then
`:latest` repointing to something else before promotion) by construction — there is no
window where an unscanned or failed build has any tag or digest reachable in the public
registry at all.

## Scanning & policy checks

Tiered by cost/maturity — start with Tier 1, add the rest as bandwidth allows. All of these
run against the **local**, freshly-built image on the same runner — no push/pull
round-trip needed for any of them.

**Tier 1 — in place from the start:**

- **Trivy** (Aqua Security) — CVE scanning (OS packages + language dependencies), secret
  scanning, license scanning, and SBOM generation (CycloneDX), all in one
  actively-maintained tool. The CLI is installed directly in the workflow (not via
  `aquasecurity/trivy-action`), to minimize third-party GitHub Action trust surface for
  a task this simple. Split into three separate invocations with different roles:
  - **CVE scanning is the actual blocking gate**: `--scanners vuln --exit-code 1
    --severity CRITICAL,HIGH --ignore-unfixed`. **`--ignore-unfixed` is a deliberate
    policy, not an oversight**: an unfixed HIGH/CRITICAL (no vendor-published fix exists
    for that package version yet, in *any* upstream) does not block publication; a fixed
    one this recipe simply hasn't picked up yet does. Rationale: the alternative — block
    on any unfixed HIGH/CRITICAL regardless — means a single unpatched CVE anywhere in a
    base OS image (glibc, openssl, ...) could freeze *every* future rebuild of a recipe
    indefinitely, with nothing this project's own maintenance could do about it, while
    the previously-promoted image (already carrying the same base OS, likely the same
    CVE) just goes stale. Trivy's CVE database also can't do reachability analysis — a
    "CRITICAL, no fix" finding means "this vulnerable code exists in this binary," not
    "this application's actual code path can reach it"; that judgment call is exactly
    what a blanket blocking policy can't make and a human periodically reading the
    (still-visible, non-blocking) scan output can. Revisit if this project's own added
    packages (not the base image) are ever the source of an unfixed finding.
  - **Secret scanning is a separate, unconditional gate**: `--scanners secret
    --exit-code 1`, no severity filter and no `--ignore-unfixed` exemption — constraint
    11 ("nothing secret is ever allowed inside an image") is a hard rule, not a
    risk-tiered one.
  - **License scanning and SBOM generation are non-blocking reporting artifacts**, not a
    gate: no license policy is defined anywhere in this project (which licenses would be
    unacceptable, for instance), so gating on one would be inventing an unstated policy.
    Uploaded per-build as workflow artifacts for visibility instead.
- **Dockle** — container policy linter: flags running as root, unnecessary exposed ports,
  SUID/SGID bits, missing HEALTHCHECK, and other CIS Docker Benchmark-style checks. Covers
  the "policy checks" category (root, capabilities, exposed ports) that Trivy doesn't.
  Gate: `--exit-code 1 --exit-level WARN` (fails on WARN or FATAL).

**Tier 2 — add once the Tier 1 pipeline is proven out:**

- **Cosign** (Sigstore) — signs the image and attaches SBOM/provenance attestations using
  GitHub's own OIDC identity (keyless, no key management to maintain). Answers "was this
  actually built by this repo's workflow, from what source." GitHub's own
  `actions/attest-build-provenance` is a viable native alternative tied directly to GHCR if
  a simpler, GitHub-only mechanism is preferred over standing up Cosign.

**Tier 3 — genuine ongoing maintenance cost (ruleset upkeep); add when there's bandwidth,
not milestone 1:**

- **ClamAV + YARA** — signature-based malware/backdoor detection against the extracted
  image filesystem. Needs a maintained ruleset (e.g. Florian Roth's `signature-base`, a
  widely-used public YARA set) kept current — this is the actual "malware/backdoor" layer,
  with the important caveat from the threat-model section above: known signatures only.
- **Grype** (Anchore) as a second vulnerability scanner alongside Trivy — different
  vulnerability database, genuine defense-in-depth, but doubles scan time; only worth it
  once single-tool false-negative risk feels worth paying for.

## Least-privilege GitHub Actions permissions

Public repo, so `GITHUB_TOKEN` defaults to read-only unless a workflow explicitly requests
more via a `permissions:` block. Each job gets only what it needs — e.g. build/scan steps
stay at `contents: read`, only the final publish step gets `packages: write`.
`id-token: write` only where Cosign/attestation signing needs it (Tier 2).

## Public-repo posture

- Repo and GHCR packages are both public, deliberately (transparency: anyone can audit
  which recipe built which image). **Nothing secret is ever allowed inside an image** —
  this is a hard constraint, not a scanning target.
- No external contributions accepted — solo-maintained. PRs disabled in repo settings
  where possible. This repo's own contribution surface is therefore not currently a threat
  vector distinct from the upstream-hostile-source one above; revisit this section if that
  policy ever changes.
