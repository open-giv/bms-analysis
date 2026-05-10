#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>

#define READ_BUFFER_SIZE 256
#define STREAM_BUFFER_SIZE 2048
#define MAX_OVERRIDES 256

enum register_type {
    REGISTER_HOLDING = 0x03,
    REGISTER_INPUT = 0x04,
};

struct pending_request {
    uint8_t device_id;
    uint8_t function;
    uint16_t start_register;
    uint16_t register_count;
};

struct pending_request_slot {
    int occupied;
    struct pending_request request;
};

struct override_entry {
    int in_use;
    uint8_t device_id;
    enum register_type register_type;
    uint16_t register_number;
    uint16_t value;
};

static volatile sig_atomic_t g_stop = 0;
static int g_verbose = 0;

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
    tty.c_cc[VMIN] = 0;
    tty.c_cc[VTIME] = 1;

    if (tcsetattr(fd, TCSANOW, &tty) != 0) {
        return -1;
    }

    if (tcflush(fd, TCIFLUSH) != 0) {
        return -1;
    }

    return 0;
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

static int parse_u16(const char *text, uint16_t *value)
{
    char *end = NULL;
    unsigned long parsed;

    errno = 0;
    parsed = strtoul(text, &end, 0);
    if (errno != 0 || end == text || *end != '\0' || parsed > 0xFFFFUL) {
        return -1;
    }

    *value = (uint16_t)parsed;
    return 0;
}

static int parse_device_id(const char *text, uint8_t *device_id)
{
    uint16_t parsed;

    if (parse_u16(text, &parsed) != 0 || !is_valid_device_id((uint8_t)parsed)) {
        return -1;
    }

    *device_id = (uint8_t)parsed;
    return 0;
}

static int write_all(int fd, const uint8_t *data, size_t len)
{
    size_t total = 0;

    while (total < len) {
        ssize_t written = write(fd, data + total, len - total);

        if (written > 0) {
            total += (size_t)written;
            continue;
        }

        if (written < 0 && errno == EINTR) {
            continue;
        }

        return -1;
    }

    return 0;
}

static void pending_request_slot_set(struct pending_request_slot *slot, const struct pending_request *request)
{
    slot->request = *request;
    slot->occupied = 1;
}

static void pending_request_slot_clear(struct pending_request_slot *slot)
{
    slot->occupied = 0;
}

static void consume_upstream_requests(uint8_t *buffer,
                                      size_t *buffered_len,
                                      struct pending_request_slot *pending_request)
{
    size_t offset = 0;

    while (offset < *buffered_len) {
        uint8_t device_id;
        uint8_t function;

        if (*buffered_len - offset < 8) {
            break;
        }

        device_id = buffer[offset];
        function = buffer[offset + 1];

        if (!is_valid_device_id(device_id)) {
            offset++;
            continue;
        }

        if ((function == 0x03 || function == 0x04) && frame_has_valid_crc(buffer + offset, 8)) {
            uint16_t register_count = (uint16_t)((uint16_t)buffer[offset + 4] << 8) | buffer[offset + 5];

            if (register_count >= 1 && register_count <= 125) {
                struct pending_request request;

                request.device_id = device_id;
                request.function = function;
                request.start_register = (uint16_t)((uint16_t)buffer[offset + 2] << 8) | buffer[offset + 3];
                request.register_count = register_count;
                pending_request_slot_set(pending_request, &request);
                offset += 8;
                continue;
            }
        }

        offset++;
    }

    if (offset > 0) {
        memmove(buffer, buffer + offset, *buffered_len - offset);
        *buffered_len -= offset;
    }
}

static int find_override_index(const struct override_entry *overrides,
                               uint8_t device_id,
                               enum register_type register_type,
                               uint16_t register_number)
{
    size_t i;

    for (i = 0; i < MAX_OVERRIDES; ++i) {
        if (!overrides[i].in_use) {
            continue;
        }

        if (overrides[i].device_id == device_id &&
            overrides[i].register_type == register_type &&
            overrides[i].register_number == register_number) {
            return (int)i;
        }
    }

    return -1;
}

static int upsert_override(struct override_entry *overrides,
                           uint8_t device_id,
                           enum register_type register_type,
                           uint16_t register_number,
                           uint16_t value)
{
    int index = find_override_index(overrides, device_id, register_type, register_number);
    size_t i;

    if (index >= 0) {
        overrides[index].value = value;
        return 0;
    }

    for (i = 0; i < MAX_OVERRIDES; ++i) {
        if (!overrides[i].in_use) {
            overrides[i].in_use = 1;
            overrides[i].device_id = device_id;
            overrides[i].register_type = register_type;
            overrides[i].register_number = register_number;
            overrides[i].value = value;
            return 0;
        }
    }

    return -1;
}

static int clear_override(struct override_entry *overrides,
                          uint8_t device_id,
                          enum register_type register_type,
                          uint16_t register_number)
{
    int index = find_override_index(overrides, device_id, register_type, register_number);

    if (index < 0) {
        return -1;
    }

    memset(&overrides[index], 0, sizeof(overrides[index]));
    return 0;
}

static void list_overrides(const struct override_entry *overrides)
{
    size_t i;
    int any = 0;

    puts("Active overrides:");
    for (i = 0; i < MAX_OVERRIDES; ++i) {
        if (!overrides[i].in_use) {
            continue;
        }

        any = 1;
        printf("  device=%u %s register=%u value=0x%04X (%u)\n",
               (unsigned int)overrides[i].device_id,
               register_type_name(overrides[i].register_type),
               (unsigned int)overrides[i].register_number,
               (unsigned int)overrides[i].value,
               (unsigned int)overrides[i].value);
    }

    if (!any) {
        puts("  (none)");
    }
}

static int apply_overrides_to_response(uint8_t *frame,
                                       size_t frame_len,
                                       const struct pending_request *request,
                                       const struct override_entry *overrides)
{
    uint16_t register_index;
    int changed = 0;

    for (register_index = 0; register_index < request->register_count; ++register_index) {
        uint16_t register_number = (uint16_t)(request->start_register + register_index);
        int override_index = find_override_index(overrides,
                                                 request->device_id,
                                                 (enum register_type)request->function,
                                                 register_number);

        if (override_index >= 0) {
            uint16_t new_value = overrides[override_index].value;
            size_t data_offset = 3U + ((size_t)register_index * 2U);

            if (data_offset + 1 < frame_len - 2U) {
                uint8_t hi = (uint8_t)((new_value >> 8) & 0xFFU);
                uint8_t lo = (uint8_t)(new_value & 0xFFU);

                if (frame[data_offset] != hi || frame[data_offset + 1] != lo) {
                    frame[data_offset] = hi;
                    frame[data_offset + 1] = lo;
                    changed = 1;
                }
            }
        }
    }

    if (changed) {
        uint16_t crc = modbus_crc16(frame, frame_len - 2);

        frame[frame_len - 2] = (uint8_t)(crc & 0xFFU);
        frame[frame_len - 1] = (uint8_t)((crc >> 8) & 0xFFU);
    }

    return changed;
}

enum match_result {
    MATCH_FOUND = 0,
    MATCH_NEED_MORE = -2,
    MATCH_NONE = -1,
};

static enum match_result find_valid_matching_request(const struct pending_request_slot *pending_request,
                                                     const uint8_t *buffer,
                                                     size_t buffered_len,
                                                     uint8_t device_id,
                                                     uint8_t function,
                                                     size_t *expected_response_len)
{
    size_t response_len;

    if (!pending_request->occupied) {
        return MATCH_NONE;
    }

    if (pending_request->request.device_id != device_id ||
        pending_request->request.function != function) {
        return MATCH_NONE;
    }

    response_len = (size_t)pending_request->request.register_count * 2U + 5U;
    if (buffered_len < response_len) {
        *expected_response_len = 0;
        return MATCH_NEED_MORE;
    }

    if (!frame_has_valid_crc(buffer, response_len)) {
        *expected_response_len = 0;
        return MATCH_NEED_MORE;
    }

    *expected_response_len = response_len;
    return MATCH_FOUND;
}

enum consume_status {
    CONSUME_NEED_MORE = 0,
    CONSUME_CONSUMED,
    CONSUME_DROP_BYTE,
};

static enum consume_status try_consume_downstream_frame(uint8_t *buffer,
                                                         size_t buffered_len,
                                                         struct pending_request_slot *pending_request,
                                                         const struct override_entry *overrides,
                                                         size_t *frame_len)
{
    uint8_t device_id;
    uint8_t function;

    *frame_len = 0;

    if (buffered_len < 4) {
        return CONSUME_NEED_MORE;
    }

    device_id = buffer[0];
    function = buffer[1];

    if (!is_valid_device_id(device_id)) {
        return CONSUME_DROP_BYTE;
    }

    if (function == 0x03 || function == 0x04) {
        size_t response_len;

        if (buffered_len < 4) {
            return CONSUME_NEED_MORE;
        }

        {
            enum match_result match = find_valid_matching_request(pending_request,
                                                                  buffer,
                                                                  buffered_len,
                                                                  device_id,
                                                                  function,
                                                                  &response_len);

            if (match == MATCH_NEED_MORE) {
                return CONSUME_NEED_MORE;
            }

            if (match == MATCH_NONE) {
                return CONSUME_DROP_BYTE;
            }

            apply_overrides_to_response(buffer, response_len, &pending_request->request, overrides);
            pending_request_slot_clear(pending_request);
        }

        *frame_len = response_len;
        return CONSUME_CONSUMED;
    }

    if (function == 0x06) {
        if (buffered_len < 8) {
            return CONSUME_NEED_MORE;
        }

        if (!frame_has_valid_crc(buffer, 8)) {
            return CONSUME_DROP_BYTE;
        }

        *frame_len = 8;
        return CONSUME_CONSUMED;
    }

    if ((function & 0x80U) != 0U) {
        if (buffered_len < 5) {
            return CONSUME_NEED_MORE;
        }

        if (!frame_has_valid_crc(buffer, 5)) {
            return CONSUME_DROP_BYTE;
        }

        *frame_len = 5;
        return CONSUME_CONSUMED;
    }

    return CONSUME_DROP_BYTE;
}

static int process_downstream_buffer(uint8_t *buffer,
                                     size_t *buffered_len,
                                     int output_fd,
                                     struct pending_request_slot *pending_request,
                                     const struct override_entry *overrides)
{
    size_t offset = 0;

    while (offset < *buffered_len) {
        size_t frame_len = 0;
        enum consume_status status = try_consume_downstream_frame(buffer + offset,
                                                                  *buffered_len - offset,
                                                                  pending_request,
                                                                  overrides,
                                                                  &frame_len);

        if (status == CONSUME_CONSUMED) {
            if (write_all(output_fd, buffer + offset, frame_len) != 0) {
                return -1;
            }
            if (g_verbose) {
                trace_hex("proxy->controller tx", buffer + offset, frame_len);
            }
            offset += frame_len;
            continue;
        }

        if (status == CONSUME_DROP_BYTE) {
            if (write_all(output_fd, buffer + offset, 1) != 0) {
                return -1;
            }
            if (g_verbose) {
                trace_hex("proxy->controller tx (drop-byte passthrough)", buffer + offset, 1);
            }
            offset += 1;
            continue;
        }

        break;
    }

    if (offset > 0) {
        memmove(buffer, buffer + offset, *buffered_len - offset);
        *buffered_len -= offset;
    }

    return 0;
}

static void print_console_help(void)
{
    puts("Commands:");
    puts("  set <device_id> <holding|input> <register> <value>");
    puts("  clear <device_id> <holding|input> <register>");
    puts("  list");
    puts("  help");
    puts("  quit");
}

static void print_prompt(void)
{
    fputs("proxy> ", stdout);
    fflush(stdout);
}

static void handle_console_line(char *line, struct override_entry *overrides)
{
    char *saveptr = NULL;
    char *command = strtok_r(line, " \t\r\n", &saveptr);

    if (command == NULL) {
        return;
    }

    if (strcmp(command, "help") == 0) {
        print_console_help();
        return;
    }

    if (strcmp(command, "list") == 0) {
        list_overrides(overrides);
        return;
    }

    if (strcmp(command, "quit") == 0 || strcmp(command, "exit") == 0) {
        g_stop = 1;
        return;
    }

    if (strcmp(command, "set") == 0) {
        char *device_text = strtok_r(NULL, " \t\r\n", &saveptr);
        char *type_text = strtok_r(NULL, " \t\r\n", &saveptr);
        char *register_text = strtok_r(NULL, " \t\r\n", &saveptr);
        char *value_text = strtok_r(NULL, " \t\r\n", &saveptr);
        uint8_t device_id;
        enum register_type register_type;
        uint16_t register_number;
        uint16_t value;

        if (device_text == NULL || type_text == NULL || register_text == NULL || value_text == NULL) {
            puts("Usage: set <device_id> <holding|input> <register> <value>");
            return;
        }

        if (parse_device_id(device_text, &device_id) != 0) {
            puts("Invalid device id (expected 1..247)");
            return;
        }

        if (parse_register_type(type_text, &register_type) != 0) {
            puts("Invalid register type (expected 'holding' or 'input')");
            return;
        }

        if (parse_u16(register_text, &register_number) != 0) {
            puts("Invalid register number");
            return;
        }

        if (parse_u16(value_text, &value) != 0) {
            puts("Invalid value (expected 0..65535)");
            return;
        }

        if (upsert_override(overrides, device_id, register_type, register_number, value) != 0) {
            puts("Override table full");
            return;
        }

        printf("Override set: device=%u %s register=%u value=0x%04X (%u)\n",
               (unsigned int)device_id,
               register_type_name(register_type),
               (unsigned int)register_number,
               (unsigned int)value,
               (unsigned int)value);
        return;
    }

    if (strcmp(command, "clear") == 0) {
        char *device_text = strtok_r(NULL, " \t\r\n", &saveptr);
        char *type_text = strtok_r(NULL, " \t\r\n", &saveptr);
        char *register_text = strtok_r(NULL, " \t\r\n", &saveptr);
        uint8_t device_id;
        enum register_type register_type;
        uint16_t register_number;

        if (device_text == NULL || type_text == NULL || register_text == NULL) {
            puts("Usage: clear <device_id> <holding|input> <register>");
            return;
        }

        if (parse_device_id(device_text, &device_id) != 0) {
            puts("Invalid device id (expected 1..247)");
            return;
        }

        if (parse_register_type(type_text, &register_type) != 0) {
            puts("Invalid register type (expected 'holding' or 'input')");
            return;
        }

        if (parse_u16(register_text, &register_number) != 0) {
            puts("Invalid register number");
            return;
        }

        if (clear_override(overrides, device_id, register_type, register_number) != 0) {
            puts("No matching override found");
            return;
        }

        printf("Override cleared: device=%u %s register=%u\n",
               (unsigned int)device_id,
               register_type_name(register_type),
               (unsigned int)register_number);
        return;
    }

    puts("Unknown command. Type 'help' for commands.");
}

static int has_valid_request_frame(const uint8_t *buffer, size_t len)
{
    size_t offset = 0;

    while (offset + 8 <= len) {
        uint8_t device_id = buffer[offset];
        uint8_t function  = buffer[offset + 1];

        if (is_valid_device_id(device_id) &&
            (function == 0x03 || function == 0x04) &&
            frame_has_valid_crc(buffer + offset, 8)) {
            uint16_t register_count = (uint16_t)((uint16_t)buffer[offset + 4] << 8) | buffer[offset + 5];

            if (register_count >= 1 && register_count <= 125) {
                return 1;
            }
        }

        offset++;
    }

    return 0;
}

int main(int argc, char *argv[])
{
    int controller_fd;
    int bus_fd;
    const char *controller_path = NULL;
    const char *bus_path = NULL;
    struct sigaction sa;
    struct pollfd poll_fds[3];
    uint8_t read_buffer[READ_BUFFER_SIZE];
    uint8_t upstream_parse_buffer[STREAM_BUFFER_SIZE];
    uint8_t downstream_parse_buffer[STREAM_BUFFER_SIZE];
    size_t upstream_buffered_len = 0;
    size_t downstream_buffered_len = 0;
    struct pending_request_slot pending_request;
    struct override_entry overrides[MAX_OVERRIDES];

    if (argc == 4) {
        if (strcmp(argv[3], "-v") == 0) {
            g_verbose = 1;
        } else {
            fprintf(stderr, "Usage: %s <controller_serial_port> <bus_serial_port> [-v]\n", argv[0]);
            return 1;
        }
    } else if (argc != 3) {
        fprintf(stderr, "Usage: %s <controller_serial_port> <bus_serial_port> [-v]\n", argv[0]);
        return 1;
    }

    memset(&pending_request, 0, sizeof(pending_request));
    memset(overrides, 0, sizeof(overrides));

    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = handle_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    controller_fd = open(argv[1], O_RDWR | O_NOCTTY);
    if (controller_fd < 0) {
        perror("open controller serial port");
        return 1;
    }

    bus_fd = open(argv[2], O_RDWR | O_NOCTTY);
    if (bus_fd < 0) {
        perror("open bus serial port");
        close(controller_fd);
        return 1;
    }

    if (configure_serial_9600(controller_fd) != 0) {
        perror("configure controller serial port");
        close(bus_fd);
        close(controller_fd);
        return 1;
    }

    if (configure_serial_9600(bus_fd) != 0) {
        perror("configure bus serial port");
        close(bus_fd);
        close(controller_fd);
        return 1;
    }

    /* Detect which port is the controller by waiting for the first valid request frame */
    {
        uint8_t det_buf_a[STREAM_BUFFER_SIZE];
        uint8_t det_buf_b[STREAM_BUFFER_SIZE];
        size_t det_len_a = 0;
        size_t det_len_b = 0;
        int detected = 0;
        struct pollfd det_fds[2];

        det_fds[0].fd = controller_fd;
        det_fds[0].events = POLLIN;
        det_fds[1].fd = bus_fd;
        det_fds[1].events = POLLIN;

        fprintf(stderr, "Waiting to detect controller port (first to send a valid request)...\n");

        while (!g_stop && !detected) {
            uint8_t rbuf[READ_BUFFER_SIZE];
            int r;
            ssize_t n;

            r = poll(det_fds, 2, 200);
            if (r < 0) {
                if (errno == EINTR) { continue; }
                perror("poll");
                g_stop = 1;
                break;
            }

            if (det_fds[0].revents & POLLIN) {
                n = read(controller_fd, rbuf, sizeof(rbuf));
                if (n > 0 && det_len_a + (size_t)n <= sizeof(det_buf_a)) {
                    memcpy(det_buf_a + det_len_a, rbuf, (size_t)n);
                    det_len_a += (size_t)n;
                }
            }

            if (det_fds[1].revents & POLLIN) {
                n = read(bus_fd, rbuf, sizeof(rbuf));
                if (n > 0 && det_len_b + (size_t)n <= sizeof(det_buf_b)) {
                    memcpy(det_buf_b + det_len_b, rbuf, (size_t)n);
                    det_len_b += (size_t)n;
                }
            }

            if (has_valid_request_frame(det_buf_a, det_len_a)) {
                controller_path = argv[1];
                bus_path    = argv[2];
                memcpy(upstream_parse_buffer, det_buf_a, det_len_a);
                upstream_buffered_len = det_len_a;
                memcpy(downstream_parse_buffer, det_buf_b, det_len_b);
                downstream_buffered_len = det_len_b;
                detected = 1;
            } else if (has_valid_request_frame(det_buf_b, det_len_b)) {
                int tmp_fd = controller_fd;
                controller_fd = bus_fd;
                bus_fd    = tmp_fd;
                controller_path = argv[2];
                bus_path    = argv[1];
                memcpy(upstream_parse_buffer, det_buf_b, det_len_b);
                upstream_buffered_len = det_len_b;
                memcpy(downstream_parse_buffer, det_buf_a, det_len_a);
                downstream_buffered_len = det_len_a;
                detected = 1;
            }
        }

        if (!detected) {
            close(bus_fd);
            close(controller_fd);
            return 0;
        }
    }

    poll_fds[0].fd = controller_fd;
    poll_fds[0].events = POLLIN;
    poll_fds[1].fd = bus_fd;
    poll_fds[1].events = POLLIN;
    poll_fds[2].fd = STDIN_FILENO;
    poll_fds[2].events = POLLIN;

    fprintf(stderr, "Detected: controller=%s  bus=%s\n", controller_path, bus_path);
    fprintf(stderr, "Type 'help' for console commands.\n");
    print_prompt();

    /* Process any complete frames already buffered during detection */
    consume_upstream_requests(upstream_parse_buffer, &upstream_buffered_len, &pending_request);
    if (downstream_buffered_len > 0) {
        if (process_downstream_buffer(downstream_parse_buffer, &downstream_buffered_len,
                                      controller_fd, &pending_request, overrides) != 0) {
            close(bus_fd);
            close(controller_fd);
            return 1;
        }
    }

    while (!g_stop) {
        int poll_result = poll(poll_fds, 3, 200);

        if (poll_result < 0) {
            if (errno == EINTR) {
                continue;
            }

            perror("poll");
            break;
        }

        if (poll_fds[0].revents & POLLIN) {
            ssize_t bytes_read = read(controller_fd, read_buffer, sizeof(read_buffer));

            if (bytes_read > 0) {
                size_t chunk_len = (size_t)bytes_read;

                if (g_verbose) {
                    trace_hex("controller->proxy rx", read_buffer, chunk_len);
                }

                if (write_all(bus_fd, read_buffer, chunk_len) != 0) {
                    perror("write bus serial port");
                    break;
                }

                if (g_verbose) {
                    trace_hex("proxy->bus tx", read_buffer, chunk_len);
                }

                if (upstream_buffered_len + chunk_len > sizeof(upstream_parse_buffer)) {
                    size_t drop = upstream_buffered_len + chunk_len - sizeof(upstream_parse_buffer);

                    if (drop > upstream_buffered_len) {
                        drop = upstream_buffered_len;
                    }
                    memmove(upstream_parse_buffer,
                            upstream_parse_buffer + drop,
                            upstream_buffered_len - drop);
                    upstream_buffered_len -= drop;
                }

                memcpy(upstream_parse_buffer + upstream_buffered_len, read_buffer, chunk_len);
                upstream_buffered_len += chunk_len;
                consume_upstream_requests(upstream_parse_buffer,
                                          &upstream_buffered_len,
                                          &pending_request);
            } else if (bytes_read < 0 && errno != EINTR) {
                perror("read controller serial port");
                break;
            }
        }

        if (poll_fds[1].revents & POLLIN) {
            ssize_t bytes_read = read(bus_fd, read_buffer, sizeof(read_buffer));

            if (bytes_read > 0) {
                size_t chunk_len = (size_t)bytes_read;

                if (g_verbose) {
                    trace_hex("bus->proxy rx", read_buffer, chunk_len);
                }

                if (downstream_buffered_len + chunk_len > sizeof(downstream_parse_buffer) &&
                    downstream_buffered_len > 0) {
                    size_t overflow = downstream_buffered_len + chunk_len - sizeof(downstream_parse_buffer);

                    if (overflow > downstream_buffered_len) {
                        overflow = downstream_buffered_len;
                    }

                    if (write_all(controller_fd, downstream_parse_buffer, overflow) != 0) {
                        perror("write controller serial port");
                        g_stop = 1;
                    } else {
                        if (g_verbose) {
                            trace_hex("proxy->controller tx (overflow flush)", downstream_parse_buffer, overflow);
                        }

                        memmove(downstream_parse_buffer,
                                downstream_parse_buffer + overflow,
                                downstream_buffered_len - overflow);
                        downstream_buffered_len -= overflow;
                    }
                }

                if (g_stop) {
                    break;
                }

                memcpy(downstream_parse_buffer + downstream_buffered_len, read_buffer, chunk_len);
                downstream_buffered_len += chunk_len;

                if (process_downstream_buffer(downstream_parse_buffer,
                                              &downstream_buffered_len,
                                              controller_fd,
                                              &pending_request,
                                              overrides) != 0) {
                    perror("write controller serial port");
                    break;
                }
            } else if (bytes_read < 0 && errno != EINTR) {
                perror("read bus serial port");
                break;
            }
        }

        if (poll_fds[2].revents & POLLIN) {
            char line[256];

            if (fgets(line, sizeof(line), stdin) == NULL) {
                g_stop = 1;
            } else {
                handle_console_line(line, overrides);
                if (!g_stop) {
                    print_prompt();
                }
            }
        }
    }

    close(bus_fd);
    close(controller_fd);
    return 0;
}
