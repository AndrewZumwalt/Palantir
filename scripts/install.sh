#!/usr/bin/env bash
# Palantir - Raspberry Pi Installation Script
# Run as root on a fresh Raspberry Pi OS (Bookworm)
#
# Flags:
#   --skip-apt        Skip apt-get entirely (use when mirror is blocked;
#                     Pi OS Bookworm ships python3/venv; add redis manually later)
#   --use-fakeredis   Wire PALANTIR_REDIS_FAKE=1 in .env so the web service
#                     runs without redis-server (same as Mac dev mode)
set -euo pipefail

INSTALL_DIR="/opt/palantir"
DATA_DIR="/var/lib/palantir"
SERVICE_USER="palantir"
SKIP_APT=0
USE_FAKEREDIS=0

# Resolve the repo root from this script's own location (scripts/ lives one
# level below the project root). This makes the installer work regardless of
# the user's current working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -f "$REPO_ROOT/pyproject.toml" ]; then
    echo "Error: pyproject.toml not found at $REPO_ROOT."
    echo "       Run this script from inside a checked-out Palantir repo."
    exit 1
fi

for arg in "$@"; do
    case "$arg" in
        --skip-apt)       SKIP_APT=1 ;;
        --use-fakeredis)  USE_FAKEREDIS=1 ;;
    esac
done

echo "=== Palantir Installation ==="
[ "$SKIP_APT" = "1" ]       && echo "  (--skip-apt: skipping system package install)"
[ "$USE_FAKEREDIS" = "1" ]  && echo "  (--use-fakeredis: using in-process Redis shim)"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: Please run as root (sudo ./install.sh)"
    exit 1
fi

# 1. Install system dependencies
if [ "$SKIP_APT" = "0" ]; then
    echo "[1/9] Installing system packages..."
    apt-get update -qq
    apt-get install -y -qq \
        python3 python3-pip python3-venv \
        redis-server \
        libopencv-dev python3-opencv \
        portaudio19-dev \
        libopenblas-dev \
        libhdf5-dev \
        libssl-dev \
        ufw \
        git
else
    echo "[1/9] Skipping apt-get (--skip-apt).  Ensure python3-venv is available."
    python3 -m venv --version > /dev/null 2>&1 || {
        echo "Error: python3-venv not found. Run with hotspot or --use-fakeredis only."
        exit 1
    }
fi

# 2. Create service user
echo "[2/9] Creating service user..."
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
    usermod -aG audio,video,gpio "$SERVICE_USER"
fi

# 3. Create directory structure (including TLS + backups)
echo "[3/9] Setting up directories..."
mkdir -p \
    "$INSTALL_DIR" \
    "$DATA_DIR/enrollments" \
    "$DATA_DIR/models" \
    "$DATA_DIR/backups" \
    "$DATA_DIR/tls"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chmod 700 "$DATA_DIR/tls"

# 4. Copy project files
echo "[4/9] Copying project files from $REPO_ROOT ..."
# Use `cp -a <src>/.` so hidden files (.env.example location, etc.) are copied
# and we don't depend on cwd.
cp -a "$REPO_ROOT/." "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# Sanity check: pip install -e will explode without this.
if [ ! -f "$INSTALL_DIR/pyproject.toml" ]; then
    echo "Error: pyproject.toml missing from $INSTALL_DIR after copy."
    echo "       Check that $REPO_ROOT is a full repo checkout."
    exit 1
fi

# 5. Set up Python virtual environment
echo "[5/9] Setting up Python environment..."
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
echo "[6/9] Generating configuration..."
AUTH_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/config/.env.example" "$INSTALL_DIR/.env"
    sed -i "s|PALANTIR_AUTH_TOKEN=|PALANTIR_AUTH_TOKEN=$AUTH_TOKEN|" "$INSTALL_DIR/.env"
    echo "PALANTIR_ENV=production" >> "$INSTALL_DIR/.env"
    if [ "$USE_FAKEREDIS" = "1" ]; then
        echo "PALANTIR_REDIS_FAKE=1" >> "$INSTALL_DIR/.env"
        echo "  (fakeredis: in-process Redis, no redis-server needed)"
    fi
    chmod 600 "$INSTALL_DIR/.env"
    chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
    echo "  Auth token: $AUTH_TOKEN"
    echo "  (Save this token - you'll need it to access the web UI)"
fi

# 7. Install systemd services + timers
echo "[7/9] Installing systemd units..."
for unit_file in "$INSTALL_DIR/systemd/"*.service "$INSTALL_DIR/systemd/"*.timer; do
    # Skip glob non-match
    [ -e "$unit_file" ] || continue
    cp "$unit_file" /etc/systemd/system/
done
systemctl daemon-reload

# Enable long-running services
for svc in audio vision brain tts eventlog web; do
    systemctl enable "palantir-$svc.service"
done

# Enable the nightly backup timer (the service unit runs on demand)
systemctl enable palantir-backup.timer

# 8. Configure firewall (default-deny, permit 8080 only)
echo "[8/9] Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 8080/tcp comment "Palantir Web UI"
ufw --force enable

# 9. Configure Redis for Unix socket (safer than TCP for same-host IPC)
echo "[9/9] Configuring Redis..."
if ! grep -q "unixsocket /var/run/redis/redis.sock" /etc/redis/redis.conf; then
    cat >> /etc/redis/redis.conf <<EOF

# Palantir: enable Unix socket (restricted to redis+palantir group)
unixsocket /var/run/redis/redis.sock
unixsocketperm 770
# Disable TCP listener — LAN exposure is unnecessary
bind 127.0.0.1 -::1
protected-mode yes
EOF
    usermod -aG redis "$SERVICE_USER"
    systemctl restart redis
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /opt/palantir/.env and add your ANTHROPIC_API_KEY"
echo "  2. Start all services: sudo systemctl start palantir-{audio,vision,brain,tts,eventlog,web}"
echo "  3. Start the backup timer now: sudo systemctl start palantir-backup.timer"
echo "  4. Access the web UI: https://$(hostname -I | awk '{print $1}'):8080"
echo "     (First load generates a self-signed cert; accept the browser warning)"
echo "  5. Use auth token: $AUTH_TOKEN"
echo ""
