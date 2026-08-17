{
  "log": { "level": "info", "timestamp": true },
  "dns": {
    "servers": [{ "type": "udp", "tag": "google", "server": "8.8.8.8" }],
    "final": "google",
    "strategy": "ipv4_only",
    "reverse_mapping": true
  },
  "inbounds": [
    {
      "type": "vless",
      "tag": "vless-reality-in",
      "listen": "::",
      "listen_port": {{VLESS_PORT}},
      "users": [],
      "tls": {
        "enabled": true,
        "server_name": "{{REALITY_SERVER_NAME}}",
        "reality": {
          "enabled": true,
          "handshake": {
            "server": "{{REALITY_SERVER_NAME}}",
            "server_port": 443
          },
          "private_key": "{{REALITY_PRIVATE_KEY}}",
          "short_id": ["{{REALITY_SHORT_ID}}"]
        }
      },
      "multiplex": { "enabled": true, "padding": true }
    },
    {
      "type": "vmess",
      "tag": "vmess-ws-in",
      "listen": "::",
      "listen_port": {{VMESS_PORT}},
      "users": [],
      "transport": {
        "type": "ws",
        "path": "/vmess-path",
        "max_early_data": 2048,
        "early_data_header_name": "Sec-WebSocket-Protocol"
      },
      "tls": {
        "enabled": true,
        "certificate_path": "/etc/letsencrypt/live/{{DOMAIN}}/fullchain.pem",
        "key_path": "/etc/letsencrypt/live/{{DOMAIN}}/privkey.pem"
      }
    },
    {
      "type": "trojan",
      "tag": "trojan-in",
      "listen": "::",
      "listen_port": {{TROJAN_PORT}},
      "users": [],
      "tls": {
        "enabled": true,
        "certificate_path": "/etc/letsencrypt/live/{{DOMAIN}}/fullchain.pem",
        "key_path": "/etc/letsencrypt/live/{{DOMAIN}}/privkey.pem"
      }
    },
    {
      "type": "hysteria2",
      "tag": "hysteria2-in",
      "listen": "::",
      "listen_port": {{HYSTERIA2_PORT}},
      "up_mbps": 1000,
      "down_mbps": 1000,
      "users": [],
      "tls": {
        "enabled": true,
        "certificate_path": "/etc/letsencrypt/live/{{DOMAIN}}/fullchain.pem",
        "key_path": "/etc/letsencrypt/live/{{DOMAIN}}/privkey.pem"
      }
    },
    {
      "type": "shadowsocks",
      "tag": "shadowsocks-in",
      "listen": "::",
      "listen_port": {{SHADOWSOCKS_PORT}},
      "method": "{{SHADOWSOCKS_METHOD}}",
      "password": "{{SHADOWSOCKS_PASSWORD}}",
      "users": []
    }
  ],
  "outbounds": [
    { "type": "direct", "tag": "direct" },
    { "type": "block", "tag": "block" }
  ],
  "route": {
    "rules": [
      { "port": [53], "action": "hijack-dns" },
      { "action": "sniff" },
      { "action": "resolve", "strategy": "ipv4_only" },
      { "ip_cidr": ["::/0"], "outbound": "block" },
      { "ip_is_private": true, "outbound": "block" },
      { "rule_set": "geoip-ru", "action": "reject" }
    ],
    "rule_set": [
      {
        "type": "local",
        "tag": "geoip-ru",
        "format": "binary",
        "path": "/opt/sing-box/geoip-ru.srs"
      }
    ],
    "final": "direct"
  },
  "experimental": {
    "clash_api": {
      "external_controller": "0.0.0.0:9090",
      "secret": "{{CLASH_API_SECRET}}"
    },
    "v2ray_api": {
      "listen": "0.0.0.0:10085",
      "stats": { "enabled": true, "users": [] }
    },
    "cache_file": {
      "enabled": true,
      "path": "/opt/sing-box/cache.db"
    }
  }
}
