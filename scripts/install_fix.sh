#!/bin/bash
set -e

echo "[STEP 1] Disable old weston service..."
systemctl disable weston.service 2>/dev/null || true
systemctl stop weston.service 2>/dev/null || true

echo "[STEP 2] Disable getty on tty1..."
systemctl disable getty@tty1 2>/dev/null || true
systemctl stop getty@tty1 2>/dev/null || true

echo "[STEP 3] Creating weston-kiosk.service..."

cat > /lib/systemd/system/weston-kiosk.service << 'EOF'
[Unit]
Description=Weston Kiosk
After=systemd-user-sessions.service

[Service]
Type=simple

Environment=XDG_RUNTIME_DIR=/run/user/0
Environment=WAYLAND_DISPLAY=wayland-0

ExecStartPre=/bin/mkdir -p /run/user/0
ExecStartPre=/bin/chmod 700 /run/user/0

ExecStart=/usr/bin/weston \
  --backend=drm-backend.so \
  --tty=1 \
  --idle-time=0

Restart=always
RestartSec=1

TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty

[Install]
WantedBy=multi-user.target
EOF

echo "[STEP 4] Reload systemd..."
systemctl daemon-reexec
systemctl daemon-reload

echo "[STEP 5] Enable services..."
systemctl enable weston-kiosk.service

echo "[DONE] Setup complete!"
echo "Rebooting in 3 seconds..."

sleep 3
reboot
