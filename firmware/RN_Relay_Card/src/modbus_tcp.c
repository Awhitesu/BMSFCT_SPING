#include "modbus_tcp.h"
#include "app_config.h"
#include "lwip/def.h"
#include <string.h>

#define MODBUS_REG_REFRESH_INTERVAL_MS      100U
#define MODBUS_RESET_DELAY_MS               500U
#define MODBUS_DEVICE_ID_RC                 0x5243U
#define MODBUS_FIRMWARE_VERSION             0x0200U

static err_t modbus_tcp_accept(void *arg, struct tcp_pcb *newpcb, err_t err);
static err_t modbus_tcp_recv(void *arg, struct tcp_pcb *tpcb, struct pbuf *p, err_t err);
static void modbus_tcp_error(void *arg, err_t err);
static void modbus_tcp_close_client(Modbus_Client_Context *client);
static uint8_t coil_address_valid(uint16_t address, uint16_t quantity);
static uint8_t register_address_readable(uint16_t address);
static uint8_t register_address_writeable(uint16_t address);
static uint8_t register_range_readable(uint16_t start_addr, uint16_t quantity);
static uint8_t register_range_writeable(uint16_t start_addr, uint16_t quantity);
static Modbus_Status save_network_registers(Modbus_TCP_Server *server);
static Modbus_Status apply_network_control(Modbus_TCP_Server *server, uint16_t value);
static void request_software_reset(Modbus_TCP_Server *server);
static uint16_t read_u16_be(const uint8_t *data);
static void write_u16_be(uint8_t *data, uint16_t value);

static uint16_t read_u16_be(const uint8_t *data)
{
    return (uint16_t)(((uint16_t)data[0] << 8) | data[1]);
}

static void write_u16_be(uint8_t *data, uint16_t value)
{
    data[0] = (uint8_t)(value >> 8);
    data[1] = (uint8_t)(value & 0xFFU);
}

static uint8_t coil_address_valid(uint16_t address, uint16_t quantity)
{
    return quantity > 0U &&
           address < MODBUS_COIL_COUNT &&
           quantity <= MODBUS_COIL_COUNT &&
           (uint32_t)address + quantity <= MODBUS_COIL_COUNT;
}

static uint8_t register_address_readable(uint16_t address)
{
    return ((address >= MODBUS_REG_SYSTEM_STATUS && address <= MODBUS_REG_IO_STATUS) ||
            (address >= MODBUS_REG_IP_ADDR_HIGH && address <= MODBUS_REG_NETWORK_CONTROL) ||
            address == MODBUS_REG_SOFTWARE_RESET) ? 1U : 0U;
}

static uint8_t register_address_writeable(uint16_t address)
{
    return ((address >= MODBUS_REG_IP_ADDR_HIGH && address <= MODBUS_REG_NETWORK_CONTROL) ||
            address == MODBUS_REG_SOFTWARE_RESET) ? 1U : 0U;
}

static uint8_t register_range_readable(uint16_t start_addr, uint16_t quantity)
{
    if (quantity == 0U || quantity > 125U || (uint32_t)start_addr + quantity > MODBUS_TOTAL_REGISTERS) {
        return 0U;
    }

    for (uint16_t i = 0; i < quantity; ++i) {
        if (!register_address_readable(start_addr + i)) {
            return 0U;
        }
    }

    return 1U;
}

static uint8_t register_range_writeable(uint16_t start_addr, uint16_t quantity)
{
    if (quantity == 0U || quantity > 123U || (uint32_t)start_addr + quantity > MODBUS_TOTAL_REGISTERS) {
        return 0U;
    }

    for (uint16_t i = 0; i < quantity; ++i) {
        if (!register_address_writeable(start_addr + i)) {
            return 0U;
        }
    }

    return 1U;
}

Modbus_Status Modbus_TCP_Init(Modbus_TCP_Server *server, Relay_Handle *relay_handle)
{
    if (server == NULL || relay_handle == NULL) {
        return MODBUS_ERROR;
    }

    memset(server, 0, sizeof(*server));
    server->relay_handle = relay_handle;

    for (uint8_t i = 0; i < MODBUS_MAX_CLIENTS; ++i) {
        server->clients[i].server = server;
    }

    Modbus_Update_Registers(server);
    Modbus_Update_Network_Registers(server, 1U);

    return MODBUS_OK;
}

Modbus_Status Modbus_TCP_Start(Modbus_TCP_Server *server)
{
    if (server == NULL || server->server_running) {
        return MODBUS_ERROR;
    }

    server->listen_pcb = tcp_new();
    if (server->listen_pcb == NULL) {
        return MODBUS_ERROR;
    }

    err_t err = tcp_bind(server->listen_pcb, IP_ADDR_ANY, MODBUS_TCP_PORT);
    if (err != ERR_OK) {
        tcp_close(server->listen_pcb);
        server->listen_pcb = NULL;
        return MODBUS_ERROR;
    }

    server->listen_pcb = tcp_listen(server->listen_pcb);
    if (server->listen_pcb == NULL) {
        return MODBUS_ERROR;
    }

    tcp_arg(server->listen_pcb, server);
    tcp_accept(server->listen_pcb, modbus_tcp_accept);
    server->server_running = 1U;
    return MODBUS_OK;
}

Modbus_Status Modbus_TCP_Stop(Modbus_TCP_Server *server)
{
    if (server == NULL || !server->server_running) {
        return MODBUS_ERROR;
    }

    for (uint8_t i = 0; i < MODBUS_MAX_CLIENTS; ++i) {
        modbus_tcp_close_client(&server->clients[i]);
    }

    if (server->listen_pcb != NULL) {
        tcp_close(server->listen_pcb);
        server->listen_pcb = NULL;
    }

    server->server_running = 0U;
    return MODBUS_OK;
}

void Modbus_TCP_Process(Modbus_TCP_Server *server)
{
    static uint32_t last_update_tick = 0;
    uint32_t now;

    if (server == NULL || !server->server_running) {
        return;
    }

    now = HAL_GetTick();

    if (server->reset_requested && (now - server->reset_request_tick) >= MODBUS_RESET_DELAY_MS) {
        NetworkConfig_SoftwareReset();
    }

    if ((now - last_update_tick) >= MODBUS_REG_REFRESH_INTERVAL_MS) {
        last_update_tick = now;
        Modbus_Update_Registers(server);
    }
}

static err_t modbus_tcp_accept(void *arg, struct tcp_pcb *newpcb, err_t err)
{
    Modbus_TCP_Server *server = (Modbus_TCP_Server *)arg;
    if (server == NULL || err != ERR_OK || newpcb == NULL) {
        return ERR_VAL;
    }

    Modbus_Client_Context *client = &server->clients[0];
    if (client->active) {
        modbus_tcp_close_client(client);
    }

    client->pcb = newpcb;
    client->active = 1U;
    client->rx_index = 0U;
    client->server = server;

    tcp_nagle_disable(newpcb);
    tcp_arg(newpcb, client);
    tcp_recv(newpcb, modbus_tcp_recv);
    tcp_err(newpcb, modbus_tcp_error);
    tcp_accepted(server->listen_pcb);

    return ERR_OK;
}

static err_t modbus_tcp_recv(void *arg, struct tcp_pcb *tpcb, struct pbuf *p, err_t err)
{
    Modbus_Client_Context *client = (Modbus_Client_Context *)arg;

    if (p == NULL) {
        if (client != NULL) {
            modbus_tcp_close_client(client);
        }
        return ERR_OK;
    }

    if (err != ERR_OK) {
        pbuf_free(p);
        return err;
    }

    if (client == NULL || !client->active || client->server == NULL) {
        pbuf_free(p);
        return ERR_OK;
    }

    for (struct pbuf *q = p; q != NULL; q = q->next) {
        if (client->rx_index + q->len > MODBUS_TCP_MAX_FRAME_LENGTH) {
            pbuf_free(p);
            modbus_tcp_close_client(client);
            return ERR_OK;
        }
        memcpy(&client->rx_buffer[client->rx_index], q->payload, q->len);
        client->rx_index += q->len;
    }

    tcp_recved(tpcb, p->tot_len);
    pbuf_free(p);

    while (client->rx_index >= MODBUS_MBAP_PREFIX_LENGTH) {
        Modbus_TCP_Header *header = (Modbus_TCP_Header *)client->rx_buffer;
        uint16_t expected_length = lwip_ntohs(header->length) + MODBUS_MBAP_PREFIX_LENGTH;

        if (expected_length < (MODBUS_MBAP_HEADER_LENGTH + 1U) ||
            expected_length > MODBUS_TCP_MAX_FRAME_LENGTH) {
            modbus_tcp_close_client(client);
            return ERR_OK;
        }

        if (client->rx_index < expected_length) {
            break;
        }

        uint16_t response_len = 0;
        if (Modbus_Process_Request(client->server, client,
                                   client->rx_buffer, expected_length,
                                   client->tx_buffer, &response_len) == MODBUS_OK &&
            response_len > 0U) {
            if (tcp_write(tpcb, client->tx_buffer, response_len, TCP_WRITE_FLAG_COPY) != ERR_OK ||
                tcp_output(tpcb) != ERR_OK) {
                modbus_tcp_close_client(client);
                return ERR_OK;
            }
        }

        uint16_t remaining = client->rx_index - expected_length;
        if (remaining > 0U) {
            memmove(client->rx_buffer, &client->rx_buffer[expected_length], remaining);
        }
        client->rx_index = remaining;
    }

    return ERR_OK;
}

static void modbus_tcp_error(void *arg, err_t err)
{
    (void)err;
    Modbus_Client_Context *client = (Modbus_Client_Context *)arg;
    if (client != NULL) {
        client->active = 0U;
        client->pcb = NULL;
        client->rx_index = 0U;
    }
}

static void modbus_tcp_close_client(Modbus_Client_Context *client)
{
    if (client == NULL || !client->active) {
        return;
    }

    if (client->pcb != NULL) {
        tcp_arg(client->pcb, NULL);
        tcp_recv(client->pcb, NULL);
        tcp_err(client->pcb, NULL);
        tcp_close(client->pcb);
        client->pcb = NULL;
    }

    client->active = 0U;
    client->rx_index = 0U;
}

Modbus_Status Modbus_Process_Request(Modbus_TCP_Server *server,
                                     Modbus_Client_Context *client,
                                     uint8_t *request,
                                     uint16_t request_len,
                                     uint8_t *response,
                                     uint16_t *response_len)
{
    (void)client;

    if (server == NULL || server->relay_handle == NULL ||
        request == NULL || response == NULL || response_len == NULL ||
        request_len < MODBUS_MBAP_HEADER_LENGTH + 1U) {
        return MODBUS_ERROR;
    }

    Modbus_TCP_Header *req_header = (Modbus_TCP_Header *)request;
    Modbus_TCP_Header *resp_header = (Modbus_TCP_Header *)response;
    uint8_t *pdu = request + MODBUS_MBAP_HEADER_LENGTH;
    uint16_t pdu_len = request_len - MODBUS_MBAP_HEADER_LENGTH;
    uint8_t *resp_pdu = response + MODBUS_MBAP_HEADER_LENGTH;
    uint8_t function_code = pdu[0];

    resp_header->transaction_id = req_header->transaction_id;
    resp_header->protocol_id = req_header->protocol_id;
    resp_header->unit_id = req_header->unit_id;
    *response_len = MODBUS_MBAP_HEADER_LENGTH;

    switch (function_code) {
    case MODBUS_FUNCTION_READ_COILS: {
        if (pdu_len != 5U) {
            goto exception_illegal_data_value;
        }

        uint16_t start_addr = read_u16_be(&pdu[1]);
        uint16_t quantity = read_u16_be(&pdu[3]);
        if (!coil_address_valid(start_addr, quantity)) {
            goto exception_illegal_data_addr;
        }

        uint8_t byte_count = (uint8_t)((quantity + 7U) / 8U);
        resp_pdu[0] = function_code;
        resp_pdu[1] = byte_count;
        memset(&resp_pdu[2], 0, byte_count);

        for (uint16_t i = 0; i < quantity; ++i) {
            uint8_t state = 0U;
            if (Relay_GetChannel(server->relay_handle, start_addr + i, &state) != RELAY_OK) {
                goto exception_slave_device_fail;
            }
            if (state) {
                resp_pdu[2U + (i / 8U)] |= (uint8_t)(1U << (i % 8U));
            }
        }

        uint16_t resp_pdu_len = (uint16_t)(2U + byte_count);
        resp_header->length = lwip_htons((uint16_t)(1U + resp_pdu_len));
        *response_len += resp_pdu_len;
        break;
    }

    case MODBUS_FUNCTION_READ_HOLDING_REGS:
    case MODBUS_FUNCTION_READ_INPUT_REGS: {
        if (pdu_len != 5U) {
            goto exception_illegal_data_value;
        }

        uint16_t start_addr = read_u16_be(&pdu[1]);
        uint16_t quantity = read_u16_be(&pdu[3]);
        if (!register_range_readable(start_addr, quantity)) {
            goto exception_illegal_data_addr;
        }

        resp_pdu[0] = function_code;
        resp_pdu[1] = (uint8_t)(quantity * 2U);
        for (uint16_t i = 0; i < quantity; ++i) {
            write_u16_be(&resp_pdu[2U + i * 2U], server->registers[start_addr + i]);
        }

        uint16_t resp_pdu_len = (uint16_t)(2U + quantity * 2U);
        resp_header->length = lwip_htons((uint16_t)(1U + resp_pdu_len));
        *response_len += resp_pdu_len;
        break;
    }

    case MODBUS_FUNCTION_WRITE_SINGLE_COIL: {
        if (pdu_len != 5U) {
            goto exception_illegal_data_value;
        }

        uint16_t address = read_u16_be(&pdu[1]);
        uint16_t value = read_u16_be(&pdu[3]);
        if (!coil_address_valid(address, 1U)) {
            goto exception_illegal_data_addr;
        }
        if (value != 0xFF00U && value != 0x0000U) {
            goto exception_illegal_data_value;
        }
        if (Relay_SetChannel(server->relay_handle, address, value == 0xFF00U) != RELAY_OK) {
            goto exception_slave_device_fail;
        }

        resp_pdu[0] = function_code;
        write_u16_be(&resp_pdu[1], address);
        write_u16_be(&resp_pdu[3], value);
        resp_header->length = lwip_htons(6U);
        *response_len += 5U;
        break;
    }

    case MODBUS_FUNCTION_WRITE_SINGLE_REG: {
        if (pdu_len != 5U) {
            goto exception_illegal_data_value;
        }

        uint16_t address = read_u16_be(&pdu[1]);
        uint16_t value = read_u16_be(&pdu[3]);
        if (!register_address_writeable(address)) {
            goto exception_illegal_data_addr;
        }

        if (address == MODBUS_REG_NETWORK_CONTROL) {
            if (apply_network_control(server, value) != MODBUS_OK) {
                goto exception_illegal_data_value;
            }
        } else if (address == MODBUS_REG_SOFTWARE_RESET) {
            if (value == 1U) {
                request_software_reset(server);
            } else if (value != 0U) {
                goto exception_illegal_data_value;
            }
        } else {
            server->registers[address] = value;
        }

        resp_pdu[0] = function_code;
        write_u16_be(&resp_pdu[1], address);
        write_u16_be(&resp_pdu[3], value);
        resp_header->length = lwip_htons(6U);
        *response_len += 5U;
        break;
    }

    case MODBUS_FUNCTION_WRITE_MULTIPLE_COILS: {
        if (pdu_len < 6U) {
            goto exception_illegal_data_value;
        }

        uint16_t start_addr = read_u16_be(&pdu[1]);
        uint16_t quantity = read_u16_be(&pdu[3]);
        uint8_t byte_count = pdu[5];
        if (!coil_address_valid(start_addr, quantity) ||
            byte_count != (uint8_t)((quantity + 7U) / 8U) ||
            pdu_len != (uint16_t)(6U + byte_count)) {
            goto exception_illegal_data_addr;
        }

        for (uint16_t i = 0; i < quantity; ++i) {
            uint8_t state = (pdu[6U + (i / 8U)] & (uint8_t)(1U << (i % 8U))) ? 1U : 0U;
            if (Relay_SetChannel(server->relay_handle, start_addr + i, state) != RELAY_OK) {
                goto exception_slave_device_fail;
            }
        }

        resp_pdu[0] = function_code;
        write_u16_be(&resp_pdu[1], start_addr);
        write_u16_be(&resp_pdu[3], quantity);
        resp_header->length = lwip_htons(6U);
        *response_len += 5U;
        break;
    }

    case MODBUS_FUNCTION_WRITE_MULTIPLE_REGS: {
        if (pdu_len < 6U) {
            goto exception_illegal_data_value;
        }

        uint16_t start_addr = read_u16_be(&pdu[1]);
        uint16_t quantity = read_u16_be(&pdu[3]);
        uint8_t byte_count = pdu[5];
        if (!register_range_writeable(start_addr, quantity) ||
            byte_count != (uint8_t)(quantity * 2U) ||
            pdu_len != (uint16_t)(6U + byte_count)) {
            goto exception_illegal_data_addr;
        }

        uint16_t control_value = server->registers[MODBUS_REG_NETWORK_CONTROL];
        uint16_t reset_value = server->registers[MODBUS_REG_SOFTWARE_RESET];

        for (uint16_t i = 0; i < quantity; ++i) {
            uint16_t address = start_addr + i;
            uint16_t value = read_u16_be(&pdu[6U + i * 2U]);

            if (address == MODBUS_REG_NETWORK_CONTROL) {
                control_value = value;
            } else if (address == MODBUS_REG_SOFTWARE_RESET) {
                reset_value = value;
            } else {
                server->registers[address] = value;
            }
        }

        if (start_addr <= MODBUS_REG_NETWORK_CONTROL &&
            (uint32_t)start_addr + quantity > MODBUS_REG_NETWORK_CONTROL) {
            if (apply_network_control(server, control_value) != MODBUS_OK) {
                goto exception_illegal_data_value;
            }
        }

        if (start_addr <= MODBUS_REG_SOFTWARE_RESET &&
            (uint32_t)start_addr + quantity > MODBUS_REG_SOFTWARE_RESET) {
            if (reset_value == 1U) {
                request_software_reset(server);
            } else if (reset_value != 0U) {
                goto exception_illegal_data_value;
            }
        }

        resp_pdu[0] = function_code;
        write_u16_be(&resp_pdu[1], start_addr);
        write_u16_be(&resp_pdu[3], quantity);
        resp_header->length = lwip_htons(6U);
        *response_len += 5U;
        break;
    }

    default:
        goto exception_illegal_function;
    }

    return MODBUS_OK;

exception_illegal_function:
    resp_pdu[0] = function_code | 0x80U;
    resp_pdu[1] = MODBUS_EXCEPTION_ILLEGAL_FUNCTION;
    resp_header->length = lwip_htons(3U);
    *response_len += 2U;
    return MODBUS_OK;

exception_illegal_data_addr:
    resp_pdu[0] = function_code | 0x80U;
    resp_pdu[1] = MODBUS_EXCEPTION_ILLEGAL_DATA_ADDR;
    resp_header->length = lwip_htons(3U);
    *response_len += 2U;
    return MODBUS_OK;

exception_illegal_data_value:
    resp_pdu[0] = function_code | 0x80U;
    resp_pdu[1] = MODBUS_EXCEPTION_ILLEGAL_DATA_VALUE;
    resp_header->length = lwip_htons(3U);
    *response_len += 2U;
    return MODBUS_OK;

exception_slave_device_fail:
    resp_pdu[0] = function_code | 0x80U;
    resp_pdu[1] = MODBUS_EXCEPTION_SLAVE_DEVICE_FAIL;
    resp_header->length = lwip_htons(3U);
    *response_len += 2U;
    return MODBUS_OK;
}

void Modbus_Update_Registers(Modbus_TCP_Server *server)
{
    if (server == NULL) {
        return;
    }

    server->registers[MODBUS_REG_SYSTEM_STATUS] = 0x0001U;
    server->registers[MODBUS_REG_DEVICE_ID] = MODBUS_DEVICE_ID_RC;
    server->registers[MODBUS_REG_FIRMWARE_VERSION] = MODBUS_FIRMWARE_VERSION;
    server->registers[MODBUS_REG_CHANNEL_ENABLE] = Relay_GetChannelCount();
    server->registers[MODBUS_REG_IO_STATUS] = Relay_IsInitialized(server->relay_handle) ? 0x0001U : 0x0000U;
}

void Modbus_Update_Network_Registers(Modbus_TCP_Server *server, uint8_t force_from_config)
{
    NetworkConfig_t config;

    if (server == NULL || !force_from_config) {
        return;
    }

    if (NetworkConfig_GetCurrentConfig(&config) != NETWORK_CONFIG_OK) {
        return;
    }

    server->registers[MODBUS_REG_IP_ADDR_HIGH] = (uint16_t)(((uint16_t)config.ip_address[0] << 8) | config.ip_address[1]);
    server->registers[MODBUS_REG_IP_ADDR_LOW] = (uint16_t)(((uint16_t)config.ip_address[2] << 8) | config.ip_address[3]);
    server->registers[MODBUS_REG_NETMASK_HIGH] = (uint16_t)(((uint16_t)config.netmask[0] << 8) | config.netmask[1]);
    server->registers[MODBUS_REG_NETMASK_LOW] = (uint16_t)(((uint16_t)config.netmask[2] << 8) | config.netmask[3]);
    server->registers[MODBUS_REG_GATEWAY_HIGH] = (uint16_t)(((uint16_t)config.gateway[0] << 8) | config.gateway[1]);
    server->registers[MODBUS_REG_GATEWAY_LOW] = (uint16_t)(((uint16_t)config.gateway[2] << 8) | config.gateway[3]);
}

static Modbus_Status save_network_registers(Modbus_TCP_Server *server)
{
    NetworkConfig_t config;
    uint16_t value;

    if (server == NULL) {
        return MODBUS_ERROR;
    }

    memset(&config, 0, sizeof(config));
    config.magic = NETWORK_CONFIG_MAGIC;

    value = server->registers[MODBUS_REG_IP_ADDR_HIGH];
    config.ip_address[0] = (uint8_t)(value >> 8);
    config.ip_address[1] = (uint8_t)(value & 0xFFU);
    value = server->registers[MODBUS_REG_IP_ADDR_LOW];
    config.ip_address[2] = (uint8_t)(value >> 8);
    config.ip_address[3] = (uint8_t)(value & 0xFFU);

    value = server->registers[MODBUS_REG_NETMASK_HIGH];
    config.netmask[0] = (uint8_t)(value >> 8);
    config.netmask[1] = (uint8_t)(value & 0xFFU);
    value = server->registers[MODBUS_REG_NETMASK_LOW];
    config.netmask[2] = (uint8_t)(value >> 8);
    config.netmask[3] = (uint8_t)(value & 0xFFU);

    value = server->registers[MODBUS_REG_GATEWAY_HIGH];
    config.gateway[0] = (uint8_t)(value >> 8);
    config.gateway[1] = (uint8_t)(value & 0xFFU);
    value = server->registers[MODBUS_REG_GATEWAY_LOW];
    config.gateway[2] = (uint8_t)(value >> 8);
    config.gateway[3] = (uint8_t)(value & 0xFFU);

    config.checksum = NetworkConfig_CalculateChecksum(&config);
    if (NetworkConfig_Save(&config) != NETWORK_CONFIG_OK) {
        return MODBUS_ERROR;
    }

    return MODBUS_OK;
}

static Modbus_Status apply_network_control(Modbus_TCP_Server *server, uint16_t value)
{
    uint16_t status_bits = server->registers[MODBUS_REG_NETWORK_CONTROL] & NETWORK_CTRL_REBOOT_REQUIRED;

    if ((value & NETWORK_CTRL_APPLY_CONFIG) != 0U) {
        if (save_network_registers(server) != MODBUS_OK) {
            return MODBUS_ERROR;
        }
        status_bits |= NETWORK_CTRL_REBOOT_REQUIRED;
        Modbus_Update_Network_Registers(server, 1U);
    }

    if ((value & NETWORK_CTRL_RESTORE_DEFAULTS) != 0U) {
        if (NetworkConfig_RestoreDefaults() != NETWORK_CONFIG_OK) {
            return MODBUS_ERROR;
        }
        status_bits |= NETWORK_CTRL_REBOOT_REQUIRED;
        Modbus_Update_Network_Registers(server, 1U);
    }

    if ((value & NETWORK_CTRL_SOFTWARE_RESET) != 0U) {
        request_software_reset(server);
    }

    server->registers[MODBUS_REG_NETWORK_CONTROL] = status_bits;
    return MODBUS_OK;
}

static void request_software_reset(Modbus_TCP_Server *server)
{
    if (server == NULL) {
        return;
    }

    server->reset_requested = 1U;
    server->reset_request_tick = HAL_GetTick();
}
