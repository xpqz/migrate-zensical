# Image for tools/cutover/code-blocks.py: Dyalog 21.0 (pinned by digest) with
# the .NET 10 runtime, so .NET examples can run inside the locked-down
# container. The tool tags it code-blocks-dyalog:<first 12 hex of the sha256
# of this file> and builds it when missing; the network is used only here,
# at build time.
FROM dyalog/dyalog@sha256:d9c0dda7c53b3505b726a41709d5136ddcf15a441fdf572e2c439c69602ceb32
USER root
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libicu76 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
 && bash /tmp/dotnet-install.sh --channel 10.0 --runtime dotnet --install-dir /usr/share/dotnet \
 && ln -sf /usr/share/dotnet/dotnet /usr/bin/dotnet && rm /tmp/dotnet-install.sh
ENV DOTNET_ROOT=/usr/share/dotnet
USER dyalog
