#!/bin/bash

#ssh root@host 'bash -s' < install_fix_mod.sh


set -e

echo "[STEP 1] Disable old weston service..."
systemctl disable weston.service 2>/dev/null || true
systemctl stop weston.service 2>/dev/null || true

echo "[STEP 2] Disable getty on tty1..."
systemctl disable getty@tty1 2>/dev/null || true
systemctl stop getty@tty1 2>/dev/null || true

echo "[STEP 3] Create weston-kiosk.service from original..."

rm -f /lib/systemd/system/weston-kiosk.service 2>/dev/null || true
rm -f /etc/systemd/system/weston-kiosk.service 2>/dev/null || true

cp /lib/systemd/system/weston.service /lib/systemd/system/weston-kiosk.service

echo "[STEP 3.1] Patching weston-kiosk.service..."

# Меняем описание
sed -i 's/Description=.*/Description=Weston Kiosk Mode/' /lib/systemd/system/weston-kiosk.service

# Меняем tty7 → tty1
sed -i 's#/dev/tty7#/dev/tty1#g' /lib/systemd/system/weston-kiosk.service
sed -i 's/UtmpIdentifier=tty7/UtmpIdentifier=tty1/' /lib/systemd/system/weston-kiosk.service

# Добавляем kiosk параметры в ExecStart
sed -i 's#ExecStart=.*#ExecStart=/usr/bin/weston --log=${XDG_RUNTIME_DIR}/weston.log --modules=systemd-notify.so --backend=drm-backend.so --tty=1 --idle-time=0#g' /lib/systemd/system/weston-kiosk.service

# Добавляем авто-рестарт (если нет)
grep -q "Restart=" /lib/systemd/system/weston-kiosk.service || sed -i '/\[Service\]/a Restart=always\nRestartSec=1\nStartLimitInterval=0' /lib/systemd/system/weston-kiosk.service

# Меняем target (опционально, но лучше для kiosk)
sed -i 's/WantedBy=graphical.target/WantedBy=multi-user.target/' /lib/systemd/system/weston-kiosk.service

echo "[STEP 4] Reload systemd..."
systemctl daemon-reexec
systemctl daemon-reload

echo "[STEP 5] Enable services..."
systemctl enable weston-kiosk.service

echo "[DONE] Setup complete!"
echo "Rebooting in 3 seconds..."

sleep 3
/sbin/reboot
