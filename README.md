## ip-over-whetever daemon (ipowd)

Server `ipowd` tworzy nowy interfejs TUN oraz parę FIFO - `/var/run/tun_in.fifo`, z którego odczytywane są dane, które następnie trafią do interfejsu TUN oraz `/var/run/tun_out.fifo`, do którego trafią dane odczytane z interfejsu TUN.
Transport realizujący komunikację odpowiednio powinien odpowiednio czytać i pisac do tej pary FIFO. Mozliwe są warianty asynchroniczne - jeden transport realizuje wysyłanie danych, a inny odbieranie.

## Trash

### IN/OUT synchronicznie
- box1
```sh
./ipowd
ifconfig tun0 10.0.0.1 pointopoint 10.0.0.2 netmask 255.255.255.255 up
python3 transports/tcp-server.py
ping 10.0.0.2
```

- box2
```sh
./ipowd
ifconfig tun0 10.0.0.2 pointopoint 10.0.0.1 netmask 255.255.255.255 up
python3 transports/tcp-client.py -c box1
ping 10.0.0.1
```

### IN/OUT asynchronicznie:
- box1
```sh
./ipowd
ifconfig tun0 10.0.0.1 pointopoint 10.0.0.2 netmask 255.255.255.255 up
python3 transports/tcp-client.py -c box2 -m in
python3 transports/tcp-server.py -m out
ping 10.0.0.2
```

- box2
```sh
./ipowd
python3 transports/tcp-server.py --mode out
python3 transports/tcp-client.py -m in -c box1
ifconfig tun0 10.0.0.2 pointopoint 10.0.0.1 netmask 255.255.255.255 up
ping 10.0.0.1
```

XXX: podmienić ipowd.c z ipowd2.c, dostosować transporty udp, icmp i dns do obsługi socketów zamiast fifo.
