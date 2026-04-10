#!/usr/bin/env bash
# Palintir - Raspberry Pi Installation Script
# Run as root on a fresh Raspberry Pi OS (Bookworm)
set -euo pipefail

INSTALL_DIR="/opt/palintir"
DATA_DIR="/var/lib/palintir"
SERVICE_USER="palintir"

echo "=== Palintir Installation ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: Please run as root (sudo ./install.sh)"
    exit 1
fi

# 1. Install system dependencies
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    redis-server \
    libopencv-dev python3-opencv \
    portaudio19-dev \
    libopenblas-dev \
    libhdf5-dev \
    ufw \
    git

# 2. Create service user
echo "[2/8] Creating service user..."
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
    usermod -aG audio,video,gpio "$SERVICE_USER"
fi

# 3. Create directory structure
echo "[3/8] Setting up directories..."
mkdir -p "$INSTALL_DIR" "$DATA_DIR/enrollments" "$DATA_DIR/models"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"

# 4. Copy project files
echo "[4/8] Copying project files..."
cp -r . "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# 5. Set up Python virtual environment
echo "[5/8] Setting up Python environment..."
sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/.venv"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"
# ML dependencies will be installed per-phase:
#   pip install -e ".[voice]"   # Phase 2
#   pip install -e ".[face]"    # Phase 3
#   pip install -e ".[speaker]" # Phase 4
#   pip install -e ".[objects]" # Phase 5
#   pip install -e ".[ml]"     # All at once

# 6. Generate auth token and .env
echo "[6/8] Generating configuration..."
AUTH_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/config/.env.example" "$INSTALL_DIR/.env"
    sed -i "s|PALINTIR_AUTH_TOKEN=|PALINTIR_AUTH_TOKEN=$AUTH_TOKEN|" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
    echo "  Auth token: $AUTH_TOKEN"
    echo "  (Save this token - you'll need it to access the web UI)"
fi

# 7. Install systemd services
echo "[7/8] Installing systemd services..."
for service_file in "$INSTALL_DIR/systemd/"*.service; do
    cp "$service_file" /etc/systemd/system/
done
systemctl daemon-reload

# Enable all services
for svc in audio vision brain tts eventlog web; do
    systemctl enable "palintir-$svc.service"
done

# 8. Configure firewall
echo "[8/8] Configuring firewall..."
ufw allow 8080/tcp comment "Palintir Web UI"
ufw --force enable

# Configure Redis for Unix socket
if ! grep -q "unixsocket /var/run/redis/redis.sock" /etc/redis/redis.conf; then
    cat >> /etc/redis/redis.conf <<EOF

# Palintir: enable Unix socket
unixsocket /var/run/redis/redis.sock
unixsocketperm 770
EOF
    usermod -aG redis "$SERVICE_USER"
    systemctl restart redis
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /opt/palintir/.env and add your ANTHROPIC_API_KEY"
echo "  2. Start all services: sudo systemctl start palintir-{audio,vision,brain,tts,eventlog,web}"
echo "  3. Access the web UI: https://$(hostname -I | awk '{print $1}'):8080"
echo "  4. Use auth token: $AUTH_TOKEN"
echo ""
