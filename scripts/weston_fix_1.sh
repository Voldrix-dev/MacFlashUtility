#!/bin/bash

set -e

echo "[STEP 1] Disable + mask stock Weston..."
systemctl disable --now weston.service 2>/dev/null || true
systemctl mask weston.service 2>/dev/null || true

echo "[STEP 2] Disable + mask getty on tty1..."
systemctl disable --now getty@tty1.service 2>/dev/null || true
systemctl mask getty@tty1.service 2>/dev/null || true

echo "[STEP 3] Create Weston Kiosk service..."

cat > /etc/systemd/system/weston-kiosk.service << 'EOF'
[Unit]
Description=Weston Kiosk Mode
After=systemd-logind.service
Requires=systemd-logind.service
Conflicts=getty@tty1.service

[Service]
Type=simple
User=root

ExecStart=/usr/bin/weston \
  --backend=drm-backend.so \
  --tty=1 \
  --idle-time=0 \
  --shell=kiosk-shell.so \
  --log=/var/log/weston.log

Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF

echo "[STEP 4] systemd reload..."
systemctl daemon-reload

echo "[STEP 5] Enable services..."
systemctl enable weston-kiosk.service

echo "[DONE] Installation complete"
echo "Rebooting in 3 seconds..."

sleep 3
/sbin/reboot
