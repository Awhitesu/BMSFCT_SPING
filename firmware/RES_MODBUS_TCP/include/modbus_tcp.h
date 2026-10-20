#ifndef __MODBUS_TCP_H__
#define __MODBUS_TCP_H__

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"
#include "lwip/tcp.h"
#include "resistor_control.h"
#include "network_config.h"

#define MODBUS_TCP_PORT                     502
#define MODBUS_MAX_CLIENTS                  1
#define MODBUS_MBAP_PREFIX_LENGTH           6
#define MODBUS_MBAP_HEADER_LENGTH           7
#define MODBUS_PDU_MAX_LENGTH               253
#define MODBUS_TCP_MAX_FRAME_LENGTH         (MODBUS_MBAP_PREFIX_LENGTH + 1 + MODBUS_PDU_MAX_LENGTH)

#define MODBUS_FUNCTION_READ_HOLDING_REGS   0x03
#define MODBUS_FUNCTION_READ_INPUT_REGS     0x04
#define MODBUS_FUNCTION_WRITE_SINGLE_REG    0x06
#define MODBUS_FUNCTION_WRITE_MULTIPLE_REGS 0x10

#define MODBUS_EXCEPTION_ILLEGAL_FUNCTION   0x01
#define MODBUS_EXCEPTION_ILLEGAL_DATA_ADDR  0x02
#define MODBUS_EXCEPTION_ILLEGAL_DATA_VALUE 0x03
#define MODBUS_EXCEPTION_SLAVE_DEVICE_FAIL  0x04

#define MODBUS_REG_CH1_RESISTANCE_H         0x0000
#define MODBUS_REG_CH1_RESISTANCE_L         0x0001
#define MODBUS_REG_CH2_RESISTANCE_H         0x0002
#define MODBUS_REG_CH2_RESISTANCE_L         0x0003
#define MODBUS_REG_CH3_RESISTANCE_H         0x0004
#define MODBUS_REG_CH3_RESISTANCE_L         0x0005
#define MODBUS_REG_CH4_RESISTANCE_H         0x0006
#define MODBUS_REG_CH4_RESISTANCE_L         0x0007
#define MODBUS_REG_CH5_RESISTANCE_H         0x0008
#define MODBUS_REG_CH5_RESISTANCE_L         0x0009
#define MODBUS_REG_CH6_RESISTANCE_H         0x000A
#define MODBUS_REG_CH6_RESISTANCE_L         0x000B
#define MODBUS_REG_CH7_RESISTANCE_H         0x000C
#define MODBUS_REG_CH7_RESISTANCE_L         0x000D
#define MODBUS_REG_CH8_RESISTANCE_H         0x000E
#define MODBUS_REG_CH8_RESISTANCE_L         0x000F

#define MODBUS_REG_SYSTEM_STATUS            0x0100
#define MODBUS_REG_DEVICE_ID                0x0101
#define MODBUS_REG_FIRMWARE_VERSION         0x0102
#define MODBUS_REG_CHANNEL_ENABLE           0x0103
#define MODBUS_REG_RELAY_STATUS             0x0104

// 网络配置寄存器 (0x0110-0x0116, 0x011F)
#define MODBUS_REG_IP_ADDR_HIGH             0x0110  // IP地址高16位 (IP[0]<<8 | IP[1])
#define MODBUS_REG_IP_ADDR_LOW              0x0111  // IP地址低16位 (IP[2]<<8 | IP[3])
#define MODBUS_REG_NETMASK_HIGH             0x0112  // 子网掩码高16位
#define MODBUS_REG_NETMASK_LOW              0x0113  // 子网掩码低16位
#define MODBUS_REG_GATEWAY_HIGH             0x0114  // 网关地址高16位
#define MODBUS_REG_GATEWAY_LOW              0x0115  // 网关地址低16位
#define MODBUS_REG_NETWORK_CONTROL          0x0116  // 网络控制寄存器
#define MODBUS_REG_SOFTWARE_RESET           0x011F  // 软件重启控制寄存器

// 网络控制寄存器位定义
#define NETWORK_CTRL_APPLY_CONFIG           (1 << 0)    // 应用网络配置 (写1触发)
#define NETWORK_CTRL_RESTORE_DEFAULTS       (1 << 1)    // 恢复默认配置 (写1触发)
#define NETWORK_CTRL_REBOOT_REQUIRED        (1 << 8)    // 需要重启标志 (只读)
#define NETWORK_CTRL_SOFTWARE_RESET         (1 << 12)   // 软件重启 (写1触发)

#define MODBUS_TOTAL_REGISTERS              288

typedef enum {
    MODBUS_OK = 0,
    MODBUS_ERROR,
    MODBUS_TIMEOUT,
    MODBUS_INVALID_FUNCTION,
    MODBUS_INVALID_ADDRESS,
    MODBUS_INVALID_VALUE,
    MODBUS_CRC_ERROR
} Modbus_Status;

typedef struct {
    uint16_t transaction_id;
    uint16_t protocol_id;
    uint16_t length;
    uint8_t unit_id;
} __attribute__((packed)) Modbus_TCP_Header;

typedef struct {
    uint8_t function_code;
    uint16_t start_address;
    uint16_t quantity;
} __attribute__((packed)) Modbus_Read_Request;

typedef struct {
    uint8_t function_code;
    uint16_t start_address;
    uint16_t value;
} __attribute__((packed)) Modbus_Write_Single_Request;

typedef struct {
    uint8_t function_code;
    uint16_t start_address;
    uint16_t quantity;
    uint8_t byte_count;
    uint16_t values[];
} __attribute__((packed)) Modbus_Write_Multiple_Request;

typedef struct {
    uint8_t function_code;
    uint8_t byte_count;
    uint16_t values[];
} __attribute__((packed)) Modbus_Read_Response;

typedef struct {
    uint8_t function_code;
    uint16_t address;
    uint16_t value;
} __attribute__((packed)) Modbus_Write_Single_Response;

typedef struct {
    uint8_t function_code;
    uint16_t start_address;
    uint16_t quantity;
} __attribute__((packed)) Modbus_Write_Multiple_Response;

typedef struct {
    uint8_t function_code;
    uint8_t exception_code;
} __attribute__((packed)) Modbus_Exception_Response;

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
    Resistor_Handle *resistor_handle;
    uint16_t registers[MODBUS_TOTAL_REGISTERS];
    uint8_t server_running;
};

Modbus_Status Modbus_TCP_Init(Modbus_TCP_Server *server, Resistor_Handle *resistor_handle);
Modbus_Status Modbus_TCP_Start(Modbus_TCP_Server *server);
Modbus_Status Modbus_TCP_Stop(Modbus_TCP_Server *server);
void Modbus_TCP_Process(Modbus_TCP_Server *server);

Modbus_Status Modbus_Process_Request(Modbus_TCP_Server *server, Modbus_Client_Context *client, 
                                     uint8_t *request, uint16_t request_len, 
                                     uint8_t *response, uint16_t *response_len);

void Modbus_Update_Registers(Modbus_TCP_Server *server);
void Modbus_Update_Network_Registers(Modbus_TCP_Server *server, uint8_t force_from_flash);

#ifdef __cplusplus
}
#endif

#endif /* __MODBUS_TCP_H__ */
