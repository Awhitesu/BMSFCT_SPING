#ifndef APP_CONFIG_H
#define APP_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

#ifndef RELAY_CHANNEL_COUNT
#define RELAY_CHANNEL_COUNT 18U
#endif

#define RELAY_MAX_CHANNEL_COUNT 18U

#if RELAY_CHANNEL_COUNT < 1U
#error "RELAY_CHANNEL_COUNT must be at least 1"
#endif

#if RELAY_CHANNEL_COUNT > RELAY_MAX_CHANNEL_COUNT
#error "RELAY_CHANNEL_COUNT cannot exceed RELAY_MAX_CHANNEL_COUNT"
#endif

#if defined(RN_BOARD_TYPE_DMM_CARD)
#define RN_BOARD_NAME "DMM Card"
#elif defined(RN_BOARD_TYPE_HSD_CARD)
#define RN_BOARD_NAME "HSD Card"
#elif defined(RN_BOARD_TYPE_HV_CARD)
#define RN_BOARD_NAME "HV Card"
#elif defined(RN_BOARD_TYPE_RELAY_CARD)
#define RN_BOARD_NAME "Relay Card"
#else
#define RN_BOARD_NAME "DMM Card"
#endif

#ifdef __cplusplus
}
#endif

#endif
