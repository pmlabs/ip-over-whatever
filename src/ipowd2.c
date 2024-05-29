// Note: This version uses two datagram UNIX sockets instead of FIFOs. While the
// sockets are technically bidirectional, we're using them only in one
// direction. The sockets are nonblocking – they will drop any datagrams they
// can't handle.
#include <linux/if.h>
#include <linux/if_tun.h>
#include <sys/ioctl.h>
#include <string.h>
#include <fcntl.h>
#include <linux/string.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <signal.h>
#include <stdarg.h>
#include <time.h>
#include <stdint.h>
#include <stdbool.h>

#define BUFSIZE 20480
#define MAX_PACKET_SZ (BUFSIZE-2)  // Should be larger than MTU.

void writelog(const char *level, const char *format, ...) {
    char time_str[20]; // Buffer for time string
    strftime(time_str, 20, "%Y-%m-%d %H:%M:%S", localtime(&(time_t){time(NULL)}));
    printf("[%s] [%s] ", time_str, level);

    va_list args;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
}

int create_unix_dgram_socket(struct sockaddr_un *addr) {
  int s;

  if ((s = socket(AF_UNIX, SOCK_DGRAM, 0)) == -1) {
    fprintf(stderr, "Can't create socket %s: ", addr->sun_path);
    perror(NULL);
    return -1;
  }

  if (fcntl(s, F_SETFL, O_NONBLOCK) == -1) {
    fprintf(stderr, "Failed to make socket nonblocking %s: ", addr->sun_path);
    perror(NULL);
    return -1;
  }

  if (unlink(addr->sun_path) == -1 && errno != ENOENT) {
    fprintf(stderr, "Can't unlink %s (still active?): ", addr->sun_path);
    perror(NULL);
    return -1;
  }

  if (bind(s, (struct sockaddr*)addr, SUN_LEN(addr)) == -1) {
    fprintf(stderr, "Bind to path failed for %s: ", addr->sun_path);
    perror(NULL);
    return -1;
  }

  chmod(addr->sun_path, 0666);  // Best effort.

  return s;
}

int fifo_out_write(
    int fifo_fd, struct sockaddr_un *fifo_addr,
    void *buff, int len
) {
  int rv = -1;

  if (fifo_fd >= 0) {
    rv = sendto(fifo_fd, buff, len, 0,
                (struct sockaddr*)fifo_addr, sizeof(*fifo_addr));

    if (rv == -1) {
      if (errno == EAGAIN || errno == EWOULDBLOCK) {
        writelog("INFO", "dropping outgoing packet of %i bytes\n", len);
        return 0;
      }

      if (errno == ENOENT) {
        return -1;  // Client lost.
      }

      perror("Can't send to fifo_out: ");
      return -1;
    }
  }

  return rv;
}

int tun_alloc(char *dev) {
  struct ifreq ifr;
  int fd, err;

  if ((fd = open("/dev/net/tun", O_RDWR)) < 0) {
    fprintf(stderr, "Can't open tun (%s): ", dev);
    perror(NULL);
    return fd;
  }

  memset(&ifr, 0, sizeof(ifr));

  ifr.ifr_flags = IFF_TUN | IFF_NO_PI;
  if (*dev) {
    strncpy(ifr.ifr_name, dev, IFNAMSIZ);
  }

  if ((err = ioctl(fd, TUNSETIFF, (void *)&ifr)) < 0) {
    perror("tun_alloc: ioctl error");
    close(fd);
    return err;
  }

  strcpy(dev, ifr.ifr_name);
  return fd;
}

int main(int argc, char **argv) {
  int rv;
  char ifname[IFNAMSIZ] = "";
  const char *fifo_in = "/var/run/tun_in.fifo";
  const char *fifo_out = "/var/run/tun_out.fifo";
  int fifo_fd_in, fifo_fd_out, tun_fd;
  int maxfd;

  uint8_t *buff = (uint8_t*)malloc(BUFSIZE);

  struct sockaddr_un addr_tun_in;
  memset(&addr_tun_in, 0, sizeof(addr_tun_in));
  addr_tun_in.sun_family = AF_UNIX;
  strncpy(addr_tun_in.sun_path, fifo_in, sizeof(addr_tun_in.sun_path) - 1);

  struct sockaddr_un addr_tun_out;
  memset(&addr_tun_out, 0, sizeof(addr_tun_out));
  addr_tun_out.sun_family = AF_UNIX;
  strncpy(addr_tun_out.sun_path, fifo_out, sizeof(addr_tun_out.sun_path) - 1);

  bool tun_out_client_known = false;
  struct sockaddr_un addr_tun_out_client;
  memset(&addr_tun_out_client, 0, sizeof(addr_tun_out_client));

  tun_fd = tun_alloc(ifname);
  writelog("INFO", "tun_alloc: tun_fd=%d, ifname=%s\n", tun_fd, ifname);

  fifo_fd_in = create_unix_dgram_socket(&addr_tun_in);
  writelog("INFO", "fifo_in_prepare: fifo_in=%s, fifo_fd_in=%d\n", fifo_in, fifo_fd_in);

  fifo_fd_out = create_unix_dgram_socket(&addr_tun_out);
  writelog("INFO", "fifo_out_prepare: fifo_out=%s, fifo_fd_out=%d\n", fifo_out, fifo_fd_out);

  if (fifo_fd_in == -1 || fifo_fd_out == -1) {
    writelog("ERROR", "failed to create sockets\n");
    return 1;
  }

  writelog("INFO", "Setup done, perhaps you want to set up a tunnel, for example with something like:\n\tip addr add 10.0.0.1 peer 10.0.0.2 dev %s\n\tip link set %s up\nor with the old ifconfig:\n\tifconfig %s 10.0.0.1 pointopoint 10.0.0.2 netmask 255.255.255.255 up\nand something similar on the other end..\n", ifname, ifname, ifname);
 
  maxfd = (tun_fd > fifo_fd_in) ? tun_fd : fifo_fd_in;
  maxfd = (maxfd > fifo_fd_out) ? maxfd : fifo_fd_out;

  while (1) {
    int ret;
    int nread;
    fd_set rd_set;

    FD_ZERO(&rd_set);
    FD_SET(tun_fd, &rd_set);
    FD_SET(fifo_fd_in, &rd_set);
    FD_SET(fifo_fd_out, &rd_set);

    ret = select(maxfd + 1, &rd_set, NULL, NULL, NULL);

    if (ret < 0 && errno == EINTR) {
      continue;
    }

    if (ret < 0) {
      perror("select()");
    }

    if (FD_ISSET(tun_fd, &rd_set)) {
      if ((nread = read(tun_fd, buff, BUFSIZE)) < 0) {
        perror("tun_fd read error");
        continue;
      }
      writelog("INFO", "Read bytes from tun_fd: %d\n", nread);

      if (tun_out_client_known) {
        rv = fifo_out_write(fifo_fd_out, &addr_tun_out_client, buff, nread);
        writelog("INFO", "fifo_out_write: %d, fifo_fd_out = %d, fifo_out = %s\n", rv, fifo_fd_out, fifo_out);

        if (rv == -1) {
          tun_out_client_known = false;
          memset(&addr_tun_out_client, 0, sizeof(addr_tun_out_client));
          writelog("INFO", "fifo_out_write: client lost\n");
        }
      } else {
        writelog("INFO", "fifo_out_write: dropped packet - no one to receive it\n");
      }
    }

    if (FD_ISSET(fifo_fd_out, &rd_set)) {
      // We don't care about the data, but we need to save the address - it's
      // the client telling us where to send the data.
      socklen_t addr_sz = sizeof(addr_tun_out_client);
      nread = recvfrom(fifo_fd_out, buff, BUFSIZE, 0,
                       (struct sockaddr*)&addr_tun_out_client, &addr_sz);

      if (nread != -1) {
        tun_out_client_known = true;
        writelog("INFO", "fifo_fd_out: new client %s, %u\n",
                         addr_tun_out_client.sun_path, addr_sz);
      } else {
        writelog("INFO", "fifo_fd_out: client came and got lost again\n");
      }
    }


    if (FD_ISSET(fifo_fd_in, &rd_set)) {
      nread = recvfrom(fifo_fd_in, buff, BUFSIZE, 0, NULL, NULL);
      if (nread == 0) {
        // fifo closed on the other end
        writelog("WARNING", "fifo_fd_in, remote end closed, let's reopen it\n");
        FD_CLR(fifo_fd_in, &rd_set);
        close(fifo_fd_in);
        fifo_fd_in = create_unix_dgram_socket(&addr_tun_in);
        FD_SET(fifo_fd_in, &rd_set);
        maxfd = (tun_fd > fifo_fd_in) ? tun_fd : fifo_fd_in;
        continue;
      }

      if (nread < 0) {
        perror("fifo_fd_in read error");
        continue;
      }

      writelog("INFO", "Read bytes from fifo_fd_in: %d\n", nread);

      if ((rv = write(tun_fd, buff, nread)) <= 0) {
        writelog("WARN", "tun_fd=%d, nread=%zu\n", tun_fd, nread);
        perror("Write tun_fd error");
        // TODO: Isn't this a critical error?
        break;
      }
    }
  }

  free(buff);

  writelog("INFO", "Do widzenia\n");
  return 0;
}
