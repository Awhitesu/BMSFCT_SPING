#ifndef RESISTOR_CONTROL_H
#define RESISTOR_CONTROL_H

#include "stm32f4xx_hal.h"
#include "relay_control.h"

// 前向声明以避免循环包含
struct Modbus_TCP_Server_s;
typedef struct Modbus_TCP_Server_s Modbus_TCP_Server;

// 新的电阻控制系统参数
#define RESISTOR_VERSION "1.1.0"
#define RESISTOR_CHANNELS 1           // 单通道系统

// 通道定义 (为兼容性保留，但只有CH1)
typedef enum {
    RESISTOR_CH1 = 0,
    RESISTOR_CHANNELS_TOTAL = 1
} Resistor_Channel;

// 状态码定义
typedef enum {
    RESISTOR_OK = 0,
    RESISTOR_ERROR_INVALID_CHANNEL,
    RESISTOR_ERROR_INVALID_RESISTANCE,
    RESISTOR_ERROR_NOT_INITIALIZED
} Resistor_Status;

// 新的电阻控制句柄
typedef struct {
    Relay_Handle relay_handle;
    uint32_t channel_resistances[RESISTOR_CHANNELS_TOTAL];
    uint8_t channel_enabled[RESISTOR_CHANNELS_TOTAL];
    uint8_t initialized;
} Resistor_Handle;

// 主要接口函数
Resistor_Status Resistor_Init(Resistor_Handle *handle);

// 通道控制函数
Resistor_Status Resistor_SetChannelValue(Resistor_Handle *handle, uint8_t channel, uint32_t resistance_ohms);
Resistor_Status Resistor_GetChannelValue(Resistor_Handle *handle, uint8_t channel, uint32_t *resistance_ohms);
Resistor_Status Resistor_DisableChannel(Resistor_Handle *handle, uint8_t channel);

// 批量操作函数 (为兼容性保留)
Resistor_Status Resistor_SetAllChannels(Resistor_Handle *handle, uint32_t resistance_ohms);
Resistor_Status Resistor_DisableAllChannels(Resistor_Handle *handle);

// 系统状态函数
Resistor_Status Resistor_PrintSystemInfo(Resistor_Handle *handle);
uint8_t Resistor_IsInitialized(Resistor_Handle *handle);

// 电阻范围查询函数
uint32_t Resistor_GetMinResistance(void);
uint32_t Resistor_GetMaxResistance(void);
uint32_t Resistor_GetResolution(void);
uint32_t Resistor_GetMaxResolution(void); // 别名函数

#endif // RESISTOR_CONTROL_H