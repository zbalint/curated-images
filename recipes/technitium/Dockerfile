# Technitium DNS Server, built from source, running as the repo-wide fixed non-root
# identity (65532:65532, "nonroot") on a distroless-equivalent base (Ubuntu Chiseled).
# Binds the privileged port 53 via a build-time file capability (setcap) instead of
# running as root -- see docs/ARCHITECTURE.md constraints 2 and 4.
#
# Build layout mirrors Technitium's own build.md: TechnitiumLibrary and DnsServer are
# cloned as sibling directories and the library is built first. This is load-bearing, not
# incidental -- DnsServerCore's own .csproj files reference the library's DLLs via
# HintPath = ../../TechnitiumLibrary/bin/*.dll, not ProjectReference/NuGet, so the sibling
# directory layout has to match at build time.
#
# TechnitiumLibrary tags as "dns-server-v<version>" for the DnsServer release it matches
# (not the library's own version) -- confirmed via the GitHub API for v15.5.1. Derived
# automatically below from DNS_SERVER_VERSION so the CI workflow only has to inject one
# build arg (see recipe.yaml's build.args).
#
# libmsquic (DNS-over-QUIC) is included for feature parity with upstream's own image,
# installed the same way upstream's own Dockerfile does it (Microsoft's Ubuntu 24.04
# package repo). Validated: correct files present, full shared-library dependency chain
# resolved (libcrypto.so.3/libnuma.so.1), clean startup -- not validated: an actual live
# DoQ handshake (Technitium's DoQ listener needs a TLS cert configured via its own web
# console/API, not an env var; out of scope for this build-time verification).

# ---- builder ----
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS builder

ARG DNS_SERVER_VERSION=v15.5.1
ARG TECHNITIUM_LIBRARY_TAG=dns-server-${DNS_SERVER_VERSION}

WORKDIR /src
RUN git clone --depth 1 --branch ${TECHNITIUM_LIBRARY_TAG} \
      https://github.com/TechnitiumSoftware/TechnitiumLibrary.git TechnitiumLibrary \
    && git clone --depth 1 --branch ${DNS_SERVER_VERSION} \
      https://github.com/TechnitiumSoftware/DnsServer.git DnsServer

RUN dotnet build TechnitiumLibrary/TechnitiumLibrary.ByteTree/TechnitiumLibrary.ByteTree.csproj -c Release \
    && dotnet build TechnitiumLibrary/TechnitiumLibrary.Net/TechnitiumLibrary.Net.csproj -c Release \
    && dotnet build TechnitiumLibrary/TechnitiumLibrary.Security.OTP/TechnitiumLibrary.Security.OTP.csproj -c Release

RUN dotnet publish DnsServer/DnsServerApp/DnsServerApp.csproj -c Release -o /out

# ---- assets: everything that needs apt/dpkg, on the same non-chiseled Ubuntu Noble
# base as the final image so file paths and ABI match exactly. Nothing in this stage
# ships in the final image except the specific files copied out of it below.
FROM mcr.microsoft.com/dotnet/aspnet:10.0-noble AS assets

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates wget libcap2-bin \
    && wget -q -O /tmp/packages-microsoft-prod.deb \
         https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y libmsquic \
    && rm -rf /var/lib/apt/lists/* /tmp/packages-microsoft-prod.deb

# /etc/dns is the app's only writable path. chiseled has no mkdir of its own, so the
# directory is created here and copied across with an explicit --chown below --
# COPY of an *empty* directory does not carry over a chown set in the source stage
# (only per-file ownership inside it would; there's nothing inside it to carry).
RUN mkdir -p /etc/dns

# ---- capped: applies setcap to THIS chiseled base's own dotnet binary in place, using
# a temporarily-injected setcap tool (exec-form RUN needs no shell). Discarded stage --
# only /usr/share/dotnet/dotnet is copied out of it below, nothing else. Confirmed
# structurally and functionally that a setcap file capability survives this kind of
# cross-stage COPY, a full docker save/load round-trip (simulating a GHCR push/pull),
# and running under runsc (gVisor) -- see the prototype this recipe was adapted from
# (prototype/technitium-distroless-msquic) for the isolated repro.
FROM mcr.microsoft.com/dotnet/aspnet:10.0-noble-chiseled AS capped

# The chiseled base's own final USER instruction defaults to a non-root "app" account
# (uid 1654) -- setcap needs CAP_SETFCAP to write a file capability, so this stage's
# RUN has to run as root regardless of what the base image itself defaults to.
USER 0:0

COPY --from=assets /usr/sbin/setcap /usr/sbin/setcap
COPY --from=assets /usr/lib/x86_64-linux-gnu/libcap.so.2 /usr/lib/x86_64-linux-gnu/libcap.so.2.66 /usr/lib/x86_64-linux-gnu/
RUN ["/usr/sbin/setcap", "cap_net_bind_service=+ep", "/usr/share/dotnet/dotnet"]

# ---- runtime ----
FROM mcr.microsoft.com/dotnet/aspnet:10.0-noble-chiseled AS runtime

# The one file that carries the build-time capability forward.
COPY --from=capped /usr/share/dotnet/dotnet /usr/share/dotnet/dotnet

# libmsquic + its two non-libc runtime deps (libcrypto/libssl are already present in
# this chiseled base for ASP.NET's own TLS support -- verified, not copied again).
COPY --from=assets \
    /usr/lib/x86_64-linux-gnu/libmsquic.so.2 \
    /usr/lib/x86_64-linux-gnu/libmsquic.so.2.6.1 \
    /usr/lib/x86_64-linux-gnu/libnuma.so.1 \
    /usr/lib/x86_64-linux-gnu/libnuma.so.1.0.0 \
    /usr/lib/x86_64-linux-gnu/

# Fixed non-root identity (65532:65532, "nonroot") -- chiseled has no useradd, so
# these are hand-authored minimal passwd/group files (root entry kept, no unused
# built-in "app" account carried over) rather than generated at build time.
COPY rootfs/etc/passwd rootfs/etc/group /etc/

WORKDIR /opt/technitium/dns
COPY --from=builder /out /opt/technitium/dns
COPY --chown=65532:65532 --from=assets /etc/dns /etc/dns

USER 65532:65532

EXPOSE \
    53/udp 53/tcp      \
    853/udp 853/tcp    \
    443/udp 443/tcp    \
    80/tcp 8053/tcp    \
    5380/tcp 53443/tcp \
    67/udp

# Network mode is a deployment-time choice, not something this image or recipe encodes
# (docs/ARCHITECTURE.md constraint 1: deployment is a pure consumer of this image).
# The project's own locked decision for this recipe is bridge networking via a dedicated
# user-defined Docker network -- not the default bridge, not host, not macvlan -- applied
# at `docker run`/compose time on the consuming side.

# --portable-app forces logs into <config-folder>/logs instead of Program.cs's hardcoded
# /var/log/technitium/dns default (undocumented in DockerEnvironmentVariables.md; a
# read-only rootfs otherwise crashes on startup trying to create that path). With this
# flag, every writable path (config, zones, logs, blocklist cache, stats) lives under the
# single /etc/dns volume.
ENTRYPOINT ["/usr/bin/dotnet", "/opt/technitium/dns/DnsServerApp.dll"]
CMD ["--portable-app", "/etc/dns"]
