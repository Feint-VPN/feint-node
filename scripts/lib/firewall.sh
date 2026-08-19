#!/usr/bin/env bash

sshd_setting() {
    sshd -T | awk -v key="$1" '$1 == key && !found { print $2; found=1 }'
}

firewall_apply() {
    local env_file="$1" ssh_port="$2" transition_port="${3:-}"
    local network_id bridge subnet
    allow() {
        ufw allow "$@" || { error "Could not add UFW rule: $*"; return 1; }
    }
    network_id="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}' vpn-node-api)"
    bridge="br-${network_id:0:12}"
    subnet="$(docker network inspect "$network_id" --format '{{(index .IPAM.Config 0).Subnet}}')"

    ufw --force reset >/dev/null
    ufw default deny incoming
    ufw default deny routed
    ufw default allow outgoing
    allow "$ssh_port/tcp" comment 'Feint SSH'
    [[ -z "$transition_port" ]] || allow "$transition_port/tcp" comment 'Feint SSH transition'
    allow 80/tcp comment 'Feint ACME'
    allow "$(env_get API_PORT "$env_file")/tcp" comment 'Feint API'
    allow "$(env_get HYSTERIA2_PORT "$env_file")/udp" comment 'Feint Hysteria2'
    if [[ "$(env_get NODE_TEMPLATE "$env_file" default)" == default ]]; then
        allow "$(env_get VLESS_PORT "$env_file")/tcp" comment 'Feint VLESS'
        allow "$(env_get VMESS_PORT "$env_file")/tcp" comment 'Feint VMess'
        allow "$(env_get TROJAN_PORT "$env_file")/tcp" comment 'Feint Trojan'
        allow "$(env_get SHADOWSOCKS_PORT "$env_file")/tcp" comment 'Feint Shadowsocks'
        allow "$(env_get SHADOWSOCKS_PORT "$env_file")/udp" comment 'Feint Shadowsocks'
    fi
    allow in on "$bridge" from "$subnet" to any port 9090 proto tcp comment 'Feint Clash API internal'
    allow in on "$bridge" from "$subnet" to any port 10085 proto tcp comment 'Feint V2Ray API internal'
    ufw --force enable >/dev/null \
        || { error "Could not enable the Feint firewall policy"; return 1; }
}
