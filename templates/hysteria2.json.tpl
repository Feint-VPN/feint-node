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
