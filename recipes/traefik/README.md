# Traefik

Built from source (Go binary + its own webui dashboard frontend), running as the
repo-wide fixed non-root identity (`65532:65532`, `nonroot`) on
`gcr.io/distroless/static-debian12`, with a build-time file capability granting
`CAP_NET_BIND_SERVICE` instead of root. See `recipe.yaml` for the exact base/gVisor
validation status, and `docs/ARCHITECTURE.md` / `docs/THREAT_MODEL.md` for the
project-wide design this recipe follows.

## Running it

This repository builds and scans the image; it never runs it (deployment is a pure
consumer, per `docs/ARCHITECTURE.md` constraint 1). The following reflects the design
decisions this recipe was actually built and tested against.

```bash
docker network create traefik-net      # dedicated user-defined bridge network --
                                        # not the default bridge, not host, not macvlan
docker volume create traefik-acme      # named volume for ACME certificate storage

docker run -d --name traefik \
  --network traefik-net \
  -p 80:80/tcp -p 443:443/tcp \
  --user 65532:65532 \
  --cap-drop ALL --cap-add NET_BIND_SERVICE \
  --read-only --tmpfs /tmp \
  -v traefik-acme:/data \
  -v ./traefik.yml:/etc/traefik/traefik.yml:ro \
  ghcr.io/zbalint/curated-traefik:<tag>
```

- `--cap-drop ALL --cap-add NET_BIND_SERVICE` is required, not optional cosmetic
  hardening: the image binds ports 80/443 via a build-time file capability, which
  only becomes effective if the capability is present in the container's bounding
  set too (same finding as recipes/technitium).
- Unlike recipes/technitium, there's no single fixed data directory baked into this
  image -- Traefik's writable state is whatever the mounted static/dynamic config
  tells it to use (ACME certificate storage path, access logs if enabled). `/data`
  above is just this README's own convention; point `--certificatesresolvers.*.acme.storage`
  at wherever you mount a volume.
- No plugin support is configured or bundled in this image. Traefik's plugin system
  (Yaegi-interpreted, used by e.g. the CrowdSec bouncer plugin) downloads plugin code
  at container startup into a writable `plugins-storage` directory and needs outbound
  HTTPS to the plugin catalog -- both are compatible with this image (ca-certificates
  are present; a writable path can be mounted the same way `/data` is above) but
  neither is set up here. Revisit when/if a CrowdSec-based recipe or compose stack
  needs it.

## Not yet validated

- **arm64**: not declared as a platform at all yet (see `recipe.yaml`) -- no
  confirmed real deployment target on ARM hardware exists today for this recipe.
- **Live ACME issuance**: build-time verification confirmed the binary starts
  cleanly, binds 80/443 non-root, and has a working CA bundle for outbound TLS, but
  no live Let's Encrypt DNS-01 challenge was exercised end-to-end (that needs a real
  DNS provider API token, out of scope for this recipe's own build-time validation).
