# Traefik, built from source (Go binary + the webui dashboard's own frontend build),
# running as the repo-wide fixed non-root identity (65532:65532, "nonroot") on
# gcr.io/distroless/static-debian12 -- the actual scratch-equivalent Google ships for
# fully static (CGO_ENABLED=0) Go binaries. Binds the privileged ports 80/443 via a
# build-time file capability (setcap) instead of running as root -- see
# docs/ARCHITECTURE.md constraints 2 and 4, same mechanism already used by
# recipes/technitium.
#
# Unlike Technitium's chiseled .NET base, distroless/static-debian12's ":nonroot" tag
# already ships the exact fixed identity this repo requires out of the box
# (`nonroot:x:65532:65532:nonroot:/home/nonroot:/sbin/nologin` in its own /etc/passwd,
# confirmed by extracting it directly) plus a full ca-certificates bundle and tzdata --
# no hand-authored passwd/group files needed here, unlike recipes/technitium/rootfs.
#
# Traefik's web dashboard is a separate Vue/TypeScript app (webui/) whose built assets
# are Go-embedded (`//go:embed static` in webui/embed.go) -- webui/static/ ships EMPTY
# in the git source (a single placeholder file, see webui/readme.md), so a plain `go
# build` alone silently produces a binary with a broken/empty dashboard. This is the
# same category of "must replicate upstream's real build, not just `go build`" gotcha as
# Technitium's TechnitiumLibrary sibling-directory requirement. The exact commands below
# mirror upstream's own `.github/workflows/template-webui.yaml` build-webui job, not a
# guess: `yarn install && yarn tsc && yarn lint && yarn build`.

# ---- source: single clone shared by both the webui and Go build stages below, so
# there's no risk of them ever building from two different commits.
FROM alpine:3.22 AS source

RUN apk add --no-cache git

ARG TRAEFIK_VERSION=v3.7.13
WORKDIR /src
RUN git clone --depth 1 --branch ${TRAEFIK_VERSION} \
      https://github.com/traefik/traefik.git .

# ---- webui-builder: builds the dashboard frontend upstream itself embeds into the Go
# binary. node:24-alpine3.22 mirrors upstream's own webui/buildx.Dockerfile base exactly
# (webui/.nvmrc pins node 24.11.0; alpine 3.22 matches their build image's own base).
FROM node:24-alpine3.22 AS webui-builder

WORKDIR /src/webui

# corepack enable is required first: webui/package.json pins yarn via a
# `packageManager: yarn@4.13.0` field (Yarn Berry, resolved/fetched by corepack) --
# the alpine image's bundled classic yarn 1.x cannot read this project at all.
# .yarnrc.yml sets `enableScripts: false` (upstream's own choice, kept as-is here):
# dependency install/postinstall scripts never run, real supply-chain hardening this
# repo's threat model already benefits from for free.
RUN corepack enable

COPY --from=source /src/webui/package.json /src/webui/yarn.lock /src/webui/.yarnrc.yml ./
RUN yarn install --immutable

COPY --from=source /src/webui/ ./
ENV VITE_APP_BASE_URL="" \
    VITE_APP_BASE_API_URL="/api"
# Mirrors upstream's own `.github/workflows/template-webui.yaml` build-webui job
# exactly (yarn install, tsc, lint, build -- in that order); their `yarn build:prod`
# script additionally runs the vitest suite first, which that CI job itself skips
# (covered separately by their own test-unit.yaml), so this does too.
RUN yarn tsc && yarn lint && yarn build

# ---- go-builder: compiles the traefik binary itself, CGO_ENABLED=0 (upstream's own
# Makefile `binary` target already builds this way -- a fully static binary is why
# distroless/static, not distroless/base, is the correct final base here).
FROM golang:1.26-alpine3.22 AS go-builder

WORKDIR /src
COPY --from=source /src/ .

# Overwrites the empty placeholder committed in git with the real built dashboard --
# go:embed picks up whatever's on disk at build time, so this has to land before `go
# build` runs.
COPY --from=webui-builder /src/webui/static/ ./webui/static/

ARG TRAEFIK_VERSION=v3.7.13
ARG TARGETOS=linux
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build \
      -ldflags "-s -w \
        -X github.com/traefik/traefik/v3/pkg/version.Version=${TRAEFIK_VERSION} \
        -X github.com/traefik/traefik/v3/pkg/version.Codename=curated-images" \
      -installsuffix nocgo -o /out/traefik ./cmd/traefik

# ---- capped: applies setcap to the freshly built binary in place. Debian bookworm-slim
# matches the Debian 12 ("bookworm") generation distroless/static-debian12 itself is
# built from -- not that ABI matching matters for a statically-linked binary (setcap only
# writes an ELF xattr, no linking involved), just consistency with the final stage's
# origin. Discarded stage -- only the now-capable /out/traefik is copied out below.
FROM debian:bookworm-slim AS capped

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcap2-bin \
    && rm -rf /var/lib/apt/lists/*

COPY --from=go-builder /out/traefik /out/traefik
# A single CAP_NET_BIND_SERVICE covers every privileged port (<1024) Traefik binds --
# both 80 and 443 -- no separate grant needed per port.
RUN setcap cap_net_bind_service=+ep /out/traefik

# ---- runtime ----
FROM gcr.io/distroless/static-debian12:nonroot AS runtime

COPY --from=capped /out/traefik /traefik

EXPOSE 80/tcp 443/tcp 443/udp

# USER is already nonroot (65532:65532) by this base image's own default -- no USER
# instruction needed, unlike recipes/technitium's chiseled base which required one.

# Network mode, and every writable path (ACME certificate storage, any Traefik plugin
# storage directory if ever enabled), are deployment-time choices, not something this
# image or recipe encodes (docs/ARCHITECTURE.md constraint 1). See recipes/traefik/
# README.md for the actual `docker run`/compose shape this image was built and tested
# against.
ENTRYPOINT ["/traefik"]
