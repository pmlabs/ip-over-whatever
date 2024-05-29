#!/usr/bin/env python3

import argparse
import socket
import os
import select
import time
import logging

READ_BUFFER_SIZE = 1024

logger = logging.getLogger("tcp-client")

def tcp_connect(host, port, mode, fifo_in, fifo_out):
    mode_in = mode.lower().find('i') != -1
    mode_out = mode.lower().find('o') != -1

    if mode_in:
        if not os.path.exists(fifo_in):
            logger.info(f"FIFO '{fifo_in}' doesn't exist")
            return
        fifo_in_fd = os.open(fifo_in, os.O_RDONLY | os.O_NONBLOCK)
        logger.info(f"FIFO {fifo_in} opened for reading (in)")

    if mode_out:
        if not os.path.exists(fifo_out):
            logger.info(f"FIFO '{fifo_out}' doesn't exist")
            return
        fifo_out_fd = os.open(fifo_out, os.O_WRONLY)
        logger.info(f"FIFO {fifo_out} opened for writing (out)")

    while True:
        try:
            logger.info(f"Connecting to {host}:{port}")
            client_socket = socket.socket()
            client_socket.connect((host, port))
            logger.info("Connected")

            inputs = [client_socket]
            if mode_in:
                inputs.append(fifo_in_fd)

            while True:
                readable, _, _ = select.select(inputs, [], [])

                for fd in readable:
                    if mode_in and fd == fifo_in_fd:
                        fifo_data = os.read(fifo_in_fd, READ_BUFFER_SIZE)
                        if fifo_data:
                            try:
                                client_socket.sendall(fifo_data)
                            except Exception as e:
                                logger.info(inputs)
                                logger.info(client_socket)
                                inputs.remove(client_socket)
                                client_socket.close()
                    else:
                        client_data = fd.recv(READ_BUFFER_SIZE)
                        if mode_out and client_data:
                            os.write(fifo_out_fd, client_data)
        except Exception as e:
            logger.info(e)
            time.sleep(1)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="TCP client")
    parser.add_argument('-c', '--connect-addr', type=str, help='Remote host')
    parser.add_argument('-p', '--port', type=int, help='Remote port', default=6446)
    parser.add_argument('-i', '--fifo-in', type=str, help='FIFO in', default='/var/run/tun_out.fifo')
    parser.add_argument('-o', '--fifo-out', type=str, help='FIFO out', default='/var/run/tun_in.fifo')
    parser.add_argument("-m", "--mode", type=str, help='Mode: in/out/inout', default='inout')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] [%(levelname)s] %(funcName)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    if args.connect_addr is None or args.port is None:
        parser.error("Remote address and port needed")

    tcp_connect(args.connect_addr, args.port, args.mode, args.fifo_in, args.fifo_out)
