  #include "resistor_calculator.h"
#include <stdio.h>

// 电阻权重定义（根据硬件设计文档）
static const uint32_t LOW_RES_WEIGHTS[16] = {
    10,     // IO0: 10Ω
    20,     // IO1: 20Ω  
    40,     // IO2: 40Ω
    80,     // IO3: 80Ω
    100,    // IO4: 100Ω
    200,    // IO5: 200Ω
    400,    // IO6: 400Ω
    800,    // IO7: 800Ω
    80000,  // IO8: 80KΩ (实际硬件)
    40000,  // IO9: 40KΩ (实际硬件)
    20000,  // IO10: 20KΩ (实际硬件)
    10000,  // IO11: 10KΩ (实际硬件)
    8000,   // IO12: 8KΩ (实际硬件)
    4000,   // IO13: 4KΩ (实际硬件)
    2000,   // IO14: 2KΩ (实际硬件)
    1000    // IO15: 1KΩ (实际硬件)
};

static const uint32_t HIGH_RES_WEIGHTS[4] = {
    100000, // 100KΩ
    200000, // 200KΩ
    400000, // 400KΩ
    800000  // 800KΩ
};

// 计算低阻值组的总阻值（默认全部接入）
static uint32_t calculate_low_res_total(void)
{
    uint32_t total = 0;
    for (int i = 0; i < 16; i++) {
        total += LOW_RES_WEIGHTS[i];
    }
    return total; // 返回 166650Ω (166.65KΩ)
}

// 计算高阻值组的总阻值（默认全部接入）
static uint32_t calculate_high_res_total(void)
{
    uint32_t total = 0;
    for (int i = 0; i < 4; i++) {
        total += HIGH_RES_WEIGHTS[i];
    }
    return total; // 返回 1500000Ω (1.5MΩ)
}

ResistorCalc_Status ResistorCalc_CalculateControlMasks(uint32_t target_resistance,
                                                      uint16_t *low_mask,
                                                      uint8_t *high_mask)
{
    if (low_mask == NULL || high_mask == NULL) {
        return RESISTOR_CALC_ERROR_NULL_POINTER;
    }

    if (target_resistance < RESISTOR_CALC_MIN_VALUE || target_resistance > RESISTOR_CALC_MAX_VALUE) {
        return RESISTOR_CALC_ERROR_OUT_OF_RANGE;
    }

    // 计算总的可用阻值（所有电阻串联）
    uint32_t total_low = calculate_low_res_total();    // 166.65KΩ
    uint32_t total_high = calculate_high_res_total();  // 1.5MΩ
    uint32_t total_resistance = total_low + total_high; // 1.66665MΩ

    // 需要短接的阻值 = 总阻值 - 目标阻值
    uint32_t resistance_to_bypass = total_resistance - target_resistance;

    // 初始化掩码
    // 低阻值组：全1表示不短接任何电阻（IO置0时短接）
    // 高阻值组：全0表示不短接任何电阻（IO置1时短接）
    *low_mask = 0xFFFF;
    *high_mask = 0x00;

    uint32_t remaining_bypass = resistance_to_bypass;

    // 优先选择高阻值组进行短接（从大到小）
    // 高阻值组：IO置1时短接
    for (int i = 3; i >= 0; i--) {
        if (remaining_bypass >= HIGH_RES_WEIGHTS[i]) {
            *high_mask |= (1 << i);
            remaining_bypass -= HIGH_RES_WEIGHTS[i];
        }
    }

    // 然后选择低阻值组进行短接（必须从大到小以保证贪心算法正确）
    // 低阻值组：IO置0时短接，所以清除对应位
    const int low_res_indices_desc[16] = {8, 9, 10, 11, 12, 13, 14, 15, 7, 6, 5, 4, 3, 2, 1, 0};

    for (int j = 0; j < 16; j++) {
        int i = low_res_indices_desc[j];
        if (remaining_bypass >= LOW_RES_WEIGHTS[i]) {
            *low_mask &= ~(1 << i);  // 清除位表示短接
            remaining_bypass -= LOW_RES_WEIGHTS[i];
        }
    }

    // 检查是否能精确匹配
    if (remaining_bypass > 0) {
        return RESISTOR_CALC_ERROR_NO_EXACT_MATCH;
    }

    return RESISTOR_CALC_OK;
}

ResistorCalc_Status ResistorCalc_CalculateActualResistance(uint16_t low_mask,
                                                          uint8_t high_mask,
                                                          uint32_t *actual_resistance)
{
    if (actual_resistance == NULL) {
        return RESISTOR_CALC_ERROR_NULL_POINTER;
    }

    // 计算总阻值（所有电阻串联）
    uint32_t total_low = calculate_low_res_total();
    uint32_t total_high = calculate_high_res_total();
    uint32_t total_resistance = total_low + total_high;

    // 计算被短接的阻值
    uint32_t bypassed_resistance = 0;

    // 计算被短接的低阻值
    // 低阻值组：IO置0时短接，即位为0时短接
    for (int i = 0; i < 16; i++) {
        if (!(low_mask & (1 << i))) {  // 位为0时短接
            bypassed_resistance += LOW_RES_WEIGHTS[i];
        }
    }

    // 计算被短接的高阻值
    // 高阻值组：IO置1时短接，即位为1时短接
    for (int i = 0; i < 4; i++) {
        if (high_mask & (1 << i)) {  // 位为1时短接
            bypassed_resistance += HIGH_RES_WEIGHTS[i];
        }
    }

    // 实际阻值 = 总阻值 - 被短接的阻值
    *actual_resistance = total_resistance - bypassed_resistance;

    return RESISTOR_CALC_OK;
}

ResistorCalc_Status ResistorCalc_ValidateTargetResistance(uint32_t target_resistance)
{
    if (target_resistance < RESISTOR_CALC_MIN_VALUE) {
        return RESISTOR_CALC_ERROR_TOO_SMALL;
    }
    
    if (target_resistance > RESISTOR_CALC_MAX_VALUE) {
        return RESISTOR_CALC_ERROR_TOO_LARGE;
    }
    
    // 检查目标阻值是否可以通过现有电阻组合实现
    uint16_t low_mask;
    uint8_t high_mask;
    ResistorCalc_Status status = ResistorCalc_CalculateControlMasks(target_resistance, &low_mask, &high_mask);
    
    return status;
}

void ResistorCalc_PrintDebugInfo(uint32_t target_resistance, uint16_t low_mask, uint8_t high_mask)
{
    printf("=== 电阻计算调试信息 ===\n");
    printf("目标阻值: %lu Ω\n", (unsigned long)target_resistance);
    printf("低阻值组控制掩码: 0x%04X\n", low_mask);
    printf("高阻值组控制掩码: 0x%02X\n", high_mask);
    
    // 显示被短接的电阻
    printf("被短接的低阻值电阻 (IO置0时短接):\n");
    for (int i = 0; i < 16; i++) {
        if (!(low_mask & (1 << i))) {  // 位为0时短接
            printf("  IO%d: %lu Ω\n", i, (unsigned long)LOW_RES_WEIGHTS[i]);
        }
    }

    printf("被短接的高阻值电阻 (IO置1时短接):\n");
    for (int i = 0; i < 4; i++) {
        if (high_mask & (1 << i)) {  // 位为1时短接
            printf("  高阻值%d: %lu Ω\n", i, (unsigned long)HIGH_RES_WEIGHTS[i]);
        }
    }
    
    // 计算并显示实际阻值
    uint32_t actual_resistance;
    if (ResistorCalc_CalculateActualResistance(low_mask, high_mask, &actual_resistance) == RESISTOR_CALC_OK) {
        printf("实际阻值: %lu Ω\n", (unsigned long)actual_resistance);
        printf("误差: %ld Ω\n", (long)actual_resistance - (long)target_resistance);
    }
    
    printf("========================\n");
}