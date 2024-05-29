FROM gcc
RUN apt update && apt install -y vim iproute2 net-tools iputils-ping screen tcpdump socat python3-scapy
COPY src /app
WORKDIR /app
RUN make
ENV SHELL=/bin/bash
