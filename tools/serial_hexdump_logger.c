#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE

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
#include <limits.h>
#include <sys/stat.h>

#define READ_BUFFER_SIZE 256
#define BYTES_PER_LINE 16
#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

static volatile sig_atomic_t g_stop = 0;

/* Test-only clock shift in seconds, from LOGGER_CLOCK_OFFSET_S. */
static long g_clock_offset_s = 0;

static void now_utc(struct timespec *ts)
{
    clock_gettime(CLOCK_REALTIME, ts);
    ts->tv_sec += g_clock_offset_s;
}

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

static void make_timestamp(char *out, size_t out_size, const struct timespec *ts)
{
    struct tm tm_utc;
    char base[32];

    /* UTC with a Z suffix, so the log lines up with tcp_poller's UTC stamps. */
    gmtime_r(&ts->tv_sec, &tm_utc);
    strftime(base, sizeof(base), "%Y-%m-%d %H:%M:%S", &tm_utc);

    snprintf(out, out_size, "%s.%03ldZ", base, ts->tv_nsec / 1000000L);
}

static void log_hexdump(FILE *log_file, const unsigned char *buf, ssize_t len, uint64_t *total_bytes,
                        const struct timespec *ts)
{
    ssize_t i;

    for (i = 0; i < len; i += BYTES_PER_LINE) {
        ssize_t j;
        ssize_t line_len = len - i;
        char timestamp[48];

        if (line_len > BYTES_PER_LINE) {
            line_len = BYTES_PER_LINE;
        }

        make_timestamp(timestamp, sizeof(timestamp), ts);
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

/* Create every missing parent directory of path, like mkdir -p. */
static int make_parent_dirs(const char *path)
{
    char buf[PATH_MAX];
    char *p;

    if (snprintf(buf, sizeof(buf), "%s", path) >= (int)sizeof(buf)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    for (p = buf + 1; *p; ++p) {
        if (*p != '/') {
            continue;
        }
        *p = '\0';
        if (mkdir(buf, 0755) != 0 && errno != EEXIST) {
            return -1;
        }
        *p = '/';
    }
    return 0;
}

/* Expand the path template for the UTC date of t, and switch files if it changed. */
static int ensure_log_open(FILE **log_file, char *current_path, size_t path_size,
                           const char *template_path, time_t t)
{
    struct tm tm_utc;
    char path[PATH_MAX];

    gmtime_r(&t, &tm_utc);
    if (strftime(path, sizeof(path), template_path, &tm_utc) == 0) {
        errno = ENAMETOOLONG;
        return -1;
    }
    if (*log_file != NULL && strcmp(path, current_path) == 0) {
        return 0;
    }
    if (*log_file != NULL) {
        fclose(*log_file);
        *log_file = NULL;
    }
    if (make_parent_dirs(path) != 0) {
        return -1;
    }
    *log_file = fopen(path, "a");
    if (*log_file == NULL) {
        return -1;
    }
    snprintf(current_path, path_size, "%s", path);
    return 0;
}

int main(int argc, char *argv[])
{
    const char *serial_device = "/dev/ttyUSB0";
    const char *log_template = "serial_hexdump.log";
    int serial_fd;
    FILE *log_file = NULL;
    char current_path[PATH_MAX] = "";
    const char *offset_env = getenv("LOGGER_CLOCK_OFFSET_S");
    struct timespec now;
    struct sigaction sa;
    unsigned char buffer[READ_BUFFER_SIZE];
    uint64_t total_bytes = 0;

    if (argc > 3) {
        fprintf(stderr, "Usage: %s [serial_device] [log_file_or_strftime_template]\n", argv[0]);
        return 1;
    }

    if (argc >= 2) {
        serial_device = argv[1];
    }
    if (argc == 3) {
        log_template = argv[2];
    }

    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = handle_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    if (offset_env != NULL) {
        g_clock_offset_s = strtol(offset_env, NULL, 10);
    }

    serial_fd = open(serial_device, O_RDONLY | O_NOCTTY);
    if (serial_fd < 0) {
        perror("open serial device");
        return 1;
    }

    if (configure_serial_9600(serial_fd) != 0) {
        perror("configure serial");
        close(serial_fd);
        return 1;
    }

    now_utc(&now);
    if (ensure_log_open(&log_file, current_path, sizeof(current_path), log_template, now.tv_sec) != 0) {
        perror("open log file");
        close(serial_fd);
        return 1;
    }

    fprintf(stderr, "Logging %s at 9600 baud to %s\n", serial_device, log_template);
    fprintf(stderr, "Press Ctrl+C to stop.\n");

    while (!g_stop) {
        ssize_t bytes_read = read(serial_fd, buffer, sizeof(buffer));

        if (bytes_read > 0) {
            now_utc(&now);
            if (ensure_log_open(&log_file, current_path, sizeof(current_path), log_template, now.tv_sec) != 0) {
                perror("open log file");
                break;
            }
            log_hexdump(log_file, buffer, bytes_read, &total_bytes, &now);
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

    if (log_file != NULL) {
        fclose(log_file);
    }
    close(serial_fd);
    return 0;
}
