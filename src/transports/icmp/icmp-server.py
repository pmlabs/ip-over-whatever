#!/usr/bin/env python3

# sysctl net.ipv4.icmp_echo_ignore_all=1

import os
import select
import threading
from scapy.all import *
import argparse
import logging
import queue
import sys

MAX_ICMP_PAYLOAD_SIZE=1472

logger = logging.getLogger("icmp-listener")

fifo_queue = queue.Queue(maxsize=MAX_ICMP_PAYLOAD_SIZE)

def handle_icmp(interface, mode, fifo_out):
    mode_in = mode.lower().find('i') != -1
    mode_out = mode.lower().find('o') != -1

    fifo_out_fd = None

    if mode_out:
        if not os.path.exists(fifo_out):
            logger.info(f"Fifo out '{fifo_out}' doesn't exist")
            return
        fifo_out_fd = os.open(fifo_out, os.O_WRONLY)
        logger.info(f"FIFO {fifo_out} opened for writing (out)")


    sniff(prn=handle_icmp_reply(mode, fifo_out_fd), filter="icmp and icmp[icmptype] == icmp-echo", store=0, iface=interface) 

def handle_icmp_reply(mode, fifo_out_fd):
    mode_in = mode.lower().find('i') != -1
    mode_out = mode.lower().find('o') != -1

    def icmp_reply(pkt):
        try:
            payload = pkt[3].load
        except:
            payload = b""

        logger.info(f"Got {len(payload)} bytes from client")

        if mode_out:
            if mode_out and payload:
                os.write(fifo_out_fd, payload)

        if mode_in:
            try:
                data = fifo_queue.get_nowait()
            except queue.Empty:
                data = b""
        else:
            data = payload

        ip = IP(dst=pkt[IP].src, src=pkt[IP].dst)
        icmp = ICMP(type=0, id=pkt[ICMP].id, seq=pkt[ICMP].seq)
        reply_pkt = ip/icmp/data

        logger.info(f"sending answer to {pkt[IP].src}: {data}")

        send(reply_pkt, verbose=False)

    return icmp_reply

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
    parser = argparse.ArgumentParser(description="ICMP listener")
    parser.add_argument("-I", "--interface", help="Listening interface", default="eth0")
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


    icmp_thread = threading.Thread(target=handle_icmp, args=(args.interface, args.mode, args.fifo_out))
    fifo_thread = threading.Thread(target=handle_fifo, args=(args.fifo_in,))

    icmp_thread.start()
    logger.info("icmp_thread started")

    fifo_thread.start()
    logger.info("fifo_thread started")

    icmp_thread.join()
    fifo_thread.join()
