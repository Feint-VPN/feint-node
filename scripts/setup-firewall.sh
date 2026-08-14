#!/bin/bash
# Feint VPN Node - UFW Firewall Setup Script
# Configures firewall rules and optionally changes SSH port

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

print_header() {
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "This script must be run as root"
    echo "Please run: sudo $0"
    exit 1
fi

echo ""
print_header "Feint VPN Node - Firewall Setup"
echo ""

# Check if UFW is installed
if ! command -v ufw &> /dev/null; then
    print_warning "UFW is not installed. Installing..."
    apt-get update
    apt-get install -y ufw
    print_success "UFW installed"
fi

# Get current SSH port
CURRENT_SSH_PORT=$(grep "^Port " /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}')
if [ -z "$CURRENT_SSH_PORT" ]; then
    CURRENT_SSH_PORT=22
fi

echo "Current SSH port: $CURRENT_SSH_PORT"
echo ""

# Ask if user wants to change SSH port
read -p "Do you want to change SSH port? (recommended for security) [y/N]: " -n 1 -r
echo
CHANGE_SSH_PORT=false
NEW_SSH_PORT=$CURRENT_SSH_PORT

if [[ $REPLY =~ ^[Yy]$ ]]; then
    CHANGE_SSH_PORT=true
    
    # Suggest a random port between 10000-65000
    SUGGESTED_PORT=$((10000 + RANDOM % 55000))
    
    echo ""
    print_info "Suggested SSH port: $SUGGESTED_PORT"
    read -p "Enter new SSH port [$SUGGESTED_PORT]: " INPUT_PORT
    
    if [ -z "$INPUT_PORT" ]; then
        NEW_SSH_PORT=$SUGGESTED_PORT
    else
        NEW_SSH_PORT=$INPUT_PORT
    fi
    
    # Validate port number
    if ! [[ "$NEW_SSH_PORT" =~ ^[0-9]+$ ]] || [ "$NEW_SSH_PORT" -lt 1024 ] || [ "$NEW_SSH_PORT" -gt 65535 ]; then
        print_error "Invalid port number. Must be between 1024 and 65535"
        exit 1
    fi
    
    # Check if port is already in use
    if netstat -tuln | grep -q ":$NEW_SSH_PORT "; then
        print_error "Port $NEW_SSH_PORT is already in use"
        exit 1
    fi
fi

echo ""
print_header "Firewall Configuration Summary"
echo ""

# Check if .env.local exists to read ports
if [ -f .env.local ]; then
    print_info "Reading port configuration from .env.local..."
    
    # Read ports from .env.local
    API_PORT=$(grep "^API_PORT=" .env.local 2>/dev/null | cut -d '=' -f2 | tr -d '"' | tr -d "'")
    VLESS_PORT=$(grep "^VLESS_PORT=" .env.local 2>/dev/null | cut -d '=' -f2 | tr -d '"' | tr -d "'")
    VMESS_PORT=$(grep "^VMESS_PORT=" .env.local 2>/dev/null | cut -d '=' -f2 | tr -d '"' | tr -d "'")
    TROJAN_PORT=$(grep "^TROJAN_PORT=" .env.local 2>/dev/null | cut -d '=' -f2 | tr -d '"' | tr -d "'")
    HYSTERIA2_PORT=$(grep "^HYSTERIA2_PORT=" .env.local 2>/dev/null | cut -d '=' -f2 | tr -d '"' | tr -d "'")
    SHADOWSOCKS_PORT=$(grep "^SHADOWSOCKS_PORT=" .env.local 2>/dev/null | cut -d '=' -f2 | tr -d '"' | tr -d "'")
    CERTBOT_PORT=$(grep "^CERTBOT_PORT=" .env.local 2>/dev/null | cut -d '=' -f2 | tr -d '"' | tr -d "'")
fi

# Define default ports if not found in .env.local
API_PORT=${API_PORT:-8000}
VLESS_PORT=${VLESS_PORT:-8443}
VMESS_PORT=${VMESS_PORT:-443}
TROJAN_PORT=${TROJAN_PORT:-2053}
HYSTERIA2_PORT=${HYSTERIA2_PORT:-2083}
SHADOWSOCKS_PORT=${SHADOWSOCKS_PORT:-8388}
CERTBOT_PORT=${CERTBOT_PORT:-80}

echo -e "  ${BOLD}SSH:${NC}         $NEW_SSH_PORT"
echo -e "  ${BOLD}API:${NC}         $API_PORT"
echo -e "  ${BOLD}Certbot:${NC}     $CERTBOT_PORT (HTTP ACME challenge)"
echo ""
echo -e "  ${BOLD}VPN Protocols:${NC}"
echo "    VLESS:       $VLESS_PORT"
echo "    VMess:       $VMESS_PORT"
echo "    Trojan:      $TROJAN_PORT"
echo "    Hysteria2:   $HYSTERIA2_PORT"
echo "    Shadowsocks: $SHADOWSOCKS_PORT"
echo ""

read -p "Continue with firewall setup? [y/N]: " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_warning "Firewall setup cancelled"
    exit 0
fi

echo ""
print_header "Configuring Firewall"
echo ""

# Step 1: Change SSH port if requested (BEFORE enabling UFW)
if [ "$CHANGE_SSH_PORT" = true ] && [ "$NEW_SSH_PORT" != "$CURRENT_SSH_PORT" ]; then
    print_info "Changing SSH port from $CURRENT_SSH_PORT to $NEW_SSH_PORT..."
    
    # Check if sshd_config exists
    if [ ! -f /etc/ssh/sshd_config ]; then
        print_error "SSH configuration file not found at /etc/ssh/sshd_config"
        print_warning "This might be a non-Linux system or SSH is not installed"
        print_info "Skipping SSH port change..."
        NEW_SSH_PORT=$CURRENT_SSH_PORT
        CHANGE_SSH_PORT=false
    else
        # Backup sshd_config
        cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup.$(date +%Y%m%d_%H%M%S)
        
        # Update SSH port
        if grep -q "^Port " /etc/ssh/sshd_config; then
            sed -i "s/^Port .*/Port $NEW_SSH_PORT/" /etc/ssh/sshd_config
        else
            echo "Port $NEW_SSH_PORT" >> /etc/ssh/sshd_config
        fi
        
        # Also update ListenAddress if it exists
        if grep -q "^#Port " /etc/ssh/sshd_config; then
            sed -i "s/^#Port .*/Port $NEW_SSH_PORT/" /etc/ssh/sshd_config
        fi
        
        print_success "SSH configuration updated"
        
        # Test SSH configuration
        print_info "Testing SSH configuration..."
        if sshd -t 2>/dev/null; then
            print_success "SSH configuration is valid"
        else
            print_error "SSH configuration test failed"
            print_warning "Restoring backup..."
            cp /etc/ssh/sshd_config.backup.$(date +%Y%m%d)* /etc/ssh/sshd_config 2>/dev/null || true
            NEW_SSH_PORT=$CURRENT_SSH_PORT
            CHANGE_SSH_PORT=false
        fi
    fi
fi

# Step 2: Reset UFW to default state
print_info "Resetting UFW to default state..."
ufw --force reset > /dev/null 2>&1

# Step 3: Set default policies
print_info "Setting default policies (deny incoming, allow outgoing)..."
ufw default deny incoming
ufw default allow outgoing

# Step 4: Allow SSH on new port FIRST (critical!)
print_info "Allowing SSH on port $NEW_SSH_PORT..."
ufw allow $NEW_SSH_PORT/tcp comment 'SSH'
print_success "SSH port $NEW_SSH_PORT allowed"

# Step 5: Allow API port
print_info "Allowing API on port $API_PORT..."
ufw allow $API_PORT/tcp comment 'Feint API'

# Step 6: Allow Certbot port
print_info "Allowing Certbot on port $CERTBOT_PORT..."
ufw allow $CERTBOT_PORT/tcp comment 'Certbot ACME'

# Step 7: Allow VPN protocol ports
print_info "Allowing VPN protocol ports..."
ufw allow $VLESS_PORT/tcp comment 'VLESS'
ufw allow $VMESS_PORT/tcp comment 'VMess'
ufw allow $TROJAN_PORT/tcp comment 'Trojan'
ufw allow $HYSTERIA2_PORT/udp comment 'Hysteria2'
ufw allow $SHADOWSOCKS_PORT/tcp comment 'Shadowsocks'
ufw allow $SHADOWSOCKS_PORT/udp comment 'Shadowsocks'

print_success "All VPN ports configured"

# Step 8: Enable UFW
print_info "Enabling UFW..."
echo "y" | ufw enable > /dev/null 2>&1
print_success "UFW enabled"

# Step 9: Restart SSH if port was changed
if [ "$CHANGE_SSH_PORT" = true ] && [ "$NEW_SSH_PORT" != "$CURRENT_SSH_PORT" ]; then
    print_info "Restarting SSH service..."
    systemctl restart sshd || systemctl restart ssh
    print_success "SSH service restarted on port $NEW_SSH_PORT"
fi

# Step 10: Show UFW status
echo ""
print_header "Firewall Status"
echo ""
ufw status numbered

echo ""
print_header "Setup Complete"
echo ""

if [ "$CHANGE_SSH_PORT" = true ] && [ "$NEW_SSH_PORT" != "$CURRENT_SSH_PORT" ]; then
    print_warning "IMPORTANT: SSH port has been changed!"
    echo ""
    echo -e "  ${BOLD}Old SSH port:${NC} $CURRENT_SSH_PORT"
    echo -e "  ${BOLD}New SSH port:${NC} $NEW_SSH_PORT"
    echo ""
    print_warning "Test your SSH connection in a NEW terminal BEFORE closing this one:"
    echo ""
    echo -e "  ${CYAN}ssh -p $NEW_SSH_PORT user@your-server${NC}"
    echo ""
    print_warning "If you can't connect, you can restore the old configuration:"
    echo ""
    echo -e "  ${CYAN}sudo cp /etc/ssh/sshd_config.backup.* /etc/ssh/sshd_config${NC}"
    echo -e "  ${CYAN}sudo systemctl restart sshd${NC}"
    echo -e "  ${CYAN}sudo ufw allow $CURRENT_SSH_PORT/tcp${NC}"
    echo ""
fi

print_success "Firewall configured successfully!"
echo ""
echo "Allowed ports:"
echo "  SSH:         $NEW_SSH_PORT/tcp"
echo "  API:         $API_PORT/tcp"
echo "  Certbot:     $CERTBOT_PORT/tcp"
echo "  VLESS:       $VLESS_PORT/tcp"
echo "  VMess:       $VMESS_PORT/tcp"
echo "  Trojan:      $TROJAN_PORT/tcp"
echo "  Hysteria2:   $HYSTERIA2_PORT/udp"
echo "  Shadowsocks: $SHADOWSOCKS_PORT/tcp+udp"
echo ""

exit 0
