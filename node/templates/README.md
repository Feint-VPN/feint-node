# Sing-Box Configuration Templates

This directory contains template files for sing-box configuration.

## config.json

The `config.json` template is the initial sing-box configuration used during node initialization. It defines all 5 protocol inbounds with proper ports and settings.

### Protocol Configuration

The template includes the following protocol inbounds:

1. **VLESS Vision with TLS** (Port 8443)
   - Tag: `vless-reality-in`
   - Uses the node's TLS certificate
   - Multiplex disabled for Vision compatibility

2. **VMess with WebSocket** (Port 443)
   - Tag: `vmess-ws-in`
   - Transport: WebSocket with path `/vmess-path`
   - TLS enabled with Let's Encrypt certificates

3. **Trojan** (Port 2053)
   - Tag: `trojan-in`
   - TLS enabled with Let's Encrypt certificates

4. **Hysteria2** (Port 2083)
   - Tag: `hysteria2-in`
   - Bandwidth: 1000 Mbps up/down (configurable)
   - TLS enabled with Let's Encrypt certificates

5. **Shadowsocks** (Port 8388)
   - Tag: `shadowsocks-in`
   - Method: Configurable via environment variable
   - Password: Configurable via environment variable

### Environment Variable Placeholders

The template uses the following environment variable placeholders:

- `${TLS_CERT_PATH}`: Path to TLS certificate (Let's Encrypt fullchain.pem)
- `${TLS_KEY_PATH}`: Path to TLS private key (Let's Encrypt privkey.pem)
- `${SHADOWSOCKS_METHOD}`: Encryption method for Shadowsocks (e.g., `2022-blake3-aes-128-gcm`)
- `${SHADOWSOCKS_PASSWORD}`: Password for Shadowsocks

### Outbounds and Routing

The template includes two outbounds:

- **direct**: Direct connection to the internet
- **block**: Block traffic

Routing rules:

- Block all private IP traffic
- Default: Direct connection for all other traffic

### Usage

This template is used by the Initialization Service during node setup. The service:

1. Loads the template
2. Replaces environment variable placeholders with actual values
3. Writes the final configuration to `/opt/sing-box/config.json`
4. Starts the sing-box container

The template can also be used manually for testing or custom deployments.
