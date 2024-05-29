#!/usr/bin/env python3

import os
import select
import threading
from scapy.all import *
import argparse
import logging
import queue
import sys
import re

MAX_QUEUE_SIZE=1472
MAX_TXT_RECORD=200

logger = logging.getLogger("dns-listener")

fifo_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)

def handle_dns(server_ip, mode, fifo_out):
    mode_in = mode.lower().find('i') != -1
    mode_out = mode.lower().find('o') != -1

    fifo_out_fd = None

    if mode_out:
        if not os.path.exists(fifo_out):
            logger.info(f"Fifo out '{fifo_out}' doesn't exist")
            return
        fifo_out_fd = os.open(fifo_out, os.O_WRONLY)
        logger.info(f"FIFO {fifo_out} opened for writing (out)")

    sniff(filter=f"udp and port 53 and ip dst {server_ip}", prn=handle_dns_reply(mode, fifo_out_fd))

def handle_dns_reply(mode, fifo_out_fd):
    mode_in = mode.lower().find('i') != -1
    mode_out = mode.lower().find('o') != -1

    def create_dns_response(qname, qtype):
        if qtype == 1:  # 1 oznacza rekord typu A
            return DNSRR(rrname=qname, type='A', ttl=10, rdata='127.0.0.1')
        elif qtype == 16:  # 16 oznacza rekord typu TXT
            print(f"query for: {qname}")
            data = b""

            if mode_out:
                if mode_out:
                    m = re.match(r'^(.*)\.[\d]+\.[\d]+\.c\..*$', qname)
                    if m:
                        payload = m.group(1)
                        payload = payload.replace('.', '')
                        if payload and payload.upper() != 'ZZ':
                            try:
                                payload_decoded = bytes.fromhex(payload)
                                os.write(fifo_out_fd, payload_decoded)
                            except Exception as e:
                                print(f"Can't decode {payload} from hex")
                                print(e)
                        else:
                            print(f"Got ZZ ({payload})")

            if mode_in:
                try:
                    data = fifo_queue.get_nowait()
                except queue.Empty:
                    data = b""

            print(f"data len: {len(data)}")
            data = '1.' + ''.join(f'{byte:02x}' for byte in data)

            return DNSRR(rrname=qname, type='TXT', ttl=10, rdata=data)
        else:
            return None

    def dns_reply(pkt):
        if DNS in pkt and pkt[DNS].opcode == 0:  # Opcode 0 oznacza standardowe zapytanie DNS
            qname = pkt[DNSQR].qname.decode('ascii')
            qtype = pkt[DNSQR].qtype

            print(f"Received DNS query for {qname} (Type {qtype}) from {pkt[IP].src}")

            dns_response = create_dns_response(qname, qtype)

            if dns_response:
                dns_reply = DNS(id=pkt[DNS].id, qr=1, aa=1, qd=pkt[DNS].qd, an=dns_response)
                ip_reply = IP(src=pkt[IP].dst, dst=pkt[IP].src)
                udp_reply = UDP(sport=pkt[UDP].dport, dport=pkt[UDP].sport)

                send(ip_reply/udp_reply/dns_reply, verbose=0)

    return dns_reply 

def handle_fifo(fifo_in):
    if not mode_in:
        return 

    while True:
        try:
            fifo_fd = os.open(fifo_in, os.O_RDONLY | os.O_NONBLOCK)
            while True:
                r, _, _ = select.select([fifo_fd], [], [])
                if r:
                    data = os.read(fifo_fd, 1024)
                    if data:
                        logger.info(f"Received '{len(data)} from fifo_in")
                        fifo_queue.put(data)
                    else:
                        os.close(fifo_fd)
                        break
        except OSError:
            print("os error")
            continue

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DNS server")
    parser.add_argument("-s", "--dns-server-ip", help="DNS Server IP", required=True)
    parser.add_argument("-i", "--fifo-in", type=str, help='FIFO in', default='/var/run/tun_out.fifo')
    parser.add_argument("-o", "--fifo-out", type=str, help='FIFO out', default='/var/run/tun_in.fifo')
    parser.add_argument("-m", "--mode", type=str, help='Mode: in/out/inout', default='inout')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] [%(levelname)s] %(funcName)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    mode_in = args.mode.lower().find('i') != -1
    mode_out = args.mode.lower().find('o') != -1

    if mode_in:
        if not os.path.exists(args.fifo_in):
            logger.info(f"Fifo in '{args.fifo_in}' doesn't exist")
            sys.exit()

    if mode_out:
        if not os.path.exists(args.fifo_out):
            logger.info(f"Fifo out '{args.fifo_out}' doesn't exist")
            sys.exit()

    dns_thread = threading.Thread(target=handle_dns, args=(args.interface, args.mode, args.fifo_out))
    fifo_thread = threading.Thread(target=handle_fifo, args=(args.fifo_in,))

    dns_thread.start()
    logger.info("dns_thread started")

    fifo_thread.start()
    logger.info("fifo_thread started")

    dns_thread.join()
    fifo_thread.join()
