#!/usr/bin/env bash

firewall_apply() {
    local env_file="$1" ssh_port="$2" transition_port="${3:-}"
    local network_id bridge subnet
    network_id="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}' vpn-node-api)"
    bridge="br-${network_id:0:12}"
    subnet="$(docker network inspect "$network_id" --format '{{(index .IPAM.Config 0).Subnet}}')"

    ufw --force reset >/dev/null
    ufw default deny incoming
    ufw default deny routed
    ufw default allow outgoing
    ufw allow "$ssh_port/tcp" comment 'Feint SSH'
    [[ -z "$transition_port" ]] || ufw allow "$transition_port/tcp" comment 'Feint SSH transition'
    ufw allow 80/tcp comment 'Feint ACME'
    ufw allow "$(env_get API_PORT "$env_file")/tcp" comment 'Feint API'
    ufw allow "$(env_get VLESS_PORT "$env_file")/tcp" comment 'Feint VLESS'
    ufw allow "$(env_get VMESS_PORT "$env_file")/tcp" comment 'Feint VMess'
    ufw allow "$(env_get TROJAN_PORT "$env_file")/tcp" comment 'Feint Trojan'
    ufw allow "$(env_get HYSTERIA2_PORT "$env_file")/udp" comment 'Feint Hysteria2'
    ufw allow "$(env_get SHADOWSOCKS_PORT "$env_file")/tcp" comment 'Feint Shadowsocks'
    ufw allow "$(env_get SHADOWSOCKS_PORT "$env_file")/udp" comment 'Feint Shadowsocks'
    ufw allow in on "$bridge" from "$subnet" to any port 9090 proto tcp comment 'Feint Clash API internal'
    ufw allow in on "$bridge" from "$subnet" to any port 10085 proto tcp comment 'Feint V2Ray API internal'
    ufw --force enable >/dev/null
}
