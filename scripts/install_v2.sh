#!/bin/bash
###
#ssh -y -oStrictHostKeyChecking=no -oHostKeyAlgorithms=+ssh-rsa root@host 'bash -s' < install_v2.sh 
#[STEP 1] Disable old weston + getty...
#[STEP 2] Create weston-kiosk.service (FORCED OVERRIDE)...
#[STEP 3] systemd reload...
#[STEP 4] enable services...
#Created symlink /etc/systemd/system/graphical.target.wants/weston-kiosk.service → /etc/systemd/system/weston-#kiosk.service.
#[STEP 5] cleanup...
#[DONE] rebooting...
###

set -e

echo "[STEP 1] Disable old weston, getty, connman..."
systemctl disable --now weston.service 2>/dev/null || true
systemctl disable --now getty@tty1 2>/dev/null || true
systemctl disable --now connman 2>/dev/null || true

echo "[STEP 2] Create weston-kiosk.service (FORCED OVERRIDE)..."
rm -f /lib/systemd/system/weston-kiosk.service 2>/dev/null || true
rm -f /etc/systemd/system/weston-kiosk.service 2>/dev/null || true

cat > /etc/systemd/system/weston-kiosk.service << 'EOF'
[Unit]
Description=Weston Kiosk Mode

Requires=systemd-user-sessions.service
After=systemd-user-sessions.service

Wants=dbus.socket
After=dbus.socket

#Requires=weston.socket

Before=graphical.target

ConditionPathExists=/dev/tty0

[Service]
Type=notify
EnvironmentFile=/etc/default/weston
ExecStart=/usr/bin/weston --backend=drm-backend.so --tty=1 --idle-time=0 --shell=kiosk-shell.so --modules=systemd-notify.so --log=${XDG_RUNTIME_DIR}/weston.log

User=root
Group=root

#WorkingDirectory=/home/weston
WorkingDirectory=/

PAMName=weston-autologin

TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes

StandardInput=tty-fail
StandardOutput=journal
StandardError=journal

UtmpIdentifier=tty1
UtmpMode=user

[Install]
WantedBy=graphical.target
EOF

echo "[STEP 3] systemd reload..."
systemctl daemon-reload
systemctl daemon-reexec

echo "[STEP 4] enable services..."
systemctl enable weston-kiosk.service

echo "[STEP 5] cleanup..."
rm -f /run/user/0/weston.log 2>/dev/null || true

echo "[DONE] rebooting..."

sleep 2
#/sbin/reboot
systemctl reboot
