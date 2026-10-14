#ifndef RELAY_CONTROL_H
#define RELAY_CONTROL_H

#include "stm32f4xx_hal.h"

// 继电器配置参数
#define TOTAL_RELAYS 19
#define RESISTANCE_RELAYS 18
#define MIN_RESISTANCE_OHMS 0         // 允许全旁路(0Ω)
#if defined(RELAY_RESISTANCE_PROFILE_30M)
#define MAX_RESISTANCE_OHMS 30000000  // 30MΩ
#else
#define MAX_RESISTANCE_OHMS 5000000   // 5MΩ
#endif

// 继电器编号定义 - 根据EOL.md文档修正
typedef enum {
    RELAY_K1 = 0,    // PE13
    RELAY_K2 = 1,    // PE12
    RELAY_K3 = 2,    // PE11
    RELAY_K4 = 3,    // PE10
    RELAY_K5 = 4,    // PE9

    RELAY_K6 = 5,    // PE8
    RELAY_K7 = 6,    // PB6
    RELAY_K8 = 7,    // PB5
    RELAY_K9 = 8,    // PB4
    RELAY_K10 = 9,   // PB3

    RELAY_K11 = 10,  // PD7
    RELAY_K12 = 11,  // PD6
    RELAY_K13 = 12,  // PD5
    RELAY_K14 = 13,  // PD4
    RELAY_K15 = 14,  // PD3

    RELAY_K16 = 15,  // PD2
    RELAY_K17 = 16,  // PD1
    RELAY_K18 = 17,  // PD0

    RELAY_K19 = 18   // PC12 - 输出隔离继电器(常闭点)
} Relay_Number;

// 继电器状态结构
typedef struct {
    GPIO_TypeDef* port;
    uint16_t pin;
    uint8_t state;  // 0=关闭(旁路), 1=开启(接入)
} Relay_Definition;

// 继电器控制句柄
typedef struct {
    Relay_Definition relays[TOTAL_RELAYS];
    uint32_t current_resistance;
    uint8_t initialized;
} Relay_Handle;

// 状态码定义
typedef enum {
    RELAY_OK = 0,
    RELAY_ERROR_INVALID_RELAY,
    RELAY_ERROR_INVALID_RESISTANCE,
    RELAY_ERROR_NOT_INITIALIZED
} Relay_Status;

// 主要接口函数
Relay_Status Relay_Init(Relay_Handle *handle);
Relay_Status Relay_SetResistance(Relay_Handle *handle, uint32_t resistance_ohms);
Relay_Status Relay_GetResistance(Relay_Handle *handle, uint32_t *resistance_ohms);
Relay_Status Relay_SetAllRelaysOff(Relay_Handle *handle);

// 单个继电器控制函数
Relay_Status Relay_SetState(Relay_Handle *handle, Relay_Number relay, uint8_t state);
Relay_Status Relay_GetState(Relay_Handle *handle, Relay_Number relay, uint8_t *state);

// 电阻计算辅助函数
Relay_Status Relay_CalculateResistanceFromRelays(uint32_t relay_mask, uint32_t *resistance);
Relay_Status Relay_CalculateRelayMaskFromResistance(uint32_t target_resistance, uint32_t *relay_mask);

// 状态查询函数
Relay_Status Relay_PrintStatus(Relay_Handle *handle);

#endif // RELAY_CONTROL_H
