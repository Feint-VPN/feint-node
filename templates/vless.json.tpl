{
  "log": { "level": "warning", "timestamp": true },
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
      "multiplex": { "enabled": false }
    },
    {
      "type": "hysteria2",
      "tag": "hysteria2-in",
      "listen": "::",
      "listen_port": {{HYSTERIA2_PORT}},
      "users": [],
      "tls": {
        "enabled": true,
        "certificate_path": "/etc/letsencrypt/live/{{DOMAIN}}/fullchain.pem",
        "key_path": "/etc/letsencrypt/live/{{DOMAIN}}/privkey.pem"
      }
    },
    {
      "type": "mixed",
      "tag": "reverse-exit-in",
      "listen": "127.0.0.1",
      "listen_port": {{REVERSE_PROXY_PORT}}
    }
  ],
  "outbounds": [
    { "type": "direct", "tag": "direct" },
    { "type": "block", "tag": "block" }
  ],
  "route": {
    "rules": [
      { "action": "sniff" },
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
