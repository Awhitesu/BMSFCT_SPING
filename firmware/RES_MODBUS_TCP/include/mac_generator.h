#ifndef MAC_GENERATOR_H
#define MAC_GENERATOR_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void MAC_GenerateFromUID(uint8_t mac[6]);
void MAC_PrintAddress(const uint8_t mac[6]);

#ifdef __cplusplus
}
#endif

#endif /* MAC_GENERATOR_H */