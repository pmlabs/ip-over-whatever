#!/usr/bin/env python3

import os
import select
import socket
import argparse

READ_BUFFER_SIZE = 1024

import logging

logger = logging.getLogger("tcp-client")

def tcp_serve(host, port, mode, fifo_in, fifo_out):
    mode_in = mode.lower().find('i') != -1
    mode_out = mode.lower().find('o') != -1

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server_socket.bind((host, port))
    server_socket.listen(10)

    logger.info(f"Listening on {host}:{port}")

    if mode_in:
        if not os.path.exists(fifo_in):
            logger.info(f"Fifo in '{fifo_in}' doesn't exist")
            return
        fifo_in_fd = os.open(fifo_in, os.O_RDONLY | os.O_NONBLOCK)
        logger.info(f"FIFO {fifo_in} opened for reading (in)")

    if mode_out:
        if not os.path.exists(fifo_out):
            logger.info(f"Fifo out '{fifo_out}' doesn't exist")
            return
        fifo_out_fd = os.open(fifo_out, os.O_WRONLY)
        logger.info(f"FIFO {fifo_out} opened for writing (out)")

    inputs = [server_socket]
    if mode_in:
        inputs.append(fifo_in_fd)

    while True:
        readable, _, _ = select.select(inputs, [], [])

        for fd in readable:
            if fd == server_socket:
                client_socket, client_address = server_socket.accept()
                logger.info(f"Connection from {client_address}, appending fd:{client_socket.fileno()} to inputs")
                inputs.append(client_socket)
            elif mode_in and fd == fifo_in_fd:
                fifo_data = os.read(fifo_in_fd, READ_BUFFER_SIZE)
                logger.info(f"Got {len(fifo_data)} from fifo_in (fd:{fifo_in_fd})")
                if fifo_data:
                    for client_socket in inputs:
                        if client_socket != server_socket and client_socket != fifo_in_fd:
                            try:
                                client_socket.sendall(fifo_data)
                                logger.info(f"Sent {len(fifo_data)} to client_socket (fd:{client_socket.fileno()})")
                            except Exception as e:
                                logger.info(e)
                                logger.info(f"Removing {client_socket.fileno()}")
                                inputs.remove(client_socket)

            else:
                client_data = fd.recv(READ_BUFFER_SIZE)
                if mode_out and client_data:
                    logger.info(f"Got {len(client_data)} from client (fd:{fd.fileno()})")
                    os.write(fifo_out_fd, client_data)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TCP server")
    parser.add_argument("-l", "--listen-addr", help="Listening address", default="0.0.0.0")
    parser.add_argument("-p", "--port", type=int, help="Listening port", default=6446)
    parser.add_argument("-i", "--fifo-in", type=str, help='FIFO in', default='/var/run/tun_out.fifo')
    parser.add_argument("-o", "--fifo-out", type=str, help='FIFO out', default='/var/run/tun_in.fifo')
    parser.add_argument("-m", "--mode", type=str, help='Mode: in/out/inout', default='inout')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] [%(levelname)s] %(funcName)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    tcp_serve(args.listen_addr, args.port, args.mode, args.fifo_in, args.fifo_out)
