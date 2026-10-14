#include "tca9548a.h"

TCA9548A_Status TCA9548A_Init(TCA9548A_Handle *handle, I2C_HandleTypeDef *i2c_handle, uint8_t address)
{
    if (handle == NULL || i2c_handle == NULL) {
        return TCA9548A_ERROR;
    }

    handle->i2c_handle = i2c_handle;
    handle->address = address << 1; // Shift for HAL

    if (HAL_I2C_IsDeviceReady(handle->i2c_handle, handle->address, 2, 100) != HAL_OK) {
        return TCA9548A_ERROR;
    }

    return TCA9548A_DeselectAllChannels(handle);
}

TCA9548A_Status TCA9548A_SelectChannel(TCA9548A_Handle *handle, uint8_t channel)
{
    if (handle == NULL || channel > 7) {
        return TCA9548A_ERROR;
    }
    uint8_t control_reg = 1 << channel;
    if (HAL_I2C_Master_Transmit(handle->i2c_handle, handle->address, &control_reg, 1, 100) != HAL_OK) {
        return TCA9548A_ERROR;
    }
    return TCA9548A_OK;
}

TCA9548A_Status TCA9548A_DeselectAllChannels(TCA9548A_Handle *handle)
{
    if (handle == NULL) {
        return TCA9548A_ERROR;
    }
    uint8_t control_reg = 0x00;
    if (HAL_I2C_Master_Transmit(handle->i2c_handle, handle->address, &control_reg, 1, 100) != HAL_OK) {
        return TCA9548A_ERROR;
    }
    return TCA9548A_OK;
}
