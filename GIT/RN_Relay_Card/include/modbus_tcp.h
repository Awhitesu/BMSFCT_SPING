#ifndef __MODBUS_TCP_H__
#define __MODBUS_TCP_H__

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"
#include "lwip/tcp.h"
#include "relay_control.h"
#include "network_config.h"

#define MODBUS_TCP_PORT                     502
#define MODBUS_MAX_CLIENTS                  1
#define MODBUS_MBAP_PREFIX_LENGTH           6
#define MODBUS_MBAP_HEADER_LENGTH           7
#define MODBUS_PDU_MAX_LENGTH               253
#define MODBUS_TCP_MAX_FRAME_LENGTH         (MODBUS_MBAP_PREFIX_LENGTH + 1 + MODBUS_PDU_MAX_LENGTH)

#define MODBUS_FUNCTION_READ_COILS          0x01
#define MODBUS_FUNCTION_READ_HOLDING_REGS   0x03
#define MODBUS_FUNCTION_READ_INPUT_REGS     0x04
#define MODBUS_FUNCTION_WRITE_SINGLE_COIL   0x05
#define MODBUS_FUNCTION_WRITE_SINGLE_REG    0x06
#define MODBUS_FUNCTION_WRITE_MULTIPLE_COILS 0x0F
#define MODBUS_FUNCTION_WRITE_MULTIPLE_REGS 0x10

#define MODBUS_EXCEPTION_ILLEGAL_FUNCTION   0x01
#define MODBUS_EXCEPTION_ILLEGAL_DATA_ADDR  0x02
#define MODBUS_EXCEPTION_ILLEGAL_DATA_VALUE 0x03
#define MODBUS_EXCEPTION_SLAVE_DEVICE_FAIL  0x04

#define MODBUS_COIL_RELAY_BASE              0x0000
#define MODBUS_COIL_COUNT                   RELAY_CHANNEL_COUNT

#define MODBUS_REG_SYSTEM_STATUS            0x0100
#define MODBUS_REG_DEVICE_ID                0x0101
#define MODBUS_REG_FIRMWARE_VERSION         0x0102
#define MODBUS_REG_CHANNEL_ENABLE           0x0103
#define MODBUS_REG_IO_STATUS                0x0104

#define MODBUS_REG_IP_ADDR_HIGH             0x0110
#define MODBUS_REG_IP_ADDR_LOW              0x0111
#define MODBUS_REG_NETMASK_HIGH             0x0112
#define MODBUS_REG_NETMASK_LOW              0x0113
#define MODBUS_REG_GATEWAY_HIGH             0x0114
#define MODBUS_REG_GATEWAY_LOW              0x0115
#define MODBUS_REG_NETWORK_CONTROL          0x0116
#define MODBUS_REG_SOFTWARE_RESET           0x011F

#define NETWORK_CTRL_APPLY_CONFIG           (1U << 0)
#define NETWORK_CTRL_RESTORE_DEFAULTS       (1U << 1)
#define NETWORK_CTRL_REBOOT_REQUIRED        (1U << 8)
#define NETWORK_CTRL_SOFTWARE_RESET         (1U << 12)

#define MODBUS_TOTAL_REGISTERS              288U

typedef enum {
    MODBUS_OK = 0,
    MODBUS_ERROR
} Modbus_Status;

typedef struct {
    uint16_t transaction_id;
    uint16_t protocol_id;
    uint16_t length;
    uint8_t unit_id;
} __attribute__((packed)) Modbus_TCP_Header;

typedef struct Modbus_TCP_Server_s Modbus_TCP_Server;

typedef struct {
    struct tcp_pcb *pcb;
    uint8_t rx_buffer[MODBUS_TCP_MAX_FRAME_LENGTH];
    uint16_t rx_index;
    uint8_t tx_buffer[MODBUS_TCP_MAX_FRAME_LENGTH];
    uint8_t active;
    Modbus_TCP_Server *server;
} Modbus_Client_Context;

struct Modbus_TCP_Server_s {
    struct tcp_pcb *listen_pcb;
    Modbus_Client_Context clients[MODBUS_MAX_CLIENTS];
    Relay_Handle *relay_handle;
    uint16_t registers[MODBUS_TOTAL_REGISTERS];
    uint32_t reset_request_tick;
    uint8_t reset_requested;
    uint8_t server_running;
};

Modbus_Status Modbus_TCP_Init(Modbus_TCP_Server *server, Relay_Handle *relay_handle);
Modbus_Status Modbus_TCP_Start(Modbus_TCP_Server *server);
Modbus_Status Modbus_TCP_Stop(Modbus_TCP_Server *server);
void Modbus_TCP_Process(Modbus_TCP_Server *server);
void Modbus_Update_Registers(Modbus_TCP_Server *server);
void Modbus_Update_Network_Registers(Modbus_TCP_Server *server, uint8_t force_from_config);

Modbus_Status Modbus_Process_Request(Modbus_TCP_Server *server,
                                     Modbus_Client_Context *client,
                                     uint8_t *request,
                                     uint16_t request_len,
                                     uint8_t *response,
                                     uint16_t *response_len);

#ifdef __cplusplus
}
#endif

#endif
