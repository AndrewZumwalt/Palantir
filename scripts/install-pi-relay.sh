#!/usr/bin/env bash
# Install the Pi-side relay client.
#
# Run on a Raspberry Pi (any model, any Pi OS / Python version Pi OS ships)
# AFTER the laptop is up.  The Pi only needs to capture mic/camera, push
# them over WebSocket, and play back the laptop's TTS output — no ML, no
# torch, no insightface.
#
# Usage:
#   sudo ./scripts/install-pi-relay.sh \
#       --laptop wss://laptop.local:8080/relay/ws \
#       --token  <PALANTIR_AUTH_TOKEN>
#
# Re-run any time to pick up code changes; the script is idempotent.

set -euo pipefail

INSTALL_DIR="/opt/palantir"
SERVICE_USER="palantir"
LAPTOP_URL=""
AUTH_TOKEN=""
VERIFY_TLS=0   # default: insecure (laptop ships a self-signed cert)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --laptop)      LAPTOP_URL="$2"; shift 2 ;;
        --token)       AUTH_TOKEN="$2"; shift 2 ;;
        --verify-tls)  VERIFY_TLS=1;    shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [ "$EUID" -ne 0 ]; then
    echo "Error: run as root (sudo)." >&2
    exit 1
fi

if [ -z "$LAPTOP_URL" ]; then
    echo "Error: --laptop wss://<laptop>:8080/relay/ws is required." >&2
    exit 2
fi
if [ -z "$AUTH_TOKEN" ]; then
    echo "Error: --token <PALANTIR_AUTH_TOKEN> is required." >&2
    exit 2
fi

echo "=== Palantir Pi relay install ==="
echo "  laptop:     $LAPTOP_URL"
echo "  install:    $INSTALL_DIR"
echo "  user:       $SERVICE_USER"

# 1. Service user
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "[1/5] Creating service user..."
    useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
    usermod -aG audio,video,gpio "$SERVICE_USER" 2>/dev/null || true
fi

# 2. Sync project files
echo "[2/5] Copying project to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r . "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# 3. Build venv with whatever Python ships (3.13 on Trixie is fine)
echo "[3/5] Creating venv + installing relay deps..."
sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/.venv"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet \
    -e "$INSTALL_DIR[relay-pi]"

# 4. .env (preserve previous values if re-run)
echo "[4/5] Writing $INSTALL_DIR/.env..."
ENV_FILE="$INSTALL_DIR/.env"
{
    echo "PALANTIR_LAPTOP_URL=$LAPTOP_URL"
    echo "PALANTIR_AUTH_TOKEN=$AUTH_TOKEN"
    if [ "$VERIFY_TLS" = "0" ]; then
        echo "PALANTIR_RELAY_INSECURE=1"
    fi
} > "$ENV_FILE"
chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# 5. Systemd unit
echo "[5/5] Installing systemd unit..."
cp "$INSTALL_DIR/systemd/palantir-pi-relay.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable palantir-pi-relay.service
systemctl restart palantir-pi-relay.service

echo
echo "=== Done ==="
echo "  status:   sudo systemctl status palantir-pi-relay --no-pager -l"
echo "  logs:     journalctl -u palantir-pi-relay -f"
echo
