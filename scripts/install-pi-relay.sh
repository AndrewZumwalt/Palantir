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
SKIP_APT=0     # if 1, don't try to apt-install picamera2

while [[ $# -gt 0 ]]; do
    case "$1" in
        --laptop)      LAPTOP_URL="$2"; shift 2 ;;
        --token)       AUTH_TOKEN="$2"; shift 2 ;;
        --verify-tls)  VERIFY_TLS=1;    shift ;;
        --skip-apt)    SKIP_APT=1;      shift ;;
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
    echo "[1/6] Creating service user..."
    useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
    usermod -aG audio,video,gpio "$SERVICE_USER" 2>/dev/null || true
fi

# 2. apt deps -- mostly to make the CSI camera work via libcamera/picamera2.
#    We rely on the system package because the Pi-OS-shipped picamera2
#    is paired with the matching libcamera shared libs.
if [ "$SKIP_APT" = "0" ]; then
    echo "[2/6] Installing apt packages (python3-picamera2 + python3-venv)..."
    apt-get update -qq
    # picamera2 is in the default Pi OS repo on Bookworm/Trixie; the
    # other two are required for any pip install -e path.
    apt-get install -y -qq \
        python3-venv python3-pip python3-picamera2 || \
        echo "  (apt failed; you may need to install python3-picamera2 manually)"
else
    echo "[2/6] --skip-apt: not touching apt; ensure python3-picamera2 is installed if you want CSI."
fi

# 3. Sync project files
echo "[3/6] Copying project to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r . "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# 4. Build venv with --system-site-packages so the apt-installed
#    picamera2 (and its libcamera shared libs) are visible inside the
#    venv.  Building picamera2 from pip needs Cython + Meson + libcamera
#    headers, which is a much rougher path on a Pi.
echo "[4/6] Creating venv + installing relay deps..."
sudo -u "$SERVICE_USER" python3 -m venv --system-site-packages "$INSTALL_DIR/.venv"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet \
    -e "$INSTALL_DIR[relay-pi]"

# 5. .env (preserve previous values if re-run)
echo "[5/6] Writing $INSTALL_DIR/.env..."
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

# 6. Systemd unit
echo "[6/6] Installing systemd unit..."
cp "$INSTALL_DIR/systemd/palantir-pi-relay.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable palantir-pi-relay.service
systemctl restart palantir-pi-relay.service

echo
echo "=== Done ==="
echo "  status:   sudo systemctl status palantir-pi-relay --no-pager -l"
echo "  logs:     journalctl -u palantir-pi-relay -f"
echo
