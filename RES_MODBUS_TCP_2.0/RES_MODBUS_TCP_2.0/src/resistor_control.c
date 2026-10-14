#include "resistor_control.h"
#include <stdio.h>

/**
 * @brief 初始化新的电阻控制系统
 */
Resistor_Status Resistor_Init(Resistor_Handle *handle)
{
    if (handle == NULL) {
        return RESISTOR_ERROR_NOT_INITIALIZED;
    }

    printf("初始化RN-EOL可编程电阻板卡控制系统...\r\n");

    // 初始化继电器控制系统
    Relay_Status status = Relay_Init(&handle->relay_handle);
    if (status != RELAY_OK) {
        printf("错误: 继电器控制系统初始化失败\r\n");
        return RESISTOR_ERROR_NOT_INITIALIZED;
    }

    // 初始化通道参数
    for (int i = 0; i < RESISTOR_CHANNELS_TOTAL; i++) {
        handle->channel_resistances[i] = 0;
        handle->channel_enabled[i] = 0; // 默认禁用
    }

    handle->initialized = 1;
    printf("RN-EOL电阻控制系统初始化完成\r\n");
    printf("  阻值范围: %luΩ - %luΩ\r\n", Resistor_GetMinResistance(), Resistor_GetMaxResistance());
    printf("  分辨率: %luΩ\r\n", Resistor_GetResolution());
    printf("  通道数: %d\r\n", RESISTOR_CHANNELS_TOTAL);

    return RESISTOR_OK;
}

/**
 * @brief 设置通道阻值
 */
Resistor_Status Resistor_SetChannelValue(Resistor_Handle *handle, uint8_t channel, uint32_t resistance_ohms)
{
    if (handle == NULL || !handle->initialized) {
        return RESISTOR_ERROR_NOT_INITIALIZED;
    }

    if (channel >= RESISTOR_CHANNELS_TOTAL) {
        return RESISTOR_ERROR_INVALID_CHANNEL;
    }

    uint32_t min_res = Resistor_GetMinResistance();
    uint32_t max_res = Resistor_GetMaxResistance();

    if (resistance_ohms < min_res || resistance_ohms > max_res) {
        printf("错误: 通道%d阻值%luΩ超出有效范围 (%luΩ-%luΩ)\r\n",
               channel + 1, resistance_ohms, min_res, max_res);
        return RESISTOR_ERROR_INVALID_RESISTANCE;
    }

    // 通过继电器控制系统设置阻值
    Relay_Status relay_status = Relay_SetResistance(&handle->relay_handle, resistance_ohms);
    if (relay_status != RELAY_OK) {
        printf("错误: 继电器设置失败，状态码=%d\r\n", relay_status);
        return RESISTOR_ERROR_INVALID_RESISTANCE;
    }

    // 0Ω不对外输出：K19保持吸合隔离输出，因此通道视为禁用。
    handle->channel_resistances[channel] = resistance_ohms;
    handle->channel_enabled[channel] = (resistance_ohms != 0U) ? 1U : 0U;

    return RESISTOR_OK;
}

/**
 * @brief 获取通道阻值
 */
Resistor_Status Resistor_GetChannelValue(Resistor_Handle *handle, uint8_t channel, uint32_t *resistance_ohms)
{
    if (handle == NULL || !handle->initialized || resistance_ohms == NULL) {
        return RESISTOR_ERROR_NOT_INITIALIZED;
    }

    if (channel >= RESISTOR_CHANNELS_TOTAL) {
        return RESISTOR_ERROR_INVALID_CHANNEL;
    }

    // 从继电器控制系统读取实际阻值
    Relay_Status status = Relay_GetResistance(&handle->relay_handle, resistance_ohms);
    if (status != RELAY_OK) {
        return RESISTOR_ERROR_INVALID_RESISTANCE;
    }

    handle->channel_resistances[channel] = *resistance_ohms;
    return RESISTOR_OK;
}

/**
 * @brief 禁用通道 (设置为最小阻值)
 */
Resistor_Status Resistor_DisableChannel(Resistor_Handle *handle, uint8_t channel)
{
    if (handle == NULL || !handle->initialized) {
        return RESISTOR_ERROR_NOT_INITIALIZED;
    }

    if (channel >= RESISTOR_CHANNELS_TOTAL) {
        return RESISTOR_ERROR_INVALID_CHANNEL;
    }

    // 断开输出并关闭所有阻值继电器
    Relay_SetAllRelaysOff(&handle->relay_handle);
    handle->channel_resistances[channel] = 0;
    handle->channel_enabled[channel] = 0;

    return RESISTOR_OK;
}

/**
 * @brief 设置所有通道阻值 (兼容性函数，单通道系统)
 */
Resistor_Status Resistor_SetAllChannels(Resistor_Handle *handle, uint32_t resistance_ohms)
{
    return Resistor_SetChannelValue(handle, 0, resistance_ohms);
}

/**
 * @brief 禁用所有通道 (兼容性函数)
 */
Resistor_Status Resistor_DisableAllChannels(Resistor_Handle *handle)
{
    if (handle == NULL || !handle->initialized) {
        return RESISTOR_ERROR_NOT_INITIALIZED;
    }

    Relay_SetAllRelaysOff(&handle->relay_handle);
    for (int i = 0; i < RESISTOR_CHANNELS_TOTAL; i++) {
        handle->channel_resistances[i] = 0;
        handle->channel_enabled[i] = 0;
    }

    return RESISTOR_OK;
}

/**
 * @brief 打印系统信息
 */
Resistor_Status Resistor_PrintSystemInfo(Resistor_Handle *handle)
{
    if (handle == NULL || !handle->initialized) {
        printf("错误: 电阻控制系统未初始化\r\n");
        return RESISTOR_ERROR_NOT_INITIALIZED;
    }

    extern uint8_t IP_ADDRESS[4];
    extern uint8_t NETMASK_ADDRESS[4];
    extern uint8_t GATEWAY_ADDRESS[4];

    printf("\r\n===============================================\r\n");
    printf("          RN-EOL可编程电阻板卡系统信息          \r\n");
    printf("===============================================\r\n");

    printf("硬件版本: V1.1\r\n");
    printf("固件版本: %s\r\n", RESISTOR_VERSION);
    printf("MCU型号: STM32F407VET6\r\n");
    printf("控制方式: 继电器阵列控制\r\n");
    printf("系统时钟: %ld MHz\r\n", HAL_RCC_GetHCLKFreq() / 1000000);

    // 网络配置信息
    printf("\r网络配置:\r\n");
    printf("  IP 地址    : %d.%d.%d.%d\r\n",
           IP_ADDRESS[0], IP_ADDRESS[1], IP_ADDRESS[2], IP_ADDRESS[3]);
    printf("  子网掩码   : %d.%d.%d.%d\r\n",
           NETMASK_ADDRESS[0], NETMASK_ADDRESS[1], NETMASK_ADDRESS[2], NETMASK_ADDRESS[3]);
    printf("  网关地址   : %d.%d.%d.%d\r\n",
           GATEWAY_ADDRESS[0], GATEWAY_ADDRESS[1], GATEWAY_ADDRESS[2], GATEWAY_ADDRESS[3]);

    // Modbus TCP配置
    printf("\rModbus TCP配置:\r\n");
    printf("  服务端口   : 502\r\n");
    printf("  最大客户端 : 1\r\n");
    printf("  服务状态   : 请检查主程序状态\r\n");

    // LED状态显示
    printf("\rLED状态指示 (PC11):\r\n");
    printf("  正常状态   : 500ms闪烁 (系统正常+网络连接)\r\n");
    printf("  异常状态   : 2s闪烁 (初始化中或网络断开)\r\n");

    printf("\r电阻参数:\r\n");
    printf("  阻值范围: %luΩ - %luΩ\r\n", Resistor_GetMinResistance(), Resistor_GetMaxResolution());
    printf("  分辨率: %luΩ\r\n", Resistor_GetResolution());
    printf("  继电器数量: %d (K1-K18阻值继电器 + K19输出隔离)\r\n", TOTAL_RELAYS);

    printf("\r通道状态:\r\n");
    for (int i = 0; i < RESISTOR_CHANNELS_TOTAL; i++) {
        uint32_t resistance;
        Resistor_GetChannelValue(handle, i, &resistance);
        printf("  通道%d: %luΩ (%s)\r\n",
               i + 1, resistance,
               handle->channel_enabled[i] ? "启用" : "禁用");
    }

    // 打印继电器详细状态
    Relay_PrintStatus(&handle->relay_handle);

    printf("===============================================\r\n");
    printf("                 系统初始化完成                 \r\n");
    printf("===============================================\r\n\r\n");

    return RESISTOR_OK;
}

/**
 * @brief 检查是否已初始化
 */
uint8_t Resistor_IsInitialized(Resistor_Handle *handle)
{
    return (handle != NULL && handle->initialized) ? 1 : 0;
}

/**
 * @brief 获取最小阻值
 */
uint32_t Resistor_GetMinResistance(void)
{
    return MIN_RESISTANCE_OHMS;
}

/**
 * @brief 获取最大阻值 (计算所有阻值继电器总和)
 */
uint32_t Resistor_GetMaxResistance(void)
{
    // 软件限幅最大阻值，与当前继电器配置保持一致
    return MAX_RESISTANCE_OHMS;
}

/**
 * @brief 获取分辨率
 */
uint32_t Resistor_GetResolution(void)
{
    return 1000; // 1kΩ
}

/**
 * @brief 获取最大阻值显示值
 */
uint32_t Resistor_GetMaxResolution(void)
{
    // 别名函数，返回硬件最大阻值
    return MAX_RESISTANCE_OHMS;
}
