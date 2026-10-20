#include "pca9555pw.h"

PCA9555PW_Status PCA9555PW_Init(PCA9555PW_Handle *handle, I2C_HandleTypeDef *i2c_handle, uint8_t address)
{
    if (handle == NULL || i2c_handle == NULL) {
        return PCA9555PW_ERROR;
    }

    handle->i2c_handle = i2c_handle;
    handle->address = address << 1; // Shift for HAL

    if (HAL_I2C_IsDeviceReady(handle->i2c_handle, handle->address, 2, 100) != HAL_OK) {
        return PCA9555PW_ERROR;
    }

    // First set all outputs to HIGH (0xFF) to ensure relays are open
    uint8_t output_data[2] = {0x00, 0x00}; // All outputs HIGH
    if (HAL_I2C_Mem_Write(handle->i2c_handle, handle->address, 0x02, I2C_MEMADD_SIZE_8BIT, output_data, 2, 100) != HAL_OK) {
        return PCA9555PW_ERROR;
    }

    // Configure all pins as outputs
    uint8_t config[2] = {0x00, 0x00}; // All pins as output
    if (HAL_I2C_Mem_Write(handle->i2c_handle, handle->address, 0x06, I2C_MEMADD_SIZE_8BIT, config, 2, 100) != HAL_OK) {
        return PCA9555PW_ERROR;
    }

    return PCA9555PW_OK;
}

PCA9555PW_Status PCA9555PW_WritePort(PCA9555PW_Handle *handle, uint16_t value)
{
    if (handle == NULL) {
        return PCA9555PW_ERROR;
    }
    uint8_t data[2];
    data[0] = (uint8_t)(value & 0xFF);
    data[1] = (uint8_t)(value >> 8);

    if (HAL_I2C_Mem_Write(handle->i2c_handle, handle->address, 0x02, I2C_MEMADD_SIZE_8BIT, data, 2, 100) != HAL_OK) {
        return PCA9555PW_ERROR;
    }

    return PCA9555PW_OK;
}

PCA9555PW_Status PCA9555PW_ReadPort(PCA9555PW_Handle *handle, uint16_t *value)
{
    if (handle == NULL || value == NULL) {
        return PCA9555PW_ERROR;
    }
    uint8_t data[2];
    if (HAL_I2C_Mem_Read(handle->i2c_handle, handle->address, 0x00, I2C_MEMADD_SIZE_8BIT, data, 2, 100) != HAL_OK) {
        return PCA9555PW_ERROR;
    }
    *value = ((uint16_t)data[1] << 8) | data[0];
    return PCA9555PW_OK;
}
