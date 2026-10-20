#include "relay_control.h"
#include <stdio.h>

// 继电器GPIO映射表 - 根据EOL.md文档修正
static const struct {
    GPIO_TypeDef* port;
    uint16_t pin;
} relay_gpio_map[TOTAL_RELAYS] = {
    // 阻值继电器 (K1-K18)
    [RELAY_K1]  = {GPIOE, GPIO_PIN_13},   // PE13
    [RELAY_K2]  = {GPIOE, GPIO_PIN_12},   // PE12
    [RELAY_K3]  = {GPIOE, GPIO_PIN_11},   // PE11
    [RELAY_K4]  = {GPIOE, GPIO_PIN_10},   // PE10
    [RELAY_K5]  = {GPIOE, GPIO_PIN_9},    // PE9

    [RELAY_K6]  = {GPIOE, GPIO_PIN_8},    // PE8
    [RELAY_K7]  = {GPIOB, GPIO_PIN_6},    // PB6
    [RELAY_K8]  = {GPIOB, GPIO_PIN_5},    // PB5
    [RELAY_K9]  = {GPIOB, GPIO_PIN_4},    // PB4
    [RELAY_K10] = {GPIOB, GPIO_PIN_3},    // PB3

    [RELAY_K11] = {GPIOD, GPIO_PIN_7},    // PD7
    [RELAY_K12] = {GPIOD, GPIO_PIN_6},    // PD6
    [RELAY_K13] = {GPIOD, GPIO_PIN_5},    // PD5
    [RELAY_K14] = {GPIOD, GPIO_PIN_4},    // PD4
    [RELAY_K15] = {GPIOD, GPIO_PIN_3},    // PD3

    [RELAY_K16] = {GPIOD, GPIO_PIN_2},    // PD2
    [RELAY_K17] = {GPIOD, GPIO_PIN_1},    // PD1
    [RELAY_K18] = {GPIOD, GPIO_PIN_0},    // PD0

    // 输出隔离继电器 (K19, 常闭点)
    [RELAY_K19] = {GPIOC, GPIO_PIN_12}    // PC12 - ON时断开输出
};

#if defined(RELAY_RESISTANCE_PROFILE_30M)
// 继电器电阻值表 - 30MΩ环境（K1-K18参与阻值组合）
static const uint32_t relay_resistance_values[TOTAL_RELAYS] = {
    // kΩ 档位
    [RELAY_K1]  = 1000,     // PE13 - 1kΩ
    [RELAY_K2]  = 2000,     // PE12 - 2kΩ
    [RELAY_K3]  = 2000,     // PE11 - 2kΩ
    [RELAY_K4]  = 5000,     // PE10 - 5kΩ
    [RELAY_K5]  = 10000,    // PE9  - 10kΩ
    [RELAY_K6]  = 20000,    // PE8  - 20kΩ
    [RELAY_K7]  = 20000,    // PB6  - 20kΩ
    [RELAY_K8]  = 50000,    // PB5  - 50kΩ
    [RELAY_K9]  = 100000,   // PB4  - 100kΩ
    [RELAY_K10] = 200000,   // PB3  - 200kΩ
    [RELAY_K11] = 200000,   // PD7  - 200kΩ
    [RELAY_K12] = 500000,   // PD6  - 500kΩ

    // MΩ 档位
    [RELAY_K13] = 1000000,  // PD5  - 1MΩ
    [RELAY_K14] = 2000000,  // PD4  - 2MΩ
    [RELAY_K15] = 2000000,  // PD3  - 2MΩ
    [RELAY_K16] = 5000000,  // PD2  - 5MΩ
    [RELAY_K17] = 10000000, // PD1  - 10MΩ
    [RELAY_K18] = 20000000, // PD0  - 20MΩ

    // K19是输出隔离继电器，不参与阻值计算
    [RELAY_K19] = 0
};
#else
// 继电器电阻值表 - 默认5MΩ环境（K1-K18参与阻值组合）
static const uint32_t relay_resistance_values[TOTAL_RELAYS] = {
    // 1kΩ 档位 (K1-K5)
    [RELAY_K1]  = 1000,    // PE13 - 1kΩ 基数
    [RELAY_K2]  = 2000,    // PE12 - 2kΩ 1k组合
    [RELAY_K3]  = 2000,    // PE11 - 2kΩ 1k组合
    [RELAY_K4]  = 2000,    // PE10 - 2kΩ 1k组合
    [RELAY_K5]  = 2000,    // PE9  - 2kΩ 1k组合

    // 10kΩ 档位 (K6-K10)
    [RELAY_K6]  = 10000,   // PE8  - 10kΩ 基数
    [RELAY_K7]  = 20000,   // PB6  - 20kΩ 10k组合
    [RELAY_K8]  = 20000,   // PB5  - 20kΩ 10k组合
    [RELAY_K9]  = 20000,   // PB4  - 20kΩ 10k组合
    [RELAY_K10] = 20000,   // PB3  - 20kΩ 10k组合

    // 100kΩ 档位 (K11-K15)
    [RELAY_K11] = 100000,  // PD7  - 100kΩ 基数
    [RELAY_K12] = 200000,  // PD6  - 200kΩ 100k组合
    [RELAY_K13] = 200000,  // PD5  - 200kΩ 100k组合
    [RELAY_K14] = 200000,  // PD4  - 200kΩ 100k组合
    [RELAY_K15] = 200000,  // PD3  - 200kΩ 100k组合

    // 1MΩ 档位 (K16-K18)
    [RELAY_K16] = 1000000, // PD2  - 1MΩ 基数
    [RELAY_K17] = 2000000, // PD1  - 2MΩ 1M组合
    [RELAY_K18] = 2000000, // PD0  - 2MΩ 1M组合

    // K19是输出隔离继电器，不参与阻值计算
    [RELAY_K19] = 0
};
#endif

/**
 * @brief 初始化继电器控制系统
 */
Relay_Status Relay_Init(Relay_Handle *handle)
{
    if (handle == NULL) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }

    // 使能GPIO时钟
    __HAL_RCC_GPIOC_CLK_ENABLE();
    __HAL_RCC_GPIOE_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOD_CLK_ENABLE();
    // 释放 JTAG 对 PB3/PB4 的复用，保留 SWD 调试，确保 K9/K10 控制可用
#if defined(__HAL_AFIO_REMAP_SWJ_NOJTAG)
    __HAL_AFIO_REMAP_SWJ_NOJTAG();
#endif

    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;

    // 初始化所有继电器GPIO引脚
    for (int i = 0; i < TOTAL_RELAYS; i++) {
        handle->relays[i].port = (GPIO_TypeDef*)relay_gpio_map[i].port;
        handle->relays[i].pin = relay_gpio_map[i].pin;
        handle->relays[i].state = (i == RELAY_K19) ? 1 : 0;

        // 配置GPIO引脚
        GPIO_InitStruct.Pin = relay_gpio_map[i].pin;
        HAL_GPIO_Init(handle->relays[i].port, &GPIO_InitStruct);

        // K19为常闭输出隔离继电器，默认吸合以断开0Ω输出；K1-K18默认关闭。
        HAL_GPIO_WritePin(handle->relays[i].port,
                         handle->relays[i].pin,
                         handle->relays[i].state ? GPIO_PIN_SET : GPIO_PIN_RESET);
    }

    handle->current_resistance = 0;
    handle->initialized = 1;

    printf("继电器控制系统初始化完成，K1-K18关闭，K19吸合断开0Ω输出\r\n");
    return RELAY_OK;
}

/**
 * @brief 设置单个继电器状态
 */
Relay_Status Relay_SetState(Relay_Handle *handle, Relay_Number relay, uint8_t state)
{
    if (handle == NULL || !handle->initialized) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }

    if (relay < 0 || relay >= TOTAL_RELAYS) {
        return RELAY_ERROR_INVALID_RELAY;
    }

    handle->relays[relay].state = state ? 1 : 0;

    GPIO_PinState gpio_state = state ? GPIO_PIN_SET : GPIO_PIN_RESET;
    HAL_GPIO_WritePin(handle->relays[relay].port,
                     handle->relays[relay].pin,
                     gpio_state);

    return RELAY_OK;
}

/**
 * @brief 获取单个继电器状态
 */
Relay_Status Relay_GetState(Relay_Handle *handle, Relay_Number relay, uint8_t *state)
{
    if (handle == NULL || !handle->initialized || state == NULL) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }

    if (relay < 0 || relay >= TOTAL_RELAYS) {
        return RELAY_ERROR_INVALID_RELAY;
    }

    *state = handle->relays[relay].state;
    return RELAY_OK;
}

/**
 * @brief 根据继电器状态计算总阻值
 */
Relay_Status Relay_CalculateResistanceFromRelays(uint32_t relay_mask, uint32_t *resistance)
{
    if (resistance == NULL) {
        return RELAY_ERROR_INVALID_RESISTANCE;
    }

    uint32_t total = 0;
    for (int i = 0; i < RESISTANCE_RELAYS; i++) {
        if (relay_mask & (1U << i)) {
            total += relay_resistance_values[i];
        }
    }

    *resistance = total;
    return RELAY_OK;
}

/**
 * @brief 根据目标阻值计算继电器控制掩码 (贪心算法)
 */
Relay_Status Relay_CalculateRelayMaskFromResistance(uint32_t target_resistance, uint32_t *relay_mask)
{
    if (relay_mask == NULL) {
        return RELAY_ERROR_INVALID_RESISTANCE;
    }

    if (target_resistance < MIN_RESISTANCE_OHMS || target_resistance > MAX_RESISTANCE_OHMS) {
        return RELAY_ERROR_INVALID_RESISTANCE;
    }

    uint32_t remaining = target_resistance;
    uint32_t mask = 0;

    // 按照电阻值从大到小排序，使用贪心算法（仅K1-K18参与阻值组合）
    Relay_Number sorted_relays[RESISTANCE_RELAYS] = {
#if defined(RELAY_RESISTANCE_PROFILE_30M)
        RELAY_K18,            // 20MΩ
        RELAY_K17,            // 10MΩ
        RELAY_K16,            // 5MΩ
        RELAY_K14, RELAY_K15, // 2MΩ
        RELAY_K13,            // 1MΩ
        RELAY_K12,            // 500kΩ
        RELAY_K10, RELAY_K11, // 200kΩ
        RELAY_K9,             // 100kΩ
        RELAY_K8,             // 50kΩ
        RELAY_K6,  RELAY_K7,  // 20kΩ
        RELAY_K5,             // 10kΩ
        RELAY_K4,             // 5kΩ
        RELAY_K2,  RELAY_K3,  // 2kΩ
        RELAY_K1              // 1kΩ
#else
        RELAY_K17, RELAY_K18, // 2MΩ
        RELAY_K16,            // 1MΩ
        RELAY_K12, RELAY_K13, RELAY_K14, RELAY_K15, // 200kΩ
        RELAY_K11,            // 100kΩ
        RELAY_K7,  RELAY_K8, RELAY_K9, RELAY_K10,   // 20kΩ
        RELAY_K6,             // 10kΩ
        RELAY_K2,  RELAY_K3, RELAY_K4, RELAY_K5,    // 2kΩ
        RELAY_K1              // 1kΩ
#endif
    };

    // 贪心算法选择继电器
    for (int i = 0; i < RESISTANCE_RELAYS; i++) {
        Relay_Number relay = sorted_relays[i];
        uint32_t resistor_value = relay_resistance_values[relay];

        if (remaining >= resistor_value) {
            mask |= (1U << relay);
            remaining -= resistor_value;
        }

        if (remaining == 0) {
            break;
        }
    }

    *relay_mask = mask;
    return RELAY_OK;
}

/**
 * @brief 设置阻值
 */
Relay_Status Relay_SetResistance(Relay_Handle *handle, uint32_t resistance_ohms)
{
    if (handle == NULL || !handle->initialized) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }

    if (resistance_ohms < MIN_RESISTANCE_OHMS || resistance_ohms > MAX_RESISTANCE_OHMS) {
        printf("错误: 目标阻值 %luΩ 超出范围 (%uΩ-%uΩ)\r\n",
               resistance_ohms, MIN_RESISTANCE_OHMS, MAX_RESISTANCE_OHMS);
        return RELAY_ERROR_INVALID_RESISTANCE;
    }

    // 计算需要的继电器组合
    uint32_t relay_mask;
    Relay_Status status = Relay_CalculateRelayMaskFromResistance(resistance_ohms, &relay_mask);
    if (status != RELAY_OK) {
        return status;
    }

    // 切换阻值前先吸合K19，打开常闭点，避免短暂输出0Ω或中间组合。
    Relay_SetState(handle, RELAY_K19, 1);

    // 先关闭所有阻值继电器
    for (int i = 0; i < RESISTANCE_RELAYS; i++) {
        Relay_SetState(handle, (Relay_Number)i, 0);
    }

    if (resistance_ohms == 0) {
        handle->current_resistance = 0;
        printf("设置阻值: 目标 0Ω, K19吸合，输出已断开\r\n");
        return RELAY_OK;
    }

    // 开启需要的继电器
    for (int i = 0; i < RESISTANCE_RELAYS; i++) {
        if (relay_mask & (1U << i)) {
            Relay_SetState(handle, (Relay_Number)i, 1);
        }
    }

    // 计算实际阻值
    uint32_t actual_resistance;
    Relay_CalculateResistanceFromRelays(relay_mask, &actual_resistance);
    handle->current_resistance = actual_resistance;

    // 非0阻值已建立后释放K19，闭合常闭点并输出设定阻值。
    Relay_SetState(handle, RELAY_K19, 0);

    printf("设置阻值: 目标 %luΩ, 实际 %luΩ, 误差 %ldΩ\r\n",
           resistance_ohms, actual_resistance,
           (long)(actual_resistance - resistance_ohms));

    return RELAY_OK;
}

/**
 * @brief 获取当前阻值
 */
Relay_Status Relay_GetResistance(Relay_Handle *handle, uint32_t *resistance_ohms)
{
    if (handle == NULL || !handle->initialized || resistance_ohms == NULL) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }

    uint32_t relay_mask = 0;
    for (int i = 0; i < RESISTANCE_RELAYS; i++) {
        if (handle->relays[i].state) {
            relay_mask |= (1U << i);
        }
    }

    return Relay_CalculateResistanceFromRelays(relay_mask, resistance_ohms);
}

/**
 * @brief 断开输出并关闭所有阻值继电器
 */
Relay_Status Relay_SetAllRelaysOff(Relay_Handle *handle)
{
    if (handle == NULL || !handle->initialized) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }

    // 先吸合K19断开输出，再关闭K1-K18，避免对外呈现0Ω。
    Relay_SetState(handle, RELAY_K19, 1);

    for (int i = 0; i < RESISTANCE_RELAYS; i++) {
        Relay_SetState(handle, (Relay_Number)i, 0);
    }

    handle->current_resistance = 0;
    printf("K1-K18已关闭，K19吸合，输出已断开\r\n");
    return RELAY_OK;
}

/**
 * @brief 打印继电器状态信息
 */
Relay_Status Relay_PrintStatus(Relay_Handle *handle)
{
    if (handle == NULL || !handle->initialized) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }

    printf("\r\n=== 继电器控制系统状态 ===\r\n");
    uint32_t current_resistance;
    Relay_GetResistance(handle, &current_resistance);
    printf("当前阻值: %luΩ\r\n", current_resistance);

    printf("继电器状态:\r\n");
    const char* relay_names[] = {
#if defined(RELAY_RESISTANCE_PROFILE_30M)
        "K1(1k)", "K2(2k)", "K3(2k)", "K4(5k)", "K5(10k)",
        "K6(20k)", "K7(20k)", "K8(50k)", "K9(100k)", "K10(200k)",
        "K11(200k)", "K12(500k)", "K13(1M)", "K14(2M)", "K15(2M)",
        "K16(5M)", "K17(10M)", "K18(20M)", "K19(OUT_NC)"
#else
        "K1(1k)", "K2(2k)", "K3(2k)", "K4(2k)", "K5(2k)",
        "K6(10k)", "K7(20k)", "K8(20k)", "K9(20k)", "K10(20k)",
        "K11(100k)", "K12(200k)", "K13(200k)", "K14(200k)", "K15(200k)",
        "K16(1M)", "K17(2M)", "K18(2M)", "K19(OUT_NC)"
#endif
    };

    int active_count = 0;
    for (int i = 0; i < TOTAL_RELAYS; i++) {
        if (handle->relays[i].state) {
            printf("  %-8s: ON\r\n", relay_names[i]);
            active_count++;
        }
    }

    printf("活跃继电器: %d/%d\r\n", active_count, TOTAL_RELAYS);
    printf("==========================\r\n\r\n");

    return RELAY_OK;
}
