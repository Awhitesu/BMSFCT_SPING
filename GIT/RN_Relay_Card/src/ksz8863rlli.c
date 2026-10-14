#include "ksz8863rlli.h"

#define KSZ8863RLLI_DEFAULT_RESET_ASSERT_MS  20U
#define KSZ8863RLLI_DEFAULT_RESET_RELEASE_MS 100U

static KSZ8863RLLI_Status KSZ8863RLLI_GetPhyAddress(KSZ8863RLLI_Port port, uint8_t *phy_addr);

KSZ8863RLLI_Status KSZ8863RLLI_Init(KSZ8863RLLI_Handle *handle)
{
    KSZ8863RLLI_Status status;

    if (handle == NULL || handle->reset_port == NULL) {
        return KSZ8863RLLI_ERROR;
    }

    if (handle->reset_assert_ms == 0U) {
        handle->reset_assert_ms = KSZ8863RLLI_DEFAULT_RESET_ASSERT_MS;
    }
    if (handle->reset_release_ms == 0U) {
        handle->reset_release_ms = KSZ8863RLLI_DEFAULT_RESET_RELEASE_MS;
    }

    status = KSZ8863RLLI_Reset(handle);
    if (status != KSZ8863RLLI_OK) {
        return status;
    }

    if (KSZ8863RLLI_HasRegisterBus(handle)) {
        status = KSZ8863RLLI_VerifyChipId(handle);
        if (status != KSZ8863RLLI_OK) {
            return status;
        }
        return KSZ8863RLLI_StartSwitch(handle);
    }

    if (KSZ8863RLLI_HasMiimBus(handle)) {
        status = KSZ8863RLLI_VerifyPhyIds(handle);
        if (status != KSZ8863RLLI_OK) {
            return status;
        }

        if (KSZ8863RLLI_RestartAutoNegotiation(handle, KSZ8863RLLI_PORT1) != KSZ8863RLLI_OK ||
            KSZ8863RLLI_RestartAutoNegotiation(handle, KSZ8863RLLI_PORT2) != KSZ8863RLLI_OK) {
            return KSZ8863RLLI_UNSUPPORTED;
        }

        return KSZ8863RLLI_OK;
    }

    return KSZ8863RLLI_OK;
}

KSZ8863RLLI_Status KSZ8863RLLI_Reset(const KSZ8863RLLI_Handle *handle)
{
    if (handle == NULL || handle->reset_port == NULL) {
        return KSZ8863RLLI_ERROR;
    }

    HAL_GPIO_WritePin(handle->reset_port, handle->reset_pin, GPIO_PIN_RESET);
    HAL_Delay(handle->reset_assert_ms);
    HAL_GPIO_WritePin(handle->reset_port, handle->reset_pin, GPIO_PIN_SET);
    HAL_Delay(handle->reset_release_ms);

    return KSZ8863RLLI_OK;
}

KSZ8863RLLI_Status KSZ8863RLLI_ConfigureFixedLink(ETH_HandleTypeDef *heth)
{
    ETH_MACConfigTypeDef mac_config = {0};

    if (heth == NULL) {
        return KSZ8863RLLI_ERROR;
    }

    HAL_ETH_GetMACConfig(heth, &mac_config);
    mac_config.DuplexMode = ETH_FULLDUPLEX_MODE;
    mac_config.Speed = ETH_SPEED_100M;

    if (HAL_ETH_SetMACConfig(heth, &mac_config) != HAL_OK) {
        return KSZ8863RLLI_ERROR;
    }

    return KSZ8863RLLI_OK;
}

KSZ8863RLLI_Status KSZ8863RLLI_ReadReg(const KSZ8863RLLI_Handle *handle, uint8_t reg, uint8_t *value)
{
    if (handle == NULL || value == NULL || !KSZ8863RLLI_HasRegisterBus(handle)) {
        return KSZ8863RLLI_UNSUPPORTED;
    }

    return handle->bus.read8(handle->bus.context, reg, value);
}

KSZ8863RLLI_Status KSZ8863RLLI_WriteReg(const KSZ8863RLLI_Handle *handle, uint8_t reg, uint8_t value)
{
    if (handle == NULL || !KSZ8863RLLI_HasRegisterBus(handle)) {
        return KSZ8863RLLI_UNSUPPORTED;
    }

    return handle->bus.write8(handle->bus.context, reg, value);
}

KSZ8863RLLI_Status KSZ8863RLLI_ReadPhy(const KSZ8863RLLI_Handle *handle, uint8_t phy_addr, uint8_t reg, uint16_t *value)
{
    if (handle == NULL || value == NULL || !KSZ8863RLLI_HasMiimBus(handle)) {
        return KSZ8863RLLI_UNSUPPORTED;
    }

    return handle->bus.read_phy(handle->bus.context, phy_addr, reg, value);
}

KSZ8863RLLI_Status KSZ8863RLLI_WritePhy(const KSZ8863RLLI_Handle *handle, uint8_t phy_addr, uint8_t reg, uint16_t value)
{
    if (handle == NULL || !KSZ8863RLLI_HasMiimBus(handle)) {
        return KSZ8863RLLI_UNSUPPORTED;
    }

    return handle->bus.write_phy(handle->bus.context, phy_addr, reg, value);
}

KSZ8863RLLI_Status KSZ8863RLLI_VerifyChipId(const KSZ8863RLLI_Handle *handle)
{
    uint8_t id0 = 0;
    uint8_t id1 = 0;

    if (KSZ8863RLLI_ReadReg(handle, KSZ8863RLLI_REG_CHIP_ID0, &id0) != KSZ8863RLLI_OK ||
        KSZ8863RLLI_ReadReg(handle, KSZ8863RLLI_REG_CHIP_ID1, &id1) != KSZ8863RLLI_OK) {
        return KSZ8863RLLI_UNSUPPORTED;
    }

    if (id0 != KSZ8863RLLI_CHIP_ID0_VALUE ||
        (id1 & KSZ8863RLLI_CHIP_ID1_MASK) != KSZ8863RLLI_CHIP_ID1_VALUE) {
        return KSZ8863RLLI_ID_MISMATCH;
    }

    return KSZ8863RLLI_OK;
}

KSZ8863RLLI_Status KSZ8863RLLI_VerifyPhyIds(const KSZ8863RLLI_Handle *handle)
{
    const uint8_t phys[] = {KSZ8863RLLI_PHY1_ADDR, KSZ8863RLLI_PHY2_ADDR};

    for (uint32_t i = 0; i < (sizeof(phys) / sizeof(phys[0])); ++i) {
        uint16_t id_high = 0;
        uint16_t id_low = 0;

        if (KSZ8863RLLI_ReadPhy(handle, phys[i], KSZ8863RLLI_PHY_ID_HIGH, &id_high) != KSZ8863RLLI_OK ||
            KSZ8863RLLI_ReadPhy(handle, phys[i], KSZ8863RLLI_PHY_ID_LOW, &id_low) != KSZ8863RLLI_OK) {
            return KSZ8863RLLI_UNSUPPORTED;
        }

        if (id_high != KSZ8863RLLI_PHY_ID_HIGH_VALUE ||
            (id_low & KSZ8863RLLI_PHY_ID_LOW_MASK) != KSZ8863RLLI_PHY_ID_LOW_VALUE) {
            return KSZ8863RLLI_ID_MISMATCH;
        }
    }

    return KSZ8863RLLI_OK;
}

KSZ8863RLLI_Status KSZ8863RLLI_StartSwitch(const KSZ8863RLLI_Handle *handle)
{
    uint8_t value = 0;

    if (KSZ8863RLLI_ReadReg(handle, KSZ8863RLLI_REG_CHIP_ID1, &value) != KSZ8863RLLI_OK) {
        return KSZ8863RLLI_UNSUPPORTED;
    }

    value |= KSZ8863RLLI_START_SWITCH;
    return KSZ8863RLLI_WriteReg(handle, KSZ8863RLLI_REG_CHIP_ID1, value);
}

KSZ8863RLLI_Status KSZ8863RLLI_RestartAutoNegotiation(const KSZ8863RLLI_Handle *handle, KSZ8863RLLI_Port port)
{
    uint8_t phy_addr;
    uint16_t value = 0;
    KSZ8863RLLI_Status status;

    status = KSZ8863RLLI_GetPhyAddress(port, &phy_addr);
    if (status != KSZ8863RLLI_OK) {
        return status;
    }

    if (KSZ8863RLLI_ReadPhy(handle, phy_addr, KSZ8863RLLI_PHY_BCR, &value) != KSZ8863RLLI_OK) {
        return KSZ8863RLLI_UNSUPPORTED;
    }

    value |= (KSZ8863RLLI_PHY_BCR_AUTONEG | KSZ8863RLLI_PHY_BCR_RESTART_AN);
    return KSZ8863RLLI_WritePhy(handle, phy_addr, KSZ8863RLLI_PHY_BCR, value);
}

KSZ8863RLLI_Status KSZ8863RLLI_SetPort3RmiiClock(const KSZ8863RLLI_Handle *handle, KSZ8863RLLI_RmiiClock clock)
{
    uint8_t value = 0;

    if (KSZ8863RLLI_ReadReg(handle, KSZ8863RLLI_REG_HOST_MODE, &value) != KSZ8863RLLI_OK) {
        return KSZ8863RLLI_UNSUPPORTED;
    }

    if (clock == KSZ8863RLLI_RMII_CLOCK_INTERNAL) {
        value |= KSZ8863RLLI_HOST_MODE_P3_RMII_INTERNAL_CLK;
    } else {
        value &= (uint8_t)~KSZ8863RLLI_HOST_MODE_P3_RMII_INTERNAL_CLK;
    }

    return KSZ8863RLLI_WriteReg(handle, KSZ8863RLLI_REG_HOST_MODE, value);
}

uint8_t KSZ8863RLLI_HasRegisterBus(const KSZ8863RLLI_Handle *handle)
{
    return (handle != NULL &&
            (handle->bus.mode == KSZ8863RLLI_BUS_SMI ||
             handle->bus.mode == KSZ8863RLLI_BUS_I2C ||
             handle->bus.mode == KSZ8863RLLI_BUS_SPI) &&
            handle->bus.read8 != NULL &&
            handle->bus.write8 != NULL) ? 1U : 0U;
}

uint8_t KSZ8863RLLI_HasMiimBus(const KSZ8863RLLI_Handle *handle)
{
    return (handle != NULL &&
            handle->bus.mode == KSZ8863RLLI_BUS_MIIM &&
            handle->bus.read_phy != NULL &&
            handle->bus.write_phy != NULL) ? 1U : 0U;
}

uint8_t KSZ8863RLLI_IsManaged(const KSZ8863RLLI_Handle *handle)
{
    return (KSZ8863RLLI_HasRegisterBus(handle) || KSZ8863RLLI_HasMiimBus(handle)) ? 1U : 0U;
}

uint8_t KSZ8863RLLI_IsPortLinkUp(const KSZ8863RLLI_Handle *handle, KSZ8863RLLI_Port port)
{
    uint8_t reg = 0;
    uint8_t value = 0;
    uint8_t phy_addr = 0;
    uint16_t phy_status = 0;

    if (!KSZ8863RLLI_IsManaged(handle)) {
        return 1U;
    }

    if (KSZ8863RLLI_HasMiimBus(handle)) {
        if (KSZ8863RLLI_GetPhyAddress(port, &phy_addr) != KSZ8863RLLI_OK) {
            return 1U;
        }

        if (KSZ8863RLLI_ReadPhy(handle, phy_addr, KSZ8863RLLI_PHY_BSR, &phy_status) != KSZ8863RLLI_OK ||
            KSZ8863RLLI_ReadPhy(handle, phy_addr, KSZ8863RLLI_PHY_BSR, &phy_status) != KSZ8863RLLI_OK) {
            return 0U;
        }

        return (phy_status & KSZ8863RLLI_PHY_BSR_LINK_UP) ? 1U : 0U;
    }

    if (port == KSZ8863RLLI_PORT1) {
        reg = KSZ8863RLLI_REG_PORT1_STATUS0;
    } else if (port == KSZ8863RLLI_PORT2) {
        reg = KSZ8863RLLI_REG_PORT2_STATUS0;
    } else {
        return 1U;
    }

    if (KSZ8863RLLI_ReadReg(handle, reg, &value) != KSZ8863RLLI_OK) {
        return 0U;
    }

    return (value & KSZ8863RLLI_PORT_STATUS_LINK_UP) ? 1U : 0U;
}

uint8_t KSZ8863RLLI_IsLinkUp(const KSZ8863RLLI_Handle *handle)
{
    if (!KSZ8863RLLI_IsManaged(handle)) {
        return 1U;
    }

    return (KSZ8863RLLI_IsPortLinkUp(handle, KSZ8863RLLI_PORT1) ||
            KSZ8863RLLI_IsPortLinkUp(handle, KSZ8863RLLI_PORT2)) ? 1U : 0U;
}

static KSZ8863RLLI_Status KSZ8863RLLI_GetPhyAddress(KSZ8863RLLI_Port port, uint8_t *phy_addr)
{
    if (phy_addr == NULL) {
        return KSZ8863RLLI_ERROR;
    }

    if (port == KSZ8863RLLI_PORT1) {
        *phy_addr = KSZ8863RLLI_PHY1_ADDR;
    } else if (port == KSZ8863RLLI_PORT2) {
        *phy_addr = KSZ8863RLLI_PHY2_ADDR;
    } else {
        return KSZ8863RLLI_UNSUPPORTED;
    }

    return KSZ8863RLLI_OK;
}
