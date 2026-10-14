#include "network_config.h"
#include <string.h>

// 全局网络配置
NetworkConfig_t g_network_config;
static uint8_t g_network_config_initialized = 0;

// 私有函数声明
static NetworkConfig_Status NetworkConfig_FlashRead(NetworkConfig_t *config);
static NetworkConfig_Status NetworkConfig_FlashWrite(const NetworkConfig_t *config);
static void NetworkConfig_SetDefaults(NetworkConfig_t *config);

/**
 * @brief 初始化网络配置模�?
 * @return NetworkConfig_Status
 */
NetworkConfig_Status NetworkConfig_Init(void)
{
    if (g_network_config_initialized) {
        return NETWORK_CONFIG_OK;
    }

    NetworkConfig_Status status = NetworkConfig_Load(&g_network_config);
    if (status == NETWORK_CONFIG_OK) {
        g_network_config_initialized = 1;
        return NETWORK_CONFIG_OK;
    }

    // Load failed, fallback to defaults and try to persist them.
    NetworkConfig_SetDefaults(&g_network_config);
    status = NetworkConfig_Save(&g_network_config);
    if (status == NETWORK_CONFIG_OK) {
        g_network_config_initialized = 1;
        // Defaults are usable now, but indicate a fallback happened.
        return NETWORK_CONFIG_ERROR;
    }

    return status;
}

/**
 * @brief 从Flash加载网络配置
 * @param config 配置结构体指�?
 * @return NetworkConfig_Status
 */
NetworkConfig_Status NetworkConfig_Load(NetworkConfig_t *config)
{
    if (config == NULL) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }
    
    NetworkConfig_Status status = NetworkConfig_FlashRead(config);
    
    if (status != NETWORK_CONFIG_OK || !NetworkConfig_ValidateConfig(config)) {
        return NETWORK_CONFIG_ERROR;
    }
    
    return NETWORK_CONFIG_OK;
}

/**
 * @brief 保存网络配置到Flash
 * @param config 配置结构体指�?
 * @return NetworkConfig_Status
 */
NetworkConfig_Status NetworkConfig_Save(const NetworkConfig_t *config)
{
    if (config == NULL) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }

    if (!NetworkConfig_ValidateConfig(config)) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }

    // Persist first, then publish to RAM state to keep consistency.
    NetworkConfig_Status status = NetworkConfig_FlashWrite(config);
    if (status != NETWORK_CONFIG_OK) {
        return status;
    }

    memcpy(&g_network_config, config, sizeof(NetworkConfig_t));
    return NETWORK_CONFIG_OK;
}

/**
 * @brief 获取当前网络配置
 * @param config 配置结构体指�?
 * @return NetworkConfig_Status
 */
NetworkConfig_Status NetworkConfig_GetCurrentConfig(NetworkConfig_t *config)
{
    if (config == NULL) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }
    
    memcpy(config, &g_network_config, sizeof(NetworkConfig_t));
    return NETWORK_CONFIG_OK;
}

/**
 * @brief 设置IP地址
 * @param ip0-ip3 IP地址的四个字�?
 * @return NetworkConfig_Status
 */
NetworkConfig_Status NetworkConfig_SetIPAddress(uint8_t ip0, uint8_t ip1, uint8_t ip2, uint8_t ip3)
{
    g_network_config.ip_address[0] = ip0;
    g_network_config.ip_address[1] = ip1;
    g_network_config.ip_address[2] = ip2;
    g_network_config.ip_address[3] = ip3;
    
    // 重新计算校验�?
    g_network_config.checksum = NetworkConfig_CalculateChecksum(&g_network_config);
    
    return NetworkConfig_Save(&g_network_config);
}

/**
 * @brief 设置子网掩码
 * @param nm0-nm3 子网掩码的四个字�?
 * @return NetworkConfig_Status
 */
NetworkConfig_Status NetworkConfig_SetNetmask(uint8_t nm0, uint8_t nm1, uint8_t nm2, uint8_t nm3)
{
    g_network_config.netmask[0] = nm0;
    g_network_config.netmask[1] = nm1;
    g_network_config.netmask[2] = nm2;
    g_network_config.netmask[3] = nm3;
    
    // 重新计算校验�?
    g_network_config.checksum = NetworkConfig_CalculateChecksum(&g_network_config);
    
    return NetworkConfig_Save(&g_network_config);
}

/**
 * @brief 设置网关地址
 * @param gw0-gw3 网关地址的四个字�?
 * @return NetworkConfig_Status
 */
NetworkConfig_Status NetworkConfig_SetGateway(uint8_t gw0, uint8_t gw1, uint8_t gw2, uint8_t gw3)
{
    g_network_config.gateway[0] = gw0;
    g_network_config.gateway[1] = gw1;
    g_network_config.gateway[2] = gw2;
    g_network_config.gateway[3] = gw3;
    
    // 重新计算校验�?
    g_network_config.checksum = NetworkConfig_CalculateChecksum(&g_network_config);
    
    return NetworkConfig_Save(&g_network_config);
}

/**
 * @brief 恢复默认网络配置
 * @return NetworkConfig_Status
 */
NetworkConfig_Status NetworkConfig_RestoreDefaults(void)
{
    NetworkConfig_SetDefaults(&g_network_config);
    return NetworkConfig_Save(&g_network_config);
}

/**
 * @brief 计算配置的校验和
 * @param config 配置结构体指�?
 * @return 校验�?
 */
uint32_t NetworkConfig_CalculateChecksum(const NetworkConfig_t *config)
{
    if (config == NULL) {
        return 0;
    }
    
    uint32_t checksum = 0;
    const uint8_t *data = (const uint8_t *)config;
    size_t len = sizeof(NetworkConfig_t) - sizeof(uint32_t); // 不包括checksum字段本身
    
    for (size_t i = 0; i < len; i++) {
        checksum += data[i];
        checksum = (checksum << 1) | (checksum >> 31); // 循环左移
    }
    
    return checksum ^ 0x5A5A5A5A; // XOR with magic pattern
}

/**
 * @brief 验证配置的有效�?
 * @param config 配置结构体指�?
 * @return 1=有效, 0=无效
 */
uint8_t NetworkConfig_ValidateConfig(const NetworkConfig_t *config)
{
    if (config == NULL) {
        return 0;
    }
    
    // 检查魔术数�?
    if (config->magic != NETWORK_CONFIG_MAGIC) {
        return 0;
    }
    
    // 验证校验�?
    uint32_t calculated_checksum = NetworkConfig_CalculateChecksum(config);
    if (config->checksum != calculated_checksum) {
        return 0;
    }
    
    // 验证IP地址不是全零或全255 (广播地址)
    uint32_t ip_value = (config->ip_address[0] << 24) | (config->ip_address[1] << 16) |
                       (config->ip_address[2] << 8) | config->ip_address[3];
    if (ip_value == 0 || ip_value == 0xFFFFFFFF) {
        return 0;
    }
    
    // 验证子网掩码的合法�?(必须是连续的1)
    uint32_t netmask = (config->netmask[0] << 24) | (config->netmask[1] << 16) |
                      (config->netmask[2] << 8) | config->netmask[3];
    
    // 检查子网掩码是否为连续�? (简化检�?
    if (netmask != 0) {
        uint32_t inverted = ~netmask;
        if ((inverted & (inverted + 1)) != 0) {
            return 0; // 不是连续�?
        }
    }
    
    return 1;
}

/**
 * @brief 设置默认网络配置
 * @param config 配置结构体指�?
 */
static void NetworkConfig_SetDefaults(NetworkConfig_t *config)
{
    if (config == NULL) {
        return;
    }
    
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
    
    config->dhcp_enable = 0; // 固定IP模式
    memset(config->reserved, 0, sizeof(config->reserved));
    
    config->checksum = NetworkConfig_CalculateChecksum(config);
}

/**
 * @brief 从Flash读取网络配置
 * @param config 配置结构体指�?
 * @return NetworkConfig_Status
 */
static NetworkConfig_Status NetworkConfig_FlashRead(NetworkConfig_t *config)
{
    if (config == NULL) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }
    
    // 从Flash地址读取数据
    uint32_t *flash_addr = (uint32_t *)NETWORK_CONFIG_FLASH_ADDRESS;
    uint32_t *config_addr = (uint32_t *)config;
    
    size_t words = sizeof(NetworkConfig_t) / sizeof(uint32_t);
    
    for (size_t i = 0; i < words; i++) {
        config_addr[i] = flash_addr[i];
    }
    
    return NETWORK_CONFIG_OK;
}

/**
 * @brief 将网络配置写入Flash
 * @param config 配置结构体指�?
 * @return NetworkConfig_Status
 */
static NetworkConfig_Status NetworkConfig_FlashWrite(const NetworkConfig_t *config)
{
    if (config == NULL) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }
    
    HAL_StatusTypeDef hal_status;
    
    // 解锁Flash
    hal_status = HAL_FLASH_Unlock();
    if (hal_status != HAL_OK) {
        return NETWORK_CONFIG_FLASH_ERROR;
    }
    
    // 擦除扇区
    FLASH_EraseInitTypeDef erase_init;
    uint32_t sector_error;
    
    erase_init.TypeErase = FLASH_TYPEERASE_SECTORS;
    erase_init.VoltageRange = FLASH_VOLTAGE_RANGE_3;
    erase_init.Sector = NETWORK_CONFIG_FLASH_SECTOR;
    erase_init.NbSectors = 1;
    
    hal_status = HAL_FLASHEx_Erase(&erase_init, &sector_error);
    if (hal_status != HAL_OK) {
        HAL_FLASH_Lock();
        return NETWORK_CONFIG_FLASH_ERROR;
    }
    
    // 写入数据
    uint32_t flash_addr = NETWORK_CONFIG_FLASH_ADDRESS;
    const uint32_t *config_data = (const uint32_t *)config;
    size_t words = sizeof(NetworkConfig_t) / sizeof(uint32_t);
    
    for (size_t i = 0; i < words; i++) {
        hal_status = HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, flash_addr, config_data[i]);
        if (hal_status != HAL_OK) {
            HAL_FLASH_Lock();
            return NETWORK_CONFIG_FLASH_ERROR;
        }
        flash_addr += sizeof(uint32_t);
    }
    
    // 锁定Flash
    HAL_FLASH_Lock();
    
    return NETWORK_CONFIG_OK;
}

/**
 * @brief 执行软件重启
 */
void NetworkConfig_SoftwareReset(void)
{
    // 小延迟确保Modbus响应已发�?
    HAL_Delay(100);
    
    // 执行系统重启
    __disable_irq();  // 禁用中断
    NVIC_SystemReset();  // 执行系统重启
}

