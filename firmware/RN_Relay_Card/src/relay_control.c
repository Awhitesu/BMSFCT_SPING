#include "relay_control.h"
#include "main.h"

static const Relay_Channel relay_map[RELAY_MAX_CHANNEL_COUNT] = {
    {RELAY_K1_GPIO_Port,  RELAY_K1_Pin,  0},
    {RELAY_K2_GPIO_Port,  RELAY_K2_Pin,  0},
    {RELAY_K3_GPIO_Port,  RELAY_K3_Pin,  0},
    {RELAY_K4_GPIO_Port,  RELAY_K4_Pin,  0},
    {RELAY_K5_GPIO_Port,  RELAY_K5_Pin,  0},
    {RELAY_K6_GPIO_Port,  RELAY_K6_Pin,  0},
    {RELAY_K7_GPIO_Port,  RELAY_K7_Pin,  0},
    {RELAY_K8_GPIO_Port,  RELAY_K8_Pin,  0},
    {RELAY_K9_GPIO_Port,  RELAY_K9_Pin,  0},
    {RELAY_K10_GPIO_Port, RELAY_K10_Pin, 0},
    {RELAY_K11_GPIO_Port, RELAY_K11_Pin, 0},
    {RELAY_K12_GPIO_Port, RELAY_K12_Pin, 0},
    {RELAY_K13_GPIO_Port, RELAY_K13_Pin, 0},
    {RELAY_K14_GPIO_Port, RELAY_K14_Pin, 0},
    {RELAY_K15_GPIO_Port, RELAY_K15_Pin, 0},
    {RELAY_K16_GPIO_Port, RELAY_K16_Pin, 0},
    {RELAY_K17_GPIO_Port, RELAY_K17_Pin, 0},
    {RELAY_K18_GPIO_Port, RELAY_K18_Pin, 0},
};

Relay_Status Relay_Init(Relay_Handle *handle)
{
    if (handle == NULL) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }

    for (uint16_t i = 0; i < RELAY_MAX_CHANNEL_COUNT; ++i) {
        handle->channels[i] = relay_map[i];
        HAL_GPIO_WritePin(handle->channels[i].port, handle->channels[i].pin, GPIO_PIN_RESET);
    }

    handle->initialized = 1;
    return RELAY_OK;
}

Relay_Status Relay_SetChannel(Relay_Handle *handle, uint16_t channel, uint8_t state)
{
    if (handle == NULL || !handle->initialized) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }
    if (channel >= RELAY_CHANNEL_COUNT) {
        return RELAY_ERROR_INVALID_CHANNEL;
    }

    handle->channels[channel].state = state ? 1U : 0U;
    HAL_GPIO_WritePin(handle->channels[channel].port,
                      handle->channels[channel].pin,
                      state ? GPIO_PIN_SET : GPIO_PIN_RESET);
    return RELAY_OK;
}

Relay_Status Relay_GetChannel(const Relay_Handle *handle, uint16_t channel, uint8_t *state)
{
    if (handle == NULL || !handle->initialized || state == NULL) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }
    if (channel >= RELAY_CHANNEL_COUNT) {
        return RELAY_ERROR_INVALID_CHANNEL;
    }

    *state = handle->channels[channel].state;
    return RELAY_OK;
}

Relay_Status Relay_SetAll(Relay_Handle *handle, uint32_t mask)
{
    if (handle == NULL || !handle->initialized) {
        return RELAY_ERROR_NOT_INITIALIZED;
    }

    for (uint16_t i = 0; i < RELAY_CHANNEL_COUNT; ++i) {
        Relay_SetChannel(handle, i, (mask & (1UL << i)) ? 1U : 0U);
    }
    return RELAY_OK;
}

uint32_t Relay_GetMask(const Relay_Handle *handle)
{
    uint32_t mask = 0;

    if (handle == NULL || !handle->initialized) {
        return 0;
    }

    for (uint16_t i = 0; i < RELAY_CHANNEL_COUNT; ++i) {
        if (handle->channels[i].state) {
            mask |= (1UL << i);
        }
    }
    return mask;
}

void Relay_AllOff(Relay_Handle *handle)
{
    if (handle == NULL || !handle->initialized) {
        return;
    }

    (void)Relay_SetAll(handle, 0);
}

uint16_t Relay_GetChannelCount(void)
{
    return (uint16_t)RELAY_CHANNEL_COUNT;
}

uint8_t Relay_IsInitialized(const Relay_Handle *handle)
{
    return (handle != NULL && handle->initialized) ? 1U : 0U;
}
