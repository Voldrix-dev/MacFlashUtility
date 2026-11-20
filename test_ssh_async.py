#!/usr/bin/env python3
"""
Тестовый SSH-коннектор на asyncssh (asyncio).
Не входит в проект — чисто для тестов.
"""

import sys
import asyncio
import argparse

async def wait_for_port(host, port=22, timeout=60, delay=2):
    """Ждёт открытия TCP-порта."""
    print(f"[WAIT] Waiting for TCP port {port} on {host} (timeout={timeout}s)...")
    import socket
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=3
            )
            writer.close()
            await writer.wait_closed()
            print(f"[OK] Port {port} is open")
            return True
        except Exception:
            await asyncio.sleep(delay)
    print(f"[ERR] Port {port} did not open in time")
    return False


async def wait_stable(host, user, password, timeout=120, delay=5):
    """Ждёт успешный SSH-хендшейк."""
    import asyncssh
    print(f"[WAIT] Waiting for stable SSH handshake (timeout={timeout}s, delay={delay}s)...")
    import time
    start = time.time()
    while time.time() - start < timeout:
        try:
            async with asyncssh.connect(
                host, username=user, password=password,
                known_hosts=None,  # Auto-accept
                login_timeout=10,
                connect_timeout=10,
            ) as conn:
                print("[OK] SSH handshake successful")
                return True
        except Exception as e:
            print(f"  [..] Handshake failed: {e}")
        await asyncio.sleep(delay)
    print("[ERR] SSH never stabilized")
    return False


async def connect_and_test(host, user, password, command="echo OK"):
    """Подключается и выполняет команду."""
    import asyncssh
    try:
        print(f"[CONNECT] Connecting to {user}@{host}...")
        async with asyncssh.connect(
            host, username=user, password=password,
            known_hosts=None,
            login_timeout=12,
            connect_timeout=12,
        ) as conn:
            print("[OK] Connected")
            result = await conn.run(command, timeout=10)
            print(f"[CMD] {command}")
            print(f"  exit code: {result.exit_status}")
            if result.stdout:
                print(f"  stdout: {result.stdout.strip()}")
            if result.stderr:
                print(f"  stderr: {result.stderr.strip()}")
            return True
    except Exception as e:
        print(f"[ERR] {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="Test SSH connection (asyncssh)")
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
        if not await wait_for_port(args.host, args.port, args.port_timeout):
            sys.exit(1)
    else:
        print("[SKIP] TCP port wait")

    # 2. Wait for stable handshake
    if not args.skip_handshake_wait:
        if not await wait_stable(args.host, args.user, args.password, args.handshake_timeout, args.delay):
            sys.exit(1)
        if args.connect_delay > 0:
            print(f"[WAIT] Delay {args.connect_delay}s before final connect...")
            await asyncio.sleep(args.connect_delay)
    else:
        print("[SKIP] Handshake wait")

    # 3. Connect and run command
    if not await connect_and_test(args.host, args.user, args.password, args.cmd):
        sys.exit(1)

    print("-" * 50)
    print("[SUCCESS] All tests passed")


if __name__ == "__main__":
    asyncio.run(main())