# syntax=docker/dockerfile:1.7

FROM golang:1.24.7-alpine AS builder

ARG SINGBOX_REF=v1.13.19

RUN apk add --no-cache build-base ca-certificates git

WORKDIR /src

RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    for attempt in 1 2 3; do \
        git clone --depth 1 --branch "${SINGBOX_REF}" https://github.com/SagerNet/sing-box.git . \
            && break; \
        rm -rf /src/* /src/.git; \
        test "${attempt}" = 3 && exit 1; \
        sleep "${attempt}"; \
    done \
    && CGO_ENABLED=0 go build \
        -trimpath \
        -tags "with_clash_api with_v2ray_api with_utls with_quic" \
        -ldflags "-s -w -buildid=" \
        -o /out/sing-box \
        ./cmd/sing-box

FROM alpine:3.21

LABEL org.opencontainers.image.source="https://github.com/Feint-VPN/feint-node"

RUN apk add --no-cache ca-certificates tzdata

COPY --from=builder /out/sing-box /usr/local/bin/sing-box

ENTRYPOINT ["sing-box"]
CMD ["run", "-c", "/opt/sing-box/config.json"]
