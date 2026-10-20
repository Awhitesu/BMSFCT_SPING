#include "network_config.h"
#include <string.h>

NetworkConfig_t g_network_config;

static NetworkConfig_Status NetworkConfig_FlashRead(NetworkConfig_t *config);
static NetworkConfig_Status NetworkConfig_FlashWrite(const NetworkConfig_t *config);
static void NetworkConfig_SetDefaults(NetworkConfig_t *config);

NetworkConfig_Status NetworkConfig_Init(void)
{
    if (NetworkConfig_Load(&g_network_config) != NETWORK_CONFIG_OK) {
        NetworkConfig_SetDefaults(&g_network_config);
        (void)NetworkConfig_Save(&g_network_config);
    }

    return NETWORK_CONFIG_OK;
}

NetworkConfig_Status NetworkConfig_Load(NetworkConfig_t *config)
{
    if (config == NULL) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }

    if (NetworkConfig_FlashRead(config) != NETWORK_CONFIG_OK || !NetworkConfig_ValidateConfig(config)) {
        return NETWORK_CONFIG_ERROR;
    }

    return NETWORK_CONFIG_OK;
}

NetworkConfig_Status NetworkConfig_Save(const NetworkConfig_t *config)
{
    if (config == NULL || !NetworkConfig_ValidateConfig(config)) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }

    memcpy(&g_network_config, config, sizeof(g_network_config));
    return NetworkConfig_FlashWrite(config);
}

NetworkConfig_Status NetworkConfig_GetCurrentConfig(NetworkConfig_t *config)
{
    if (config == NULL) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }

    memcpy(config, &g_network_config, sizeof(*config));
    return NETWORK_CONFIG_OK;
}

NetworkConfig_Status NetworkConfig_SetIPAddress(uint8_t ip0, uint8_t ip1, uint8_t ip2, uint8_t ip3)
{
    g_network_config.ip_address[0] = ip0;
    g_network_config.ip_address[1] = ip1;
    g_network_config.ip_address[2] = ip2;
    g_network_config.ip_address[3] = ip3;
    g_network_config.checksum = NetworkConfig_CalculateChecksum(&g_network_config);
    return NetworkConfig_Save(&g_network_config);
}

NetworkConfig_Status NetworkConfig_SetNetmask(uint8_t nm0, uint8_t nm1, uint8_t nm2, uint8_t nm3)
{
    g_network_config.netmask[0] = nm0;
    g_network_config.netmask[1] = nm1;
    g_network_config.netmask[2] = nm2;
    g_network_config.netmask[3] = nm3;
    g_network_config.checksum = NetworkConfig_CalculateChecksum(&g_network_config);
    return NetworkConfig_Save(&g_network_config);
}

NetworkConfig_Status NetworkConfig_SetGateway(uint8_t gw0, uint8_t gw1, uint8_t gw2, uint8_t gw3)
{
    g_network_config.gateway[0] = gw0;
    g_network_config.gateway[1] = gw1;
    g_network_config.gateway[2] = gw2;
    g_network_config.gateway[3] = gw3;
    g_network_config.checksum = NetworkConfig_CalculateChecksum(&g_network_config);
    return NetworkConfig_Save(&g_network_config);
}

NetworkConfig_Status NetworkConfig_RestoreDefaults(void)
{
    NetworkConfig_SetDefaults(&g_network_config);
    return NetworkConfig_Save(&g_network_config);
}

uint32_t NetworkConfig_CalculateChecksum(const NetworkConfig_t *config)
{
    uint32_t checksum = 0;
    const uint8_t *data = (const uint8_t *)config;
    size_t len = sizeof(NetworkConfig_t) - sizeof(uint32_t);

    if (config == NULL) {
        return 0;
    }

    for (size_t i = 0; i < len; ++i) {
        checksum += data[i];
        checksum = (checksum << 1) | (checksum >> 31);
    }

    return checksum ^ 0x5A5A5A5AUL;
}

uint8_t NetworkConfig_ValidateConfig(const NetworkConfig_t *config)
{
    uint32_t ip_value;
    uint32_t netmask;
    uint32_t inverted;

    if (config == NULL || config->magic != NETWORK_CONFIG_MAGIC) {
        return 0U;
    }

    if (config->checksum != NetworkConfig_CalculateChecksum(config)) {
        return 0U;
    }

    ip_value = ((uint32_t)config->ip_address[0] << 24) |
               ((uint32_t)config->ip_address[1] << 16) |
               ((uint32_t)config->ip_address[2] << 8) |
               config->ip_address[3];
    if (ip_value == 0UL || ip_value == 0xFFFFFFFFUL) {
        return 0U;
    }

    netmask = ((uint32_t)config->netmask[0] << 24) |
              ((uint32_t)config->netmask[1] << 16) |
              ((uint32_t)config->netmask[2] << 8) |
              config->netmask[3];
    if (netmask == 0UL) {
        return 0U;
    }

    inverted = ~netmask;
    if ((inverted & (inverted + 1UL)) != 0UL) {
        return 0U;
    }

    return 1U;
}

void NetworkConfig_SoftwareReset(void)
{
    __disable_irq();
    NVIC_SystemReset();
}

static void NetworkConfig_SetDefaults(NetworkConfig_t *config)
{
    if (config == NULL) {
        return;
    }

    memset(config, 0, sizeof(*config));
    config->magic = NETWORK_CONFIG_MAGIC;
    config->ip_address[0] = DEFAULT_IP_ADDR_0;
    config->ip_address[1] = DEFAULT_IP_ADDR_1;
    config->ip_address[2] = DEFAULT_IP_ADDR_2;
    config->ip_address[3] = DEFAULT_IP_ADDR_3;
    config->netmask[0] = DEFAULT_NETMASK_0;
    config->netmask[1] = DEFAULT_NETMASK_1;
    config->netmask[2] = DEFAULT_NETMASK_2;
    config->netmask[3] = DEFAULT_NETMASK_3;
    config->gateway[0] = DEFAULT_GATEWAY_0;
    config->gateway[1] = DEFAULT_GATEWAY_1;
    config->gateway[2] = DEFAULT_GATEWAY_2;
    config->gateway[3] = DEFAULT_GATEWAY_3;
    config->checksum = NetworkConfig_CalculateChecksum(config);
}

static NetworkConfig_Status NetworkConfig_FlashRead(NetworkConfig_t *config)
{
    if (config == NULL) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }

    memcpy(config, (const void *)NETWORK_CONFIG_FLASH_ADDRESS, sizeof(*config));
    return NETWORK_CONFIG_OK;
}

static NetworkConfig_Status NetworkConfig_FlashWrite(const NetworkConfig_t *config)
{
    FLASH_EraseInitTypeDef erase_init;
    uint32_t sector_error = 0;
    uint32_t flash_addr = NETWORK_CONFIG_FLASH_ADDRESS;
    const uint32_t *data = (const uint32_t *)config;
    size_t words = sizeof(NetworkConfig_t) / sizeof(uint32_t);

    if (config == NULL || (sizeof(NetworkConfig_t) % sizeof(uint32_t)) != 0U) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }

    if (HAL_FLASH_Unlock() != HAL_OK) {
        return NETWORK_CONFIG_FLASH_ERROR;
    }

    erase_init.TypeErase = FLASH_TYPEERASE_SECTORS;
    erase_init.VoltageRange = FLASH_VOLTAGE_RANGE_3;
    erase_init.Sector = NETWORK_CONFIG_FLASH_SECTOR;
    erase_init.NbSectors = 1;

    if (HAL_FLASHEx_Erase(&erase_init, &sector_error) != HAL_OK) {
        HAL_FLASH_Lock();
        return NETWORK_CONFIG_FLASH_ERROR;
    }

    for (size_t i = 0; i < words; ++i) {
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, flash_addr, data[i]) != HAL_OK) {
            HAL_FLASH_Lock();
            return NETWORK_CONFIG_FLASH_ERROR;
        }
        flash_addr += sizeof(uint32_t);
    }

    HAL_FLASH_Lock();
    return NETWORK_CONFIG_OK;
}
