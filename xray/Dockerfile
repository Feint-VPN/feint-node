FROM ghcr.io/xtls/xray-core:26.7.28 AS upstream

FROM alpine:3.21

LABEL org.opencontainers.image.source="https://github.com/Feint-VPN/feint-node"

RUN apk add --no-cache ca-certificates tzdata libcap
COPY --from=upstream /usr/local/bin/xray /usr/local/bin/xray
COPY --from=upstream /usr/local/share/xray /usr/local/share/xray
RUN setcap cap_net_bind_service=+ep /usr/local/bin/xray

USER 1000:1000
ENTRYPOINT ["xray"]
CMD ["run", "-config", "/opt/sing-box/xray.json"]
