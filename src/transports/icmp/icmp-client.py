#!/usr/bin/env python3

import os
import select
import threading
from scapy.all import *
import argparse
import logging
import queue
import sys
import time

MAX_ICMP_PAYLOAD_SIZE=1472

logger = logging.getLogger("icmp-client")

fifo_queue = queue.Queue(maxsize=MAX_ICMP_PAYLOAD_SIZE)

def handle_icmp(mode_in, mode_out, addr, fifo_out, keep_alive):
    if mode_out:
        fifo_out_fd = os.open(fifo_out, os.O_WRONLY)

    last_empty = True

    while True:
        if mode_in:
            try:
                data = fifo_queue.get(block=last_empty, timeout=keep_alive)
                last_empty = False
                logger.info(f"Received '{len(data)} from queue")
            except queue.Empty:
                logger.info(f"Queue empty")
                last_empty = True
                data = b""
        else:
            time.sleep(keep_alive)
            data = b""

        reply = sr1(IP(dst=addr)/ICMP()/data, verbose=False)
        if reply and 'Raw' in reply:
            payload = reply['Raw'].load
            logger.info(f"Sent {len(data)} bytes, got {len(payload)} bytes in reply")
            if mode_out:
                if mode_out and payload:
                    logger.info(f"Sending {len(payload)} to fifo_out_fd={fifo_out_fd}")
                    os.write(fifo_out_fd, payload)



def handle_fifo(mode_in, mode_out, fifo_in):
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
    parser.add_argument("-i", "--fifo-in", type=str, help='FIFO in', default='/var/run/tun_out.fifo')
    parser.add_argument("-o", "--fifo-out", type=str, help='FIFO out', default='/var/run/tun_in.fifo')
    parser.add_argument("-m", "--mode", type=str, help='Mode: in/out/inout', default='inout')
    parser.add_argument('-c', '--connect-addr', type=str, help='Remote host')
    parser.add_argument('-k', '--keep-alive', type=float, help='Keep alive in seconds (default 1.0)', default=1.0)
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


    icmp_thread = threading.Thread(target=handle_icmp, args=(mode_in, mode_out, args.connect_addr, args.fifo_out, args.keep_alive))
    fifo_thread = threading.Thread(target=handle_fifo, args=(mode_in, mode_out, args.fifo_in,))

    icmp_thread.start()
    logger.info("icmp_thread started")

    fifo_thread.start()
    logger.info("fifo_thread started")

    icmp_thread.join()
    fifo_thread.join()
