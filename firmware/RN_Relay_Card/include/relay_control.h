#ifndef RELAY_CONTROL_H
#define RELAY_CONTROL_H

#include "app_config.h"
#include "stm32f4xx_hal.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    RELAY_OK = 0,
    RELAY_ERROR_INVALID_CHANNEL,
    RELAY_ERROR_NOT_INITIALIZED
} Relay_Status;

typedef struct {
    GPIO_TypeDef *port;
    uint16_t pin;
    uint8_t state;
} Relay_Channel;

typedef struct {
    Relay_Channel channels[RELAY_MAX_CHANNEL_COUNT];
    uint8_t initialized;
} Relay_Handle;

Relay_Status Relay_Init(Relay_Handle *handle);
Relay_Status Relay_SetChannel(Relay_Handle *handle, uint16_t channel, uint8_t state);
Relay_Status Relay_GetChannel(const Relay_Handle *handle, uint16_t channel, uint8_t *state);
Relay_Status Relay_SetAll(Relay_Handle *handle, uint32_t mask);
uint32_t Relay_GetMask(const Relay_Handle *handle);
void Relay_AllOff(Relay_Handle *handle);
uint16_t Relay_GetChannelCount(void);
uint8_t Relay_IsInitialized(const Relay_Handle *handle);

#ifdef __cplusplus
}
#endif

#endif
