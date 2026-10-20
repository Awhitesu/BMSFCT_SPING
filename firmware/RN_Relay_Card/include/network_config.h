#ifndef NETWORK_CONFIG_H
#define NETWORK_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"
#include "stm32f4xx_hal_flash.h"

#define NETWORK_CONFIG_FLASH_SECTOR     FLASH_SECTOR_7
#define NETWORK_CONFIG_FLASH_ADDRESS    0x08060000UL
#define NETWORK_CONFIG_MAGIC            0x4E455431UL

#define DEFAULT_IP_ADDR_0               192U
#define DEFAULT_IP_ADDR_1               168U
#define DEFAULT_IP_ADDR_2               1U
#define DEFAULT_IP_ADDR_3               10U

#define DEFAULT_NETMASK_0               255U
#define DEFAULT_NETMASK_1               255U
#define DEFAULT_NETMASK_2               255U
#define DEFAULT_NETMASK_3               0U

#define DEFAULT_GATEWAY_0               192U
#define DEFAULT_GATEWAY_1               168U
#define DEFAULT_GATEWAY_2               1U
#define DEFAULT_GATEWAY_3               1U

typedef enum {
    NETWORK_CONFIG_OK = 0,
    NETWORK_CONFIG_ERROR,
    NETWORK_CONFIG_FLASH_ERROR,
    NETWORK_CONFIG_INVALID_PARAM
} NetworkConfig_Status;

typedef struct {
    uint32_t magic;
    uint8_t ip_address[4];
    uint8_t netmask[4];
    uint8_t gateway[4];
    uint8_t dhcp_enable;
    uint8_t reserved[3];
    uint32_t checksum;
} __attribute__((packed)) NetworkConfig_t;

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

extern NetworkConfig_t g_network_config;

#ifdef __cplusplus
}
#endif

#endif
