#!/usr/bin/env bash
# Idempotent, ADDITIVE deploy on the shared OCI VM. Run from ~/voice-order on the VM.
# Touches only new ports (7871/7880/7881/7882), new voicedt-* units, and an nginx include.
set -euo pipefail
cd /home/ubuntu/voice-order

# 1) render livekit.yaml keys from .env
set -a; . ./.env; set +a
sed -e "s|__LIVEKIT_API_KEY__|${LIVEKIT_API_KEY}|" \
    -e "s|__LIVEKIT_API_SECRET__|${LIVEKIT_API_SECRET}|" \
    deploy/livekit.yaml.tmpl > deploy/livekit.yaml
echo "rendered deploy/livekit.yaml"

# 2) systemd units
sudo cp deploy/voicedt-livekit.service deploy/voicedt-agent.service deploy/voicedt-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now voicedt-livekit voicedt-web voicedt-agent
sleep 2
systemctl is-active voicedt-livekit voicedt-web voicedt-agent || true

# 3) firewall (best-effort; OCI security list must also allow these inbound)
sudo ufw allow 7881/tcp 2>/dev/null || true
sudo ufw allow 7882/udp 2>/dev/null || true

# 4) nginx — additive include inside the existing oracle-aicoe.com server block
sudo cp deploy/nginx-voice-drivethru.locations /etc/nginx/voice-drivethru.locations
if ! sudo grep -q "voice-drivethru.locations" /etc/nginx/sites-available/orascreen-demo; then
  sudo sed -i '0,/server_name oracle-aicoe.com;/s//server_name oracle-aicoe.com;\n    include \/etc\/nginx\/voice-drivethru.locations;/' /etc/nginx/sites-available/orascreen-demo
  echo "added nginx include"
fi
sudo nginx -t && sudo systemctl reload nginx
echo "=== status ==="
systemctl --no-pager --type=service | grep voicedt || true
curl -s http://127.0.0.1:7871/api/healthz && echo
echo "DEPLOY DONE — https://oracle-aicoe.com/voice-drivethru/"
