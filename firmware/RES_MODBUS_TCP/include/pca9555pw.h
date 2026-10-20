#ifndef PCA9555PW_H
#define PCA9555PW_H

#include "stm32f4xx_hal.h"

#define PCA9555PW_BASE_ADDRESS 0x20

typedef enum {
    PCA9555PW_OK = 0,
    PCA9555PW_ERROR
} PCA9555PW_Status;

typedef struct {
    I2C_HandleTypeDef *i2c_handle;
    uint8_t address;
} PCA9555PW_Handle;

PCA9555PW_Status PCA9555PW_Init(PCA9555PW_Handle *handle, I2C_HandleTypeDef *i2c_handle, uint8_t address);
PCA9555PW_Status PCA9555PW_WritePort(PCA9555PW_Handle *handle, uint16_t value);
PCA9555PW_Status PCA9555PW_ReadPort(PCA9555PW_Handle *handle, uint16_t *value);

#endif // PCA9555PW_H
