# Technitium DNS Server

Built from source, running as the repo-wide fixed non-root identity (`65532:65532`,
`nonroot`) on a distroless-equivalent base (Ubuntu Chiseled), with a build-time file
capability granting `CAP_NET_BIND_SERVICE` instead of root, and `libmsquic` included
for DNS-over-QUIC feature parity with upstream's own image. See `recipe.yaml` for the
exact base/gVisor validation status, and `docs/ARCHITECTURE.md` /
`docs/THREAT_MODEL.md` for the project-wide design this recipe follows.

## Running it

This repository builds and scans the image; it never runs it (deployment is a pure
consumer, per `docs/ARCHITECTURE.md` constraint 1). The following reflects the design
decisions this recipe was actually built and tested against — deviating from them
(especially the network mode) hasn't been validated.

```bash
docker network create technitium-net   # dedicated user-defined bridge network --
                                        # not the default bridge, not host, not macvlan
docker volume create technitium-data   # named volume, not a host bind-mount: a bind
                                        # mount needs its host directory pre-owned by
                                        # uid 65532, which a named volume avoids by
                                        # populating from the image's own ownership

docker run -d --name technitium \
  --network technitium-net \
  -p 53:53/udp -p 53:53/tcp \
  -p 5380:5380/tcp \
  --user 65532:65532 \
  --cap-drop ALL --cap-add NET_BIND_SERVICE \
  --read-only --tmpfs /tmp:exec \
  -v technitium-data:/etc/dns \
  ghcr.io/zbalint/curated-technitium:<tag>
```

- `--cap-drop ALL --cap-add NET_BIND_SERVICE` is required, not optional cosmetic
  hardening: the image binds port 53 via a build-time file capability, which only
  becomes effective if the capability is present in the container's bounding set too
  (see the recipe's own commit history / SALTMDB for the underlying finding).
- `/etc/dns` is the only writable path — everything else is read-only rootfs. Config,
  zones, logs, blocklist cache, and dashboard stats all live under it.
- `--tmpfs /tmp:exec` needs the explicit `:exec`: Docker mounts tmpfs `noexec` by
  default, and Technitium apps that ship native code (e.g. Query Logs (Sqlite)) extract
  their library to `/tmp` and `dlopen` it, which a `noexec` mount refuses -- surfacing
  as `The type initializer for 'Microsoft.Data.Sqlite.SqliteConnection' threw an
  exception`. In compose: `tmpfs: - /tmp:exec`.
- Web console: `http://<host>:5380/`. Ports `853` (DoT/DoQ), `443`/`80`/`8053` (DoH),
  `53443` (web console HTTPS), and `67/udp` (DHCP, unexercised by this recipe) are also
  `EXPOSE`d if needed.

## Not yet validated

- **arm64**: declared in `recipe.yaml`'s `platforms`, and the base image + `libmsquic`
  are confirmed to publish arm64 variants, but this dev environment couldn't actually
  build-and-run an emulated arm64 image end-to-end (a local `userns-remap` config blocks
  registering QEMU). The first live GitHub Actions run, or direct testing on real arm64
  hardware, is the actual validation.
- **DNS-over-QUIC**: the files and dependency chain are present and the image starts
  cleanly, but no live DoQ handshake was tested — Technitium's DoQ listener needs a TLS
  certificate configured through its own web console/API, not an environment variable.
