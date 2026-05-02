#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L

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

#define SERIAL_DEVICE "/dev/ttyUSB0"
#define READ_BUFFER_SIZE 256
#define STREAM_BUFFER_SIZE 1024
#define MAX_PENDING_REQUESTS 32

enum register_type {
    REGISTER_HOLDING = 0x03,
    REGISTER_INPUT = 0x04,
};

struct monitor_config {
    enum register_type register_type;
    uint16_t register_number;
};

struct pending_request {
    uint8_t device_id;
    uint8_t function;
    uint16_t start_register;
    uint16_t register_count;
};

struct pending_request_queue {
    struct pending_request entries[MAX_PENDING_REQUESTS];
    size_t count;
};

enum parse_status {
    PARSE_NEED_MORE = 0,
    PARSE_CONSUMED,
    PARSE_DROP_BYTE,
};

static volatile sig_atomic_t g_stop = 0;
static int g_verbose = 0;
static int g_csv = 0;

static void trace_hex(const char *label, const uint8_t *data, size_t len)
{
    size_t i;

    fprintf(stderr, "[TRACE] %s (%zu bytes):", label, len);
    for (i = 0; i < len; ++i) {
        fprintf(stderr, " %02X", data[i]);
    }
    fputc('\n', stderr);
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

static uint16_t modbus_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    size_t index;

    for (index = 0; index < len; ++index) {
        int bit;

        crc ^= data[index];
        for (bit = 0; bit < 8; ++bit) {
            if ((crc & 0x0001U) != 0U) {
                crc = (uint16_t)((crc >> 1) ^ 0xA001U);
            } else {
                crc >>= 1;
            }
        }
    }

    return crc;
}

static int frame_has_valid_crc(const uint8_t *frame, size_t frame_len)
{
    uint16_t expected_crc;
    uint16_t actual_crc;

    if (frame_len < 4) {
        return 0;
    }

    expected_crc = modbus_crc16(frame, frame_len - 2);
    actual_crc = (uint16_t)frame[frame_len - 2] | ((uint16_t)frame[frame_len - 1] << 8);
    return expected_crc == actual_crc;
}

static int is_valid_device_id(uint8_t device_id)
{
    return device_id >= 1 && device_id <= 247;
}

static const char *register_type_name(enum register_type register_type)
{
    return register_type == REGISTER_HOLDING ? "holding" : "input";
}

static int parse_register_type(const char *text, enum register_type *register_type)
{
    if (strcmp(text, "holding") == 0) {
        *register_type = REGISTER_HOLDING;
        return 0;
    }

    if (strcmp(text, "input") == 0) {
        *register_type = REGISTER_INPUT;
        return 0;
    }

    return -1;
}

static int parse_register_number(const char *text, uint16_t *register_number)
{
    char *end = NULL;
    unsigned long value;

    errno = 0;
    value = strtoul(text, &end, 0);
    if (errno != 0 || end == text || *end != '\0' || value > 0xFFFFUL) {
        return -1;
    }

    *register_number = (uint16_t)value;
    return 0;
}

static void pending_request_queue_push(struct pending_request_queue *queue, const struct pending_request *request)
{
    if (queue->count == MAX_PENDING_REQUESTS) {
        memmove(&queue->entries[0], &queue->entries[1], (MAX_PENDING_REQUESTS - 1) * sizeof(queue->entries[0]));
        queue->count = MAX_PENDING_REQUESTS - 1;
    }

    queue->entries[queue->count++] = *request;
}

static void pending_request_queue_remove(struct pending_request_queue *queue, size_t index)
{
    if (index >= queue->count) {
        return;
    }

    if (index + 1 < queue->count) {
        memmove(&queue->entries[index],
                &queue->entries[index + 1],
                (queue->count - index - 1) * sizeof(queue->entries[0]));
    }

    queue->count--;
}

static void log_register_value(FILE *log_file,
                               const struct monitor_config *config,
                               uint8_t device_id,
                               uint16_t value)
{
    char timestamp[48];

    make_timestamp(timestamp, sizeof(timestamp));

    if (g_csv) {
        fprintf(log_file,
                "%s,%u,%s:%u,0x%04X,%u,%d\n",
                timestamp,
                (unsigned int)device_id,
                register_type_name(config->register_type),
                (unsigned int)config->register_number,
                (unsigned int)value,
                (unsigned int)value,
                (int)(int16_t)value);
    } else {
        fprintf(log_file,
                "%s device=%u %s[%u]=0x%04X (%u)\n",
                timestamp,
                (unsigned int)device_id,
                register_type_name(config->register_type),
                (unsigned int)config->register_number,
                (unsigned int)value,
                (unsigned int)value);
    }
    fflush(log_file);
}

static int process_matching_response(FILE *log_file,
                                     const struct monitor_config *config,
                                     const struct pending_request *request,
                                     const uint8_t *frame,
                                     size_t frame_len)
{
    uint8_t byte_count = frame[2];
    uint16_t register_offset;
    size_t data_offset;
    uint16_t value;

    (void)frame_len;

    if (request->function != (uint8_t)config->register_type) {
        if (g_verbose) {
            fprintf(stderr, "[TRACE] response FC%02X from device %u: wrong function (want FC%02X)\n",
                    request->function, request->device_id, (uint8_t)config->register_type);
        }
        return 0;
    }

    if (config->register_number < request->start_register) {
        if (g_verbose) {
            fprintf(stderr, "[TRACE] response FC%02X device %u reg range %u..%u: target reg %u is below range\n",
                    request->function, request->device_id,
                    request->start_register,
                    (unsigned)(request->start_register + request->register_count - 1),
                    (unsigned)config->register_number);
        }
        return 0;
    }

    register_offset = (uint16_t)(config->register_number - request->start_register);
    if (register_offset >= request->register_count) {
        if (g_verbose) {
            fprintf(stderr, "[TRACE] response FC%02X device %u reg range %u..%u: target reg %u is above range\n",
                    request->function, request->device_id,
                    request->start_register,
                    (unsigned)(request->start_register + request->register_count - 1),
                    (unsigned)config->register_number);
        }
        return 0;
    }

    data_offset = 3U + ((size_t)register_offset * 2U);
    if (data_offset + 1 >= (size_t)byte_count + 3U) {
        if (g_verbose) {
            fprintf(stderr, "[TRACE] response FC%02X device %u: data_offset %zu out of byte_count %u\n",
                    request->function, request->device_id, data_offset, byte_count);
        }
        return 0;
    }

    value = (uint16_t)((uint16_t)frame[data_offset] << 8) | frame[data_offset + 1];
    log_register_value(log_file, config, request->device_id, value);
    return 1;
}

static enum parse_status try_consume_frame(const uint8_t *buffer,
                                           size_t buffered_len,
                                           struct pending_request_queue *pending_requests,
                                           FILE *log_file,
                                           const struct monitor_config *config,
                                           size_t *consumed_len)
{
    uint8_t device_id;
    uint8_t function;
    size_t index;

    *consumed_len = 0;

    if (buffered_len < 4) {
        return PARSE_NEED_MORE;
    }

    device_id = buffer[0];
    function = buffer[1];

    if (!is_valid_device_id(device_id)) {
        return PARSE_DROP_BYTE;
    }

    if (function == 0x03 || function == 0x04) {
        for (index = 0; index < pending_requests->count; ++index) {
            const struct pending_request *request = &pending_requests->entries[index];
            size_t response_len;

            if (request->device_id != device_id || request->function != function) {
                continue;
            }

            response_len = (size_t)request->register_count * 2U + 5U;
            if (buffered_len < response_len) {
                if ((size_t)buffer[2] + 5U == response_len) {
                    return PARSE_NEED_MORE;
                }
                continue;
            }

            if (buffer[2] != (uint8_t)(request->register_count * 2U)) {
                continue;
            }

            if (!frame_has_valid_crc(buffer, response_len)) {
                if (g_verbose) {
                    uint16_t got = (uint16_t)buffer[response_len - 2] | ((uint16_t)buffer[response_len - 1] << 8);
                    uint16_t want = modbus_crc16(buffer, response_len - 2);
                    fprintf(stderr, "[TRACE] response FC%02X device %u len=%zu: CRC mismatch got 0x%04X want 0x%04X\n",
                            function, device_id, response_len, got, want);
                }
                continue;
            }

            if (g_verbose) {
                fprintf(stderr, "[TRACE] matched response FC%02X device %u start=%u count=%u\n",
                        function, device_id,
                        request->start_register, request->register_count);
            }
            process_matching_response(log_file, config, request, buffer, response_len);
            pending_request_queue_remove(pending_requests, index);
            *consumed_len = response_len;
            return PARSE_CONSUMED;
        }

        if (buffered_len < 8) {
            return PARSE_NEED_MORE;
        }

        if (frame_has_valid_crc(buffer, 8)) {
            uint16_t register_count = (uint16_t)((uint16_t)buffer[4] << 8) | buffer[5];

            if (register_count >= 1 && register_count <= 125) {
                struct pending_request request;

                request.device_id = device_id;
                request.function = function;
                request.start_register = (uint16_t)((uint16_t)buffer[2] << 8) | buffer[3];
                request.register_count = register_count;
                if (g_verbose) {
                    fprintf(stderr, "[TRACE] queued request FC%02X device %u start=%u count=%u\n",
                            function, device_id, request.start_register, register_count);
                }
                pending_request_queue_push(pending_requests, &request);
                *consumed_len = 8;
                return PARSE_CONSUMED;
            }
        }

        if ((buffer[2] & 1U) == 0U && buffer[2] >= 2U) {
            size_t response_len = (size_t)buffer[2] + 5U;

            if (response_len > 255U) {
                return PARSE_DROP_BYTE;
            }

            if (buffered_len < response_len) {
                return PARSE_NEED_MORE;
            }

            if (frame_has_valid_crc(buffer, response_len)) {
                if (g_verbose) {
                    fprintf(stderr, "[TRACE] unmatched response FC%02X device %u len=%zu (no pending request)\n",
                            function, device_id, response_len);
                }
                *consumed_len = response_len;
                return PARSE_CONSUMED;
            }
        }

        if (g_verbose) {
            fprintf(stderr, "[TRACE] dropping byte 0x%02X (device=%u FC=%02X, no valid frame)\n",
                    buffer[0], device_id, function);
        }
        return PARSE_DROP_BYTE;
    }

    if (function == 0x06) {
        if (buffered_len < 8) {
            return PARSE_NEED_MORE;
        }

        if (frame_has_valid_crc(buffer, 8)) {
            *consumed_len = 8;
            return PARSE_CONSUMED;
        }

        return PARSE_DROP_BYTE;
    }

    if ((function & 0x80U) != 0U) {
        if (buffered_len < 5) {
            return PARSE_NEED_MORE;
        }

        if (frame_has_valid_crc(buffer, 5)) {
            *consumed_len = 5;
            return PARSE_CONSUMED;
        }

        return PARSE_DROP_BYTE;
    }

    return PARSE_DROP_BYTE;
}

static void process_stream_buffer(uint8_t *stream_buffer,
                                  size_t *buffered_len,
                                  struct pending_request_queue *pending_requests,
                                  FILE *log_file,
                                  const struct monitor_config *config)
{
    size_t offset = 0;

    while (offset < *buffered_len) {
        enum parse_status status;
        size_t consumed_len = 0;

        status = try_consume_frame(stream_buffer + offset,
                                   *buffered_len - offset,
                                   pending_requests,
                                   log_file,
                                   config,
                                   &consumed_len);

        if (status == PARSE_CONSUMED) {
            offset += consumed_len;
            continue;
        }

        if (status == PARSE_DROP_BYTE) {
            offset++;
            continue;
        }

        break;
    }

    if (offset > 0) {
        memmove(stream_buffer, stream_buffer + offset, *buffered_len - offset);
        *buffered_len -= offset;
    }
}

int main(int argc, char *argv[])
{
    const char *log_path = "modbus_register.log";
    const char *serial_device = SERIAL_DEVICE;
    struct monitor_config config;
    int serial_fd;
    FILE *log_file;
    struct sigaction sa;
    unsigned char buffer[READ_BUFFER_SIZE];
    uint8_t stream_buffer[STREAM_BUFFER_SIZE];
    size_t buffered_len = 0;
    struct pending_request_queue pending_requests;

    while (argc >= 2 && argv[argc - 1][0] == '-') {
        if (strcmp(argv[argc - 1], "-v") == 0) {
            g_verbose = 1;
        } else if (strcmp(argv[argc - 1], "-csv") == 0) {
            g_csv = 1;
        } else {
            break;
        }
        argc--;
    }

    if (argc < 3 || argc > 5) {
        fprintf(stderr, "Usage: %s [serial_device] <holding|input> <register> [log_file] [-v] [-csv]\n", argv[0]);
        return 1;
    }

    if (parse_register_type(argv[1], &config.register_type) != 0) {
        serial_device = argv[1];
        argc--;
        argv++;
    }

    if (argc < 3) {
        fprintf(stderr, "Usage: %s [serial_device] <holding|input> <register> [log_file] [-v] [-csv]\n", argv[0]);
        return 1;
    }

    if (parse_register_type(argv[1], &config.register_type) != 0) {
        fprintf(stderr, "Invalid register type '%s' (expected 'holding' or 'input')\n", argv[1]);
        return 1;
    }

    if (parse_register_number(argv[2], &config.register_number) != 0) {
        fprintf(stderr, "Invalid register number '%s'\n", argv[2]);
        return 1;
    }

    if (argc == 4) {
        log_path = argv[3];
    }

    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = handle_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

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

    log_file = fopen(log_path, "a");
    if (log_file == NULL) {
        perror("open log file");
        close(serial_fd);
        return 1;
    }

    if (g_csv) {
        long pos = ftell(log_file);
        if (pos == 0) {
            fprintf(log_file, "datetime,device_id,register,value_hex,value_unsigned,value_signed\n");
            fflush(log_file);
        }
    }

    memset(&pending_requests, 0, sizeof(pending_requests));

    fprintf(stderr,
            "Monitoring %s register %u on %s at 9600 baud -> %s\n",
            register_type_name(config.register_type),
            (unsigned int)config.register_number,
            serial_device,
            log_path);
    fprintf(stderr, "Press Ctrl+C to stop.\n");

    while (!g_stop) {
        ssize_t bytes_read = read(serial_fd, buffer, sizeof(buffer));

        if (bytes_read > 0) {
            size_t chunk_len = (size_t)bytes_read;

            if (g_verbose) {
                trace_hex("rx", (const uint8_t *)buffer, chunk_len);
            }

            if (buffered_len + chunk_len > sizeof(stream_buffer)) {
                size_t discard_len = buffered_len + chunk_len - sizeof(stream_buffer);

                memmove(stream_buffer, stream_buffer + discard_len, buffered_len - discard_len);
                buffered_len -= discard_len;
            }

            memcpy(stream_buffer + buffered_len, buffer, chunk_len);
            buffered_len += chunk_len;

            process_stream_buffer(stream_buffer, &buffered_len, &pending_requests, log_file, &config);
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
