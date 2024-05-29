// To replace with ipowd2.c which uses sockets instead of fifos
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

#define BUFSIZE 4096

void writelog(const char *level, const char *format, ...) {
    char time_str[20]; // Buffer for time string
    strftime(time_str, 20, "%Y-%m-%d %H:%M:%S", localtime(&(time_t){time(NULL)}));
    printf("[%s] [%s] ", time_str, level);

    va_list args;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
}

int fifo_in_open(char *filepath) {
  int fifo_in_fd;

  if ((fifo_in_fd = open(filepath, O_RDONLY|O_NONBLOCK)) == -1) {
    fprintf(stderr, "Can't open fifo_in (%s): ", filepath);
    perror(NULL);
  }

  return fifo_in_fd;
}

int fifo_in_prepare(char *filepath) {
  unlink(filepath);

  if (mkfifo(filepath, 0666) == -1) {
    fprintf(stderr, "Can't prepare fifo_in (%s): ", filepath);
    perror(NULL);
    return -1;
  }

  return fifo_in_open(filepath);
}

int fifo_out_connect(char *filename) {
  int fifo_out_fd;

  // O_RDWR instead of O_WRONLY, because we want to use O_NONBLOCK
  if ((fifo_out_fd = open(filename, O_RDWR|O_NONBLOCK)) == -1) {
    fprintf(stderr, "Can't connect to fifo_out (%s): ", filename);
    perror(NULL);
    return -1;
  }

  return fifo_out_fd;
}

int fifo_out_prepare(char *filepath) {
  unlink(filepath);

  if (mkfifo(filepath, 0666) == -1) {
    fprintf(stderr, "Can't prepare fifo_out (%s): ", filepath);
    perror(NULL);
    return -1;
  }

  return fifo_out_connect(filepath);
}

int fifo_out_write(int *fifo_fd, char *buff, int len, char *filename) {
  int rv = -1;

  if (*fifo_fd <= 0) {
    *fifo_fd = fifo_out_connect(filename);
  }

  if (*fifo_fd >= 0) {
    rv = write(*fifo_fd, buff, len);
    if (rv == -1) {
      perror("Can't write to fifo_out: ");
      *fifo_fd = -1;
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
  char *fifo_in = "/var/run/tun_in.fifo";
  char *fifo_out = "/var/run/tun_out.fifo";
  int fifo_fd_in, fifo_fd_out, tun_fd;
  int maxfd;

  char buff[BUFSIZE];

  tun_fd = tun_alloc(ifname);
  writelog("INFO", "tun_alloc: tun_fd=%d, ifname=%s\n", tun_fd, ifname);

  fifo_fd_in = fifo_in_prepare(fifo_in);
  writelog("INFO", "fifo_in_prepare: fifo_in=%s, fifo_fd_in=%d\n", fifo_in, fifo_fd_in);

  fifo_fd_out = fifo_out_prepare(fifo_out);
  writelog("INFO", "fifo_out_prepare: fifo_out=%s, fifo_fd_out=%d\n", fifo_out, fifo_fd_out);

  writelog("INFO", "Setup done, perhaps you want to set up a tunnel, for example with something like:\n\tip addr add 10.0.0.1 peer 10.0.0.2 dev %s\n\tip link set %s up\nor with the old ifconfig:\n\tifconfig %s 10.0.0.1 pointopoint 10.0.0.2 netmask 255.255.255.255 up\nand something similar on the other end..\n", ifname, ifname, ifname);
 
  maxfd = (tun_fd > fifo_fd_in) ? tun_fd : fifo_fd_in;

  while (1) {
    int ret;
    int nread;
    fd_set rd_set;

    FD_ZERO(&rd_set);
    FD_SET(tun_fd, &rd_set);
    FD_SET(fifo_fd_in, &rd_set);

    ret = select(maxfd + 1, &rd_set, NULL, NULL, NULL);

    if (ret < 0 && errno == EINTR) {
      continue;
    }

    if (ret < 0) {
      perror("select()");
    }

    if (FD_ISSET(tun_fd, &rd_set)) {
      memset(buff, 0, sizeof(buff));
      if ((nread = read(tun_fd, buff, BUFSIZE - 1)) < 0) {
        perror("tun_fd read error");
        continue;
      }
      writelog("INFO", "Read bytes from tun_fd: %d\n", nread);

      rv = fifo_out_write(&fifo_fd_out, buff, nread, fifo_out);
      writelog("INFO", "fifo_out_write: %d, fifo_fd_out = %d, fifo_out = %s\n", rv, fifo_fd_out, fifo_out);
    }

    if (FD_ISSET(fifo_fd_in, &rd_set)) {
      memset(buff, 0, sizeof(buff));
      nread = read(fifo_fd_in, buff, BUFSIZE - 1);
      if (nread == 0) {
        // fifo closed on the other end
        writelog("WARNING", "fifo_fd_in, remote end closed, let's reopen it\n");
        FD_CLR(fifo_fd_in, &rd_set);
        close(fifo_fd_in);
        fifo_fd_in = fifo_in_open(fifo_in);
        FD_SET(fifo_fd_in, &rd_set);
        continue;
      }

      if (nread < 0) {
        perror("fifo_fd_in read error");
        continue;
      }

      writelog("INFO", "Read bytes from fifo_fd_in: %d\n", nread);
      if ((rv = write(tun_fd, buff, nread)) <= 0) {
        writelog("WARN", "tun_fd=%d, nread=%d, buff=%s\n", tun_fd, nread, buff);
        perror("Write tun_fd error");
        continue;
      }
    }
  }

  writelog("INFO", "Do widzenia\n");
  return 0;
}
