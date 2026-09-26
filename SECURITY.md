# Security Policy

## Reporting a Vulnerability

If you find a security vulnerability in this repository's own code (a recipe, a GitHub
Actions workflow, or the scanning/publishing configuration) or in an image it publishes,
please open a GitHub Issue describing the problem. This repository does not accept pull
requests, so report the issue rather than submitting a fix directly.

Vulnerabilities in an upstream project itself — the software a recipe packages — should be
reported to that project directly, not here.

See `docs/THREAT_MODEL.md` for the full threat model and scanning design behind this
project, including exactly what a published image's scan results do and don't guarantee.
