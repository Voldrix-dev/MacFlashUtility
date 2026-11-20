#!/usr/bin/env python3
"""
Тестовый SSH-коннектор для диагностики подключения к устройству.
Не входит в проект — чисто для тестов.

Usage:
    python3 test_ssh_connect.py [host] [user] [password]
    python3 test_ssh_connect.py --host 15.15.15.15 --user root --password infinityroot
"""

import sys
import time
import argparse
import socket
import paramiko
from paramiko import transport as paramiko_transport

# Алгоритмы для dropbear (как в проекте)
_DROPBEAR_KEX = (
    "curve25519-sha256@libssh.org",
    "ecdh-sha2-nistp256",
    "diffie-hellman-group14-sha256",
    "diffie-hellman-group14-sha1",
)
_DROPBEAR_CIPHERS = (
    "aes128-ctr", "aes256-ctr", "aes128-cbc", "aes256-cbc",
)
_DROPBEAR_MACS = (
    "hmac-sha2-256", "hmac-sha2-512", "hmac-sha1",
)


def make_client():
    """Создаёт SSHClient с настройками для dropbear."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    orig_kex = paramiko_transport.Transport._preferred_kex
    orig_ciphers = paramiko_transport.Transport._preferred_ciphers
    orig_macs = paramiko_transport.Transport._preferred_macs
    paramiko_transport.Transport._preferred_kex = _DROPBEAR_KEX
    paramiko_transport.Transport._preferred_ciphers = _DROPBEAR_CIPHERS
    paramiko_transport.Transport._preferred_macs = _DROPBEAR_MACS
    orig_connect = ssh.connect
    def wrapped(*args, **kwargs):
        try:
            return orig_connect(*args, **kwargs)
        finally:
            paramiko_transport.Transport._preferred_kex = orig_kex
            paramiko_transport.Transport._preferred_ciphers = orig_ciphers
            paramiko_transport.Transport._preferred_macs = orig_macs
    ssh.connect = wrapped
    return ssh


def wait_for_port(host, port=22, timeout=60, delay=2):
    """Ждёт открытия TCP-порта."""
    print(f"[WAIT] Waiting for TCP port {port} on {host} (timeout={timeout}s)...")
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection((host, port), timeout=3):
                print(f"[OK] Port {port} is open")
                return True
        except Exception:
            time.sleep(delay)
    print(f"[ERR] Port {port} did not open in time")
    return False


def wait_stable(host, user, password, timeout=120, delay=5, log=print):
    """Ждёт успешный SSH-хендшейк."""
    log(f"[WAIT] Waiting for stable SSH handshake (timeout={timeout}s, delay={delay}s)...")
    t0 = time.time()
    while time.time() - t0 < timeout:
        ssh = None
        try:
            ssh = make_client()
            ssh.connect(host, username=user, password=password, timeout=10,
                        banner_timeout=10, auth_timeout=10,
                        look_for_keys=False, allow_agent=False)
            log("[OK] SSH handshake successful")
            return True
        except Exception as e:
            log(f"  [..] Handshake failed: {e}")
        finally:
            if ssh:
                try: ssh.close()
                except: pass
        time.sleep(delay)
    log("[ERR] SSH never stabilized")
    return False


def connect_and_test(host, user, password, command="echo OK"):
    """Подключается и выполняет команду."""
    ssh = make_client()
    try:
        print(f"[CONNECT] Connecting to {user}@{host}...")
        ssh.connect(host, username=user, password=password, timeout=10,
                    banner_timeout=12, auth_timeout=12,
                    look_for_keys=False, allow_agent=False)
        print("[OK] Connected")
        stdin, stdout, stderr = ssh.exec_command(command, timeout=10)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        code = stdout.channel.recv_exit_status()
        print(f"[CMD] {command}")
        print(f"  exit code: {code}")
        if out: print(f"  stdout: {out}")
        if err: print(f"  stderr: {err}")
        return True
    except Exception as e:
        print(f"[ERR] {e}")
        return False
    finally:
        try: ssh.close()
        except: pass


def main():
    parser = argparse.ArgumentParser(description="Test SSH connection to device")
    parser.add_argument("host", nargs="?", default="15.15.15.15", help="Target host")
    parser.add_argument("user", nargs="?", default="root", help="Username")
    parser.add_argument("password", nargs="?", default="infinityroot", help="Password")
    parser.add_argument("--port", type=int, default=22, help="SSH port")
    parser.add_argument("--port-timeout", type=int, default=60, help="Port wait timeout")
    parser.add_argument("--handshake-timeout", type=int, default=120, help="Handshake wait timeout")
    parser.add_argument("--delay", type=int, default=5, help="Delay between handshake retries")
    parser.add_argument("--cmd", default="echo OK", help="Command to run after connect")
    parser.add_argument("--skip-port-wait", action="store_true", help="Skip TCP port wait")
    parser.add_argument("--skip-handshake-wait", action="store_true", help="Skip handshake wait")
    parser.add_argument("--connect-delay", type=int, default=3, help="Delay after handshake before final connect")
    args = parser.parse_args()

    print(f"Target: {args.user}@{args.host}:{args.port}")
    print(f"Command: {args.cmd}")
    print("-" * 50)

    # 1. Wait for TCP port
    if not args.skip_port_wait:
        if not wait_for_port(args.host, args.port, args.port_timeout):
            sys.exit(1)
    else:
        print("[SKIP] TCP port wait")

    # 2. Wait for stable handshake
    if not args.skip_handshake_wait:
        if not wait_stable(args.host, args.user, args.password, args.handshake_timeout, args.delay):
            sys.exit(1)
        # Dropbear may reset if we connect too fast after handshake
        if args.connect_delay > 0:
            print(f"[WAIT] Delay {args.connect_delay}s before final connect...")
            time.sleep(args.connect_delay)
    else:
        print("[SKIP] Handshake wait")

    # 3. Connect and run command
    if not connect_and_test(args.host, args.user, args.password, args.cmd):
        sys.exit(1)

    print("-" * 50)
    print("[SUCCESS] All tests passed")


if __name__ == "__main__":
    main()