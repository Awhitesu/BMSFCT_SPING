#ifndef TCA9548A_H
#define TCA9548A_H

#include "stm32f4xx_hal.h"

#define TCA9548A_BASE_ADDRESS 0x70

typedef enum {
    TCA9548A_OK = 0,
    TCA9548A_ERROR
} TCA9548A_Status;

typedef struct {
    I2C_HandleTypeDef *i2c_handle;
    uint8_t address;
} TCA9548A_Handle;


TCA9548A_Status TCA9548A_Init(TCA9548A_Handle *handle, I2C_HandleTypeDef *i2c_handle, uint8_t address);
TCA9548A_Status TCA9548A_SelectChannel(TCA9548A_Handle *handle, uint8_t channel);
TCA9548A_Status TCA9548A_DeselectAllChannels(TCA9548A_Handle *handle);

#endif // TCA9548A_H
