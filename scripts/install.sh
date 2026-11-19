#!/bin/bash

echo "[STEP 1] Disable connman"
systemctl disable --now connman 2>/dev/null || true
systemctl disable --now mac_set 2>/dev/null || true

