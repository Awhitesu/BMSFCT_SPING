#ifndef KSZ8863RLLI_H
#define KSZ8863RLLI_H

#include "stm32f4xx_hal.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    KSZ8863RLLI_OK = 0,
    KSZ8863RLLI_ERROR,
    KSZ8863RLLI_UNSUPPORTED,
    KSZ8863RLLI_ID_MISMATCH
} KSZ8863RLLI_Status;

typedef enum {
    KSZ8863RLLI_BUS_NONE = 0,
    KSZ8863RLLI_BUS_MIIM,
    KSZ8863RLLI_BUS_SMI,
    KSZ8863RLLI_BUS_I2C,
    KSZ8863RLLI_BUS_SPI
} KSZ8863RLLI_BusMode;

typedef enum {
    KSZ8863RLLI_PORT1 = 1,
    KSZ8863RLLI_PORT2 = 2,
    KSZ8863RLLI_PORT3 = 3
} KSZ8863RLLI_Port;

typedef enum {
    KSZ8863RLLI_RMII_CLOCK_EXTERNAL = 0,
    KSZ8863RLLI_RMII_CLOCK_INTERNAL = 1
} KSZ8863RLLI_RmiiClock;

typedef KSZ8863RLLI_Status (*KSZ8863RLLI_Read8Fn)(void *context, uint8_t reg, uint8_t *value);
typedef KSZ8863RLLI_Status (*KSZ8863RLLI_Write8Fn)(void *context, uint8_t reg, uint8_t value);
typedef KSZ8863RLLI_Status (*KSZ8863RLLI_ReadPhyFn)(void *context, uint8_t phy_addr, uint8_t reg, uint16_t *value);
typedef KSZ8863RLLI_Status (*KSZ8863RLLI_WritePhyFn)(void *context, uint8_t phy_addr, uint8_t reg, uint16_t value);

typedef struct {
    KSZ8863RLLI_BusMode mode;
    KSZ8863RLLI_Read8Fn read8;
    KSZ8863RLLI_Write8Fn write8;
    KSZ8863RLLI_ReadPhyFn read_phy;
    KSZ8863RLLI_WritePhyFn write_phy;
    void *context;
} KSZ8863RLLI_Bus;

typedef struct {
    GPIO_TypeDef *reset_port;
    uint16_t reset_pin;
    GPIO_TypeDef *int_port;
    uint16_t int_pin;
    uint32_t reset_assert_ms;
    uint32_t reset_release_ms;
    KSZ8863RLLI_Bus bus;
} KSZ8863RLLI_Handle;

#define KSZ8863RLLI_REG_CHIP_ID0        0x00U
#define KSZ8863RLLI_REG_CHIP_ID1        0x01U
#define KSZ8863RLLI_REG_GLOBAL_CTRL0    0x02U
#define KSZ8863RLLI_REG_GLOBAL_CTRL1    0x03U
#define KSZ8863RLLI_REG_PORT1_CTRL0     0x10U
#define KSZ8863RLLI_REG_PORT2_CTRL0     0x20U
#define KSZ8863RLLI_REG_PORT3_CTRL0     0x30U
#define KSZ8863RLLI_REG_PORT1_STATUS0   0x1EU
#define KSZ8863RLLI_REG_PORT2_STATUS0   0x2EU
#define KSZ8863RLLI_REG_PORT3_STATUS1   0x3FU
#define KSZ8863RLLI_REG_INTR_ENABLE     0xBBU
#define KSZ8863RLLI_REG_LINK_CHANGE     0xBCU
#define KSZ8863RLLI_REG_POWER_LED       0xC3U
#define KSZ8863RLLI_REG_SLEEP_MODE      0xC4U
#define KSZ8863RLLI_REG_HOST_MODE       0xC6U

#define KSZ8863RLLI_CHIP_ID0_VALUE      0x88U
#define KSZ8863RLLI_CHIP_ID1_MASK       0xF0U
#define KSZ8863RLLI_CHIP_ID1_VALUE      0x30U
#define KSZ8863RLLI_START_SWITCH        0x01U
#define KSZ8863RLLI_PORT_STATUS_LINK_UP 0x20U
#define KSZ8863RLLI_HOST_MODE_P3_RMII_INTERNAL_CLK 0x08U

#define KSZ8863RLLI_PHY1_ADDR          0x01U
#define KSZ8863RLLI_PHY2_ADDR          0x02U
#define KSZ8863RLLI_PHY_BCR            0x00U
#define KSZ8863RLLI_PHY_BSR            0x01U
#define KSZ8863RLLI_PHY_ID_HIGH        0x02U
#define KSZ8863RLLI_PHY_ID_LOW         0x03U
#define KSZ8863RLLI_PHY_ID_HIGH_VALUE  0x0022U
#define KSZ8863RLLI_PHY_ID_LOW_VALUE   0x1430U
#define KSZ8863RLLI_PHY_ID_LOW_MASK    0xFFF0U
#define KSZ8863RLLI_PHY_BCR_AUTONEG    0x1000U
#define KSZ8863RLLI_PHY_BCR_RESTART_AN 0x0200U
#define KSZ8863RLLI_PHY_BSR_LINK_UP    0x0004U

KSZ8863RLLI_Status KSZ8863RLLI_Init(KSZ8863RLLI_Handle *handle);
KSZ8863RLLI_Status KSZ8863RLLI_Reset(const KSZ8863RLLI_Handle *handle);
KSZ8863RLLI_Status KSZ8863RLLI_ConfigureFixedLink(ETH_HandleTypeDef *heth);
KSZ8863RLLI_Status KSZ8863RLLI_ReadReg(const KSZ8863RLLI_Handle *handle, uint8_t reg, uint8_t *value);
KSZ8863RLLI_Status KSZ8863RLLI_WriteReg(const KSZ8863RLLI_Handle *handle, uint8_t reg, uint8_t value);
KSZ8863RLLI_Status KSZ8863RLLI_ReadPhy(const KSZ8863RLLI_Handle *handle, uint8_t phy_addr, uint8_t reg, uint16_t *value);
KSZ8863RLLI_Status KSZ8863RLLI_WritePhy(const KSZ8863RLLI_Handle *handle, uint8_t phy_addr, uint8_t reg, uint16_t value);
KSZ8863RLLI_Status KSZ8863RLLI_VerifyChipId(const KSZ8863RLLI_Handle *handle);
KSZ8863RLLI_Status KSZ8863RLLI_VerifyPhyIds(const KSZ8863RLLI_Handle *handle);
KSZ8863RLLI_Status KSZ8863RLLI_StartSwitch(const KSZ8863RLLI_Handle *handle);
KSZ8863RLLI_Status KSZ8863RLLI_RestartAutoNegotiation(const KSZ8863RLLI_Handle *handle, KSZ8863RLLI_Port port);
KSZ8863RLLI_Status KSZ8863RLLI_SetPort3RmiiClock(const KSZ8863RLLI_Handle *handle, KSZ8863RLLI_RmiiClock clock);
uint8_t KSZ8863RLLI_HasRegisterBus(const KSZ8863RLLI_Handle *handle);
uint8_t KSZ8863RLLI_HasMiimBus(const KSZ8863RLLI_Handle *handle);
uint8_t KSZ8863RLLI_IsManaged(const KSZ8863RLLI_Handle *handle);
uint8_t KSZ8863RLLI_IsPortLinkUp(const KSZ8863RLLI_Handle *handle, KSZ8863RLLI_Port port);
uint8_t KSZ8863RLLI_IsLinkUp(const KSZ8863RLLI_Handle *handle);

#ifdef __cplusplus
}
#endif

#endif
