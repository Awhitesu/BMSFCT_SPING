#ifndef __NETWORK_CONFIG_H__
#define __NETWORK_CONFIG_H__

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"
#include "stm32f4xx_hal_flash.h"

// Flash存储地址定义 (使用最后一个可用扇区)
// STM32F407ZE (512KB): 扇区7 (0x08060000-0x0807FFFF, 128KB)
#define NETWORK_CONFIG_FLASH_SECTOR     FLASH_SECTOR_7
#define NETWORK_CONFIG_FLASH_ADDRESS    0x08060000UL
#define NETWORK_CONFIG_MAGIC            0x4E455431UL  // "NET1" 

// 默认网络配置
#define DEFAULT_IP_ADDR_0       192
#define DEFAULT_IP_ADDR_1       168
#define DEFAULT_IP_ADDR_2       1
#define DEFAULT_IP_ADDR_3       10

#define DEFAULT_NETMASK_0       255
#define DEFAULT_NETMASK_1       255
#define DEFAULT_NETMASK_2       255
#define DEFAULT_NETMASK_3       0

#define DEFAULT_GATEWAY_0       192
#define DEFAULT_GATEWAY_1       168
#define DEFAULT_GATEWAY_2       1
#define DEFAULT_GATEWAY_3       1

typedef enum {
    NETWORK_CONFIG_OK = 0,
    NETWORK_CONFIG_ERROR,
    NETWORK_CONFIG_FLASH_ERROR,
    NETWORK_CONFIG_INVALID_PARAM
} NetworkConfig_Status;

typedef struct {
    uint32_t magic;         // 魔术数字，用于验证数据有效性
    uint8_t ip_address[4];  // IP地址
    uint8_t netmask[4];     // 子网掩码
    uint8_t gateway[4];     // 网关地址
    uint8_t dhcp_enable;    // DHCP启用标志 (预留，当前固定IP)
    uint8_t reserved[3];    // 保留字节，用于对齐
    uint32_t checksum;      // 校验和
} __attribute__((packed)) NetworkConfig_t;

// 函数声明
NetworkConfig_Status NetworkConfig_Init(void);
NetworkConfig_Status NetworkConfig_Load(NetworkConfig_t *config);
NetworkConfig_Status NetworkConfig_Save(const NetworkConfig_t *config);
NetworkConfig_Status NetworkConfig_GetCurrentConfig(NetworkConfig_t *config);
NetworkConfig_Status NetworkConfig_SetIPAddress(uint8_t ip0, uint8_t ip1, uint8_t ip2, uint8_t ip3);
NetworkConfig_Status NetworkConfig_SetNetmask(uint8_t nm0, uint8_t nm1, uint8_t nm2, uint8_t nm3);
NetworkConfig_Status NetworkConfig_SetGateway(uint8_t gw0, uint8_t gw1, uint8_t gw2, uint8_t gw3);
NetworkConfig_Status NetworkConfig_RestoreDefaults(void);
uint32_t NetworkConfig_CalculateChecksum(const NetworkConfig_t *config);
uint8_t NetworkConfig_ValidateConfig(const NetworkConfig_t *config);
void NetworkConfig_SoftwareReset(void);

// 全局变量声明
extern NetworkConfig_t g_network_config;

#ifdef __cplusplus
}
#endif

#endif /* __NETWORK_CONFIG_H__ */
