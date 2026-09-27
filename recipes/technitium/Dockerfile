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
# resolved (libcrypto.so.3/libnuma.so.1), clean startup, plain DNS resolution and
# non-QUIC forwarders all work correctly.
#
# 2026-09-27 CONFIRMED BUG (arm64 only, real hardware): a DoQ upstream forwarder
# crashes the process with SIGABRT the moment a query hits the QUIC path. Root-caused
# via A/B test against stock upstream technitium/dns-server:latest on the same board
# and same config volume -- it crashes identically, proving this is an upstream
# msquic/OpenSSL bug on aarch64 (see recipe.yaml's platforms comment for the full
# writeup and the matching upstream issue), not something this Dockerfile's hardening
# introduced. No workaround on this recipe's side; needs an upstream msquic/OpenSSL
# fix. Not yet confirmed whether amd64 shares this bug.

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

# Collect the setcap and ldconfig tools and the shared libraries the runtime/capped
# stages need into
# one fixed, architecture-agnostic path. The Debian/Ubuntu multiarch directory these
# packages actually install into (/usr/lib/x86_64-linux-gnu, /usr/lib/aarch64-linux-gnu,
# ...) depends on the target platform this stage itself was built for -- since this
# recipe is multi-arch (recipe.yaml), a path hardcoded to one architecture would only
# work for that one architecture and silently fail COPY on every other one. Resolving
# the real multiarch dir here (where a shell exists) and copying into a stable location
# sidesteps needing to know or guess that name in any later stage. `cp -P` preserves the
# *.so.N -> *.so.N.n.n symlinks as symlinks (not dereferenced), and the glob avoids
# hardcoding an exact patch-version filename that an unrelated package bump could rename
# out from under a literal COPY path.
# uname -m already equals the Debian/Ubuntu multiarch triplet prefix for both
# architectures this recipe declares (x86_64, aarch64) -- avoids depending on
# dpkg-dev (not installed here) just for dpkg-architecture. Would need revisiting if
# a non-"-linux-gnu" architecture (e.g. armhf's arm-linux-gnueabihf) is ever added.
RUN multiarch="$(uname -m)-linux-gnu" \
    && mkdir -p /opt/curated-libs \
    && cp -P "/usr/lib/${multiarch}/libmsquic.so.2"* /opt/curated-libs/ \
    && cp -P "/usr/lib/${multiarch}/libnuma.so.1"* /opt/curated-libs/ \
    && cp -P "/usr/lib/${multiarch}/libcap.so.2"* /opt/curated-libs/ \
    && cp -P /usr/sbin/setcap /opt/curated-libs/setcap \
    && cp -P /sbin/ldconfig.real /opt/curated-libs/ldconfig

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

# setcap itself needs libcap.so.2 at exec time; ENV (not a shell export) works here
# even though this stage has no shell -- it's applied by the builder, not executed.
ENV LD_LIBRARY_PATH=/opt/curated-libs
COPY --from=assets /opt/curated-libs/setcap /opt/curated-libs/libcap.so.2* /opt/curated-libs/
RUN ["/opt/curated-libs/setcap", "cap_net_bind_service=+ep", "/usr/share/dotnet/dotnet"]

# ---- runtime ----
FROM mcr.microsoft.com/dotnet/aspnet:10.0-noble-chiseled AS runtime

# The one file that carries the build-time capability forward.
COPY --from=capped /usr/share/dotnet/dotnet /usr/share/dotnet/dotnet

# libmsquic + its one non-libc runtime dep (libcrypto/libssl are already present in
# this chiseled base for ASP.NET's own TLS support -- verified, not copied again).
# Registered into a build-time ld.so.cache below rather than found via
# LD_LIBRARY_PATH: dotnet carries a file capability (see the capped stage above),
# which puts the process into glibc secure-execution mode at exec time -- and secure
# mode strips LD_LIBRARY_PATH entirely, so a non-root capability-bearing process can
# never rely on it. Confirmed with a getauxval(AT_SECURE)+dlopen repro under the same
# non-root+capability conditions (AT_SECURE=1, LD_LIBRARY_PATH removed, load fails).
# ld.so.cache is a build-time, root-written artifact the loader still honors in
# secure-execution mode, so it keeps the same fixed, architecture-independent path
# used above without needing to know the real multiarch dir name in this shell-less
# stage.
COPY --from=assets /opt/curated-libs/libmsquic.so.2* /opt/curated-libs/libnuma.so.1* /opt/curated-libs/

# ldconfig.real is statically linked (confirmed via ldd), so it needs no shared
# libraries of its own copied into this shell-less stage. -f/-C point it at a
# self-contained config/cache pair instead of relying on /etc/ld.so.conf(.d), which
# this chiseled base doesn't ship. Requires root to write /etc/ld.so.cache --
# same reasoning as the capped stage's USER 0:0 above, regardless of what this base's
# own default USER is.
COPY --from=assets /opt/curated-libs/ldconfig /opt/curated-libs/ldconfig
COPY rootfs/opt/curated-libs/ld.so.conf /opt/curated-libs/ld.so.conf
USER 0:0
RUN ["/opt/curated-libs/ldconfig", "-f", "/opt/curated-libs/ld.so.conf", "-C", "/etc/ld.so.cache"]

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
