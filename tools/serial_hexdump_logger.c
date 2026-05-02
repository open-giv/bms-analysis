#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define SERIAL_DEVICE "/dev/ttyUSB1"
#define READ_BUFFER_SIZE 256
#define BYTES_PER_LINE 16

static volatile sig_atomic_t g_stop = 0;

static void handle_signal(int sig)
{
    (void)sig;
    g_stop = 1;
}

static int configure_serial_9600(int fd)
{
    struct termios tty;

    if (tcgetattr(fd, &tty) != 0) {
        return -1;
    }

    cfmakeraw(&tty);

    if (cfsetispeed(&tty, B9600) != 0 || cfsetospeed(&tty, B9600) != 0) {
        return -1;
    }

    tty.c_cflag |= (CLOCAL | CREAD);
    tty.c_cflag &= ~PARENB;
    tty.c_cflag &= ~CSTOPB;
    tty.c_cflag &= ~CSIZE;
    tty.c_cflag |= CS8;
#ifdef CRTSCTS
    tty.c_cflag &= ~CRTSCTS;
#endif

    tty.c_iflag &= ~(IXON | IXOFF | IXANY);
    tty.c_cc[VMIN] = 1;
    tty.c_cc[VTIME] = 0;

    if (tcsetattr(fd, TCSANOW, &tty) != 0) {
        return -1;
    }

    if (tcflush(fd, TCIFLUSH) != 0) {
        return -1;
    }

    return 0;
}

static void make_timestamp(char *out, size_t out_size)
{
    struct timespec ts;
    struct tm tm_local;
    char base[32];

    clock_gettime(CLOCK_REALTIME, &ts);
    localtime_r(&ts.tv_sec, &tm_local);
    strftime(base, sizeof(base), "%Y-%m-%d %H:%M:%S", &tm_local);

    snprintf(out, out_size, "%s.%03ld", base, ts.tv_nsec / 1000000L);
}

static void log_hexdump(FILE *log_file, const unsigned char *buf, ssize_t len, uint64_t *total_bytes)
{
    ssize_t i;

    for (i = 0; i < len; i += BYTES_PER_LINE) {
        ssize_t j;
        ssize_t line_len = len - i;
        char timestamp[48];

        if (line_len > BYTES_PER_LINE) {
            line_len = BYTES_PER_LINE;
        }

        make_timestamp(timestamp, sizeof(timestamp));
        fprintf(log_file, "%s  %08llx  ", timestamp, (unsigned long long)(*total_bytes + (uint64_t)i));

        for (j = 0; j < BYTES_PER_LINE; ++j) {
            if (j < line_len) {
                fprintf(log_file, "%02X ", buf[i + j]);
            } else {
                fputs("   ", log_file);
            }
        }

        fputs(" |", log_file);
        for (j = 0; j < line_len; ++j) {
            unsigned char c = buf[i + j];
            fputc(isprint(c) ? c : '.', log_file);
        }
        fputs("|\n", log_file);
    }

    *total_bytes += (uint64_t)len;
    fflush(log_file);
}

int main(int argc, char *argv[])
{
    const char *log_path = "serial_hexdump.log";
    int serial_fd;
    FILE *log_file;
    struct sigaction sa;
    unsigned char buffer[READ_BUFFER_SIZE];
    uint64_t total_bytes = 0;

    if (argc > 2) {
        fprintf(stderr, "Usage: %s [log_file]\n", argv[0]);
        return 1;
    }

    if (argc == 2) {
        log_path = argv[1];
    }

    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = handle_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    serial_fd = open(SERIAL_DEVICE, O_RDONLY | O_NOCTTY);
    if (serial_fd < 0) {
        perror("open serial device");
        return 1;
    }

    if (configure_serial_9600(serial_fd) != 0) {
        perror("configure serial");
        close(serial_fd);
        return 1;
    }

    log_file = fopen(log_path, "a");
    if (log_file == NULL) {
        perror("open log file");
        close(serial_fd);
        return 1;
    }

    fprintf(stderr, "Logging %s at 9600 baud to %s\n", SERIAL_DEVICE, log_path);
    fprintf(stderr, "Press Ctrl+C to stop.\n");

    while (!g_stop) {
        ssize_t bytes_read = read(serial_fd, buffer, sizeof(buffer));

        if (bytes_read > 0) {
            log_hexdump(log_file, buffer, bytes_read, &total_bytes);
            continue;
        }

        if (bytes_read == 0) {
            continue;
        }

        if (errno == EINTR) {
            continue;
        }

        perror("read serial");
        break;
    }

    fclose(log_file);
    close(serial_fd);
    return 0;
}
