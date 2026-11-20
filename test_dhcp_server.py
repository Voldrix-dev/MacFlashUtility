
#!/usr/bin/env python3
"""
Тестовый DHCP-сервер для диагностики устройств.

Запуск:
    sudo python3 test_dhcp_server.py enp1s0f0
    sudo python3 test_dhcp_server.py enp1s0f0 --listen

Режимы:
    --serve   DHCP-сервер (по умолчанию)
    --listen  Только прослушивание без ответов
"""

import sys
import time
import argparse

from scapy.all import (
    sniff, sendp, Ether, IP, UDP,
    BOOTP, DHCP, get_if_hwaddr
)

SERVER_IP = "15.15.15.1"
OFFER_IP = "15.15.15.15"
SUBNET_MASK = "255.255.255.0"
LEASE_TIME = 3600

MSG_NAMES = {
    1: "DISCOVER",
    2: "OFFER",
    3: "REQUEST",
    4: "DECLINE",
    5: "ACK",
    6: "NAK",
    7: "RELEASE",
    8: "INFORM",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Test DHCP server")
    parser.add_argument("interface", help="Network interface")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--serve", action="store_true",
        help="DHCP server mode (default)"
    )
    mode.add_argument(
        "--listen", action="store_true",
        help="Passive monitoring mode"
    )
    return parser.parse_args()


def get_options(pkt):
    result = {}

    for opt in pkt[DHCP].options:
        if opt == "end":
            break

        if isinstance(opt, tuple):
            result[opt[0]] = opt[1]

    return result


def message_type(options):
    value = options.get("message-type")

    if isinstance(value, bytes):
        value = value[0]

    return value


def log_packet(direction, msg, mac, xid, ciaddr, **extra):
    ts = time.strftime("%H:%M:%S")

    line = (
        f"[{ts}] {direction:2s} {msg:8s} | "
        f"MAC={mac:17s} | XID={xid:08x} | "
        f"ciaddr={ciaddr}"
    )

    for key, value in extra.items():
        if value is not None:
            line += f" | {key}={value}"

    print(line, flush=True)


def send_dhcp(iface, my_mac, pkt, msg_type, options=None):
    bootp = pkt[BOOTP]
    ether = pkt[Ether]

    client_mac = ether.src

    if options is None:
        options = get_options(pkt)

    requested = options.get("requested_addr")
    client_ip = options.get("client_id")

    if msg_type == 3:
        if requested and requested != OFFER_IP:
            reply_type = 6
            yiaddr = "0.0.0.0"
            dhcp_options = [
                ("message-type", "nak"),
                ("server_id", SERVER_IP),
                "end",
            ]
        else:
            reply_type = 5
            yiaddr = OFFER_IP
            dhcp_options = [
                ("message-type", "ack"),
                ("server_id", SERVER_IP),
                ("lease_time", LEASE_TIME),
                ("subnet_mask", SUBNET_MASK),
                ("router", SERVER_IP),
                ("name_server", SERVER_IP),
                "end",
            ]

    elif msg_type == 1:
        reply_type = 2
        yiaddr = OFFER_IP

        dhcp_options = [
            ("message-type", "offer"),
            ("server_id", SERVER_IP),
            ("lease_time", LEASE_TIME),
            ("subnet_mask", SUBNET_MASK),
            ("router", SERVER_IP),
            ("name_server", SERVER_IP),
            "end",
        ]

    elif msg_type == 8:
        reply_type = 5
        yiaddr = "0.0.0.0"

        dhcp_options = [
            ("message-type", "ack"),
            ("server_id", SERVER_IP),
            ("subnet_mask", SUBNET_MASK),
            ("router", SERVER_IP),
            ("name_server", SERVER_IP),
            "end",
        ]

    else:
        return

    # Не отвечаем на REQUEST, адресованный другому DHCP-серверу.
    server_id = options.get("server_id")

    if msg_type == 3 and server_id and server_id != SERVER_IP:
        print(
            f"    [SKIP] REQUEST предназначен серверу {server_id}",
            flush=True
        )
        return

    reply = (
        Ether(
            src=my_mac,
            dst="ff:ff:ff:ff:ff:ff"
        )
        / IP(
            src=SERVER_IP,
            dst="255.255.255.255"
        )
        / UDP(
            sport=67,
            dport=68
        )
        / BOOTP(
            op=2,
            htype=bootp.htype,
            hlen=bootp.hlen,
            hops=bootp.hops,
            xid=bootp.xid,
            secs=bootp.secs,
            flags=bootp.flags,
            ciaddr=bootp.ciaddr,
            yiaddr=yiaddr,
            siaddr=SERVER_IP,
            giaddr=bootp.giaddr,
            chaddr=bootp.chaddr
        )
        / DHCP(options=dhcp_options)
    )

    try:
        sendp(reply, iface=iface, verbose=False)

        reply_name = MSG_NAMES[reply_type]

        log_packet(
            "TX", reply_name,
            client_mac,
            bootp.xid,
            str(bootp.ciaddr),
            ip=yiaddr,
            dst="broadcast"
        )

    except Exception as exc:
        print(f"[ERROR] DHCP send failed: {exc}", flush=True)


def main():
    args = parse_args()
    iface = args.interface
    serve = not args.listen

    try:
        my_mac = get_if_hwaddr(iface)
    except Exception as exc:
        print(f"[ERROR] Interface error: {exc}")
        sys.exit(1)

    print(f"[*] Interface: {iface}")
    print(f"[*] MAC:       {my_mac}")
    print(f"[*] Mode:      {'DHCP SERVER' if serve else 'LISTEN ONLY'}")

    if serve:
        print(f"[*] Server IP: {SERVER_IP}")
        print(f"[*] Offer IP:  {OFFER_IP}")
        print(f"[*] Netmask:   {SUBNET_MASK}")
        print(f"[*] Lease:     {LEASE_TIME}s")

    print("[*] Press Ctrl+C to stop\n")

    print(
        f"{'TIME':8s} {'DIR':2s} {'TYPE':8s} "
        f"{'MAC':17s} {'XID':>8s} {'ciaddr':>15s}"
    )
    print("-" * 100)

    def handle(pkt):
        if DHCP not in pkt or BOOTP not in pkt or Ether not in pkt:
            return

        ether = pkt[Ether]

        # Игнорируем собственные исходящие пакеты.
        if ether.src.lower() == my_mac.lower():
            return

        bootp = pkt[BOOTP]
        options = get_options(pkt)

        msg_type = message_type(options)
        msg_name = MSG_NAMES.get(msg_type, f"TYPE-{msg_type}")

        requested = options.get("requested_addr")
        hostname = options.get("hostname")
        param_req = options.get("parameter_request_list")

        log_packet(
            "RX", msg_name,
            ether.src,
            bootp.xid,
            str(bootp.ciaddr),
            requested=requested,
            host=hostname,
            params=param_req
        )

        if serve:
            if msg_type in (1, 3, 8):
                send_dhcp(
                    iface,
                    my_mac,
                    pkt,
                    msg_type,
                    options
                )

    try:
        sniff(
            iface=iface,
            filter="udp and (port 67 or port 68)",
            prn=handle,
            store=False
        )

    except PermissionError:
        print("[ERROR] Run with sudo")
        sys.exit(1)

    except KeyboardInterrupt:
        print("\n[*] Stopped")


if __name__ == "__main__":
    main()
