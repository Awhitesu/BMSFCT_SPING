#include "mac_generator.h"
#include "stm32f4xx_hal.h"
#include <stdio.h>

void MAC_GenerateFromUID(uint8_t mac[6]) {
    const uint32_t uid[3] = {
        HAL_GetUIDw0(),
        HAL_GetUIDw1(),
        HAL_GetUIDw2()
    };
    uint64_t hash = 14695981039346656037ULL;

    for (uint32_t i = 0; i < 3U; i++) {
        uint32_t word = uid[i];

        for (uint32_t byte = 0; byte < 4U; byte++) {
            hash ^= (uint8_t)(word & 0xFFU);
            hash *= 1099511628211ULL;
            word >>= 8;
        }
    }

    hash ^= hash >> 33;
    hash *= 0xff51afd7ed558ccdULL;
    hash ^= hash >> 33;
    hash *= 0xc4ceb9fe1a85ec53ULL;
    hash ^= hash >> 33;

    mac[0] = (uint8_t)((hash >> 40) & 0xFFU);
    mac[1] = (uint8_t)((hash >> 32) & 0xFFU);
    mac[2] = (uint8_t)((hash >> 24) & 0xFFU);
    mac[3] = (uint8_t)((hash >> 16) & 0xFFU);
    mac[4] = (uint8_t)((hash >> 8) & 0xFFU);
    mac[5] = (uint8_t)(hash & 0xFFU);

    mac[0] &= 0xFEU; /* Unicast address. */
    mac[0] |= 0x02U; /* Locally administered address. */
}

void MAC_PrintAddress(const uint8_t mac[6]) {
    printf("MAC Address: %02X:%02X:%02X:%02X:%02X:%02X\r\n",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}
