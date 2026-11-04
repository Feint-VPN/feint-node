FROM ghcr.io/xtls/xray-core:26.7.28 AS upstream

FROM golang:1.26.8-alpine AS build
ENV CGO_ENABLED=0 GOMAXPROCS=1 GOMEMLIMIT=600MiB
RUN apk add --no-cache git
WORKDIR /src
RUN git init && git remote add origin https://github.com/XTLS/Xray-core.git \
    && git fetch --depth 1 origin 5ca6f4b7d4dc20a881d4330e498892697627ec0c \
    && git checkout --detach FETCH_HEAD
COPY mtu-bbr.patch /tmp/mtu-bbr.patch
COPY bbr_mtu_test.go transport/internet/hysteria/congestion/bbr/feint_mtu_test.go
RUN if go test -p 1 -run '^TestFeintBBRHandlesMTUDecrease$' \
        ./transport/internet/hysteria/congestion/bbr > /tmp/reproduction.log 2>&1; then \
        echo 'Expected the upstream MTU regression to fail'; exit 1; \
    fi \
    && grep -F 'decreased max datagram size from 1280 to 1200' /tmp/reproduction.log
RUN git apply /tmp/mtu-bbr.patch \
    && go test -p 1 ./transport/internet/hysteria/congestion/...
COPY stateless_reset_test.go transport/internet/hysteria/feint_stateless_reset_test.go
RUN if go test -p 1 -run '^TestFeintServerTransport' \
        ./transport/internet/hysteria > /tmp/reset-reproduction.log 2>&1; then \
        echo 'Expected the upstream persistent reset-key regression to fail'; exit 1; \
    fi \
    && grep -F 'undefined: newServerTransport' /tmp/reset-reproduction.log
COPY stateless_reset.go transport/internet/hysteria/feint_stateless_reset.go
COPY stateless-reset.patch /tmp/stateless-reset.patch
RUN git apply --unidiff-zero /tmp/stateless-reset.patch \
    && go test -p 1 ./transport/internet/hysteria/... \
    && go build -p 1 -trimpath -ldflags="-s -w -buildid=" -o /out/xray ./main

FROM alpine:3.21

LABEL org.opencontainers.image.source="https://github.com/Feint-VPN/feint-node"

RUN apk add --no-cache ca-certificates tzdata libcap
COPY --from=build /out/xray /usr/local/bin/xray
COPY --from=upstream /usr/local/share/xray /usr/local/share/xray
RUN setcap cap_net_bind_service=+ep /usr/local/bin/xray

USER 1000:1000
ENTRYPOINT ["xray"]
CMD ["run", "-config", "/opt/sing-box/xray.json"]
