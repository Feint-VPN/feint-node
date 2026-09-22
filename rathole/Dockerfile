FROM debian:bookworm-slim

ARG RATHOLE_VERSION=0.5.0
ARG RATHOLE_SHA256=3e7d0d0f365120cd3cd351d147d1a12ee960c8068b464d4dd533a3821873b80e

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && curl --fail --location --silent --show-error \
        "https://github.com/rathole-org/rathole/releases/download/v${RATHOLE_VERSION}/rathole-x86_64-unknown-linux-gnu.zip" \
        --output /tmp/rathole.zip \
    && echo "${RATHOLE_SHA256}  /tmp/rathole.zip" | sha256sum --check --strict \
    && unzip /tmp/rathole.zip -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/rathole \
    && rm -rf /var/lib/apt/lists/* /tmp/rathole.zip \
    && useradd --create-home --uid 1000 appuser \
    && mkdir -p /opt/relay \
    && chown appuser:appuser /opt/relay

COPY --chmod=0755 entrypoint.sh /usr/local/bin/feint-rathole

USER appuser

ENV RATHOLE_CONFIG_PATH=/opt/relay/rathole.toml

ENTRYPOINT ["/usr/local/bin/feint-rathole"]
