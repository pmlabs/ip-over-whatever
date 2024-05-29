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
import dns.resolver
import re
import random
import traceback

MAX_QUEUE_SIZE=1472
MAX_DOMAIN_LENGTH=128
MAX_SUBDOMAIN_LENGTH=32

logger = logging.getLogger("dns-client")

fifo_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)

def handle_dns(mode_in, mode_out, server, domain, fifo_out, keep_alive):
    serial = random.randint(1000000, 9999999)
    no = 0

    resolver = dns.resolver.Resolver()
    resolver.nameservers = [server]

    if mode_out:
        fifo_out_fd = os.open(fifo_out, os.O_WRONLY)

    last_empty = True

    while True:

        if mode_in:
            try:
                data = fifo_queue.get(block=last_empty, timeout=keep_alive)
                last_empty = False
                logger.info(f"Received {len(data)} from queue")
            except queue.Empty:
                logger.info(f"Queue empty")
                last_empty = True
                data = b""
        else:
            time.sleep(keep_alive)
            data = b""

        data = data.hex()
        if data == '':
            data = 'ZZ'

        try:
            chunks = [data[i:i + MAX_DOMAIN_LENGTH] for i in range(0, len(data), MAX_DOMAIN_LENGTH)]
            for chunk in chunks:
                no = no + 1
                records = []
                subdomain = '.'.join(chunk[i:i+MAX_SUBDOMAIN_LENGTH] for i in range(0, len(chunk), MAX_SUBDOMAIN_LENGTH))
                query_domain = f"{no}.{serial}.{domain}"
                query_domain = subdomain.strip('.') + '.' + query_domain
                print(f"Resolving {query_domain}")
                answers = resolver.resolve(query_domain)
                for a in answers:
                    if a.rdtype.value == 16:
                        records.append(str(a))
                records.sort()
                for r in records:
                    re.sub(r'^\d+\.', '', r)
                    try:
                        b = bytes.fromhex(r)
                        os.wrte(fifo_out_fd, b)
                    except Exception as e:
                        print(traceback.format_exc())


        except Exception as e:
            print(traceback.format_exc())

def handle_fifo(mode_in, mode_out, fifo_in):
    if not mode_in:
        return 

    while True:
        try:
            fifo_fd = os.open(fifo_in, os.O_RDONLY | os.O_NONBLOCK)
            print(fifo_fd)
            while True:
                #r, r1, r2 = select.select([fifo_fd], [], [])
                r, r1, r2 = select([fifo_fd], [], [])
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
    parser = argparse.ArgumentParser(description="dns listener")
    parser.add_argument("-i", "--fifo-in", type=str, help='FIFO in', default='/var/run/tun_out.fifo')
    parser.add_argument("-o", "--fifo-out", type=str, help='FIFO out', default='/var/run/tun_in.fifo')
    parser.add_argument("-m", "--mode", type=str, help='Mode: in/out/inout', default='inout')
    parser.add_argument('-d', '--domain', type=str, help='Domain to resolve', required=True)
    parser.add_argument('-s', '--server', type=str, help='Remote DNS server', required=True)
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


    dns_thread = threading.Thread(target=handle_dns, args=(mode_in, mode_out, args.server, args.domain, args.fifo_out, args.keep_alive))
    fifo_thread = threading.Thread(target=handle_fifo, args=(mode_in, mode_out, args.fifo_in,))

    dns_thread.start()
    logger.info("dns_thread started")

    fifo_thread.start()
    logger.info("fifo_thread started")

    dns_thread.join()
    fifo_thread.join()
