#ifndef RESISTOR_CALCULATOR_H
#define RESISTOR_CALCULATOR_H

#include <stdint.h>

// 电阻计算范围定义
#define RESISTOR_CALC_MIN_VALUE     0         // 最小0Ω（所有电阻都被短接）
#define RESISTOR_CALC_MAX_VALUE     1666650   // 最大1.66665MΩ（所有电阻都串联）

// 电阻计算状态枚举
typedef enum {
    RESISTOR_CALC_OK = 0,
    RESISTOR_CALC_ERROR_NULL_POINTER,
    RESISTOR_CALC_ERROR_OUT_OF_RANGE,
    RESISTOR_CALC_ERROR_TOO_SMALL,
    RESISTOR_CALC_ERROR_TOO_LARGE,
    RESISTOR_CALC_ERROR_NO_EXACT_MATCH
} ResistorCalc_Status;

/**
 * @brief 根据目标阻值计算控制掩码
 * @param target_resistance 目标电阻值(Ω)
 * @param low_mask 输出低阻值组控制掩码(16位)，位置0表示短接对应电阻
 * @param high_mask 输出高阻值组控制掩码(4位)，位置1表示短接对应电阻
 * @return 计算状态
 *
 * @note 硬件逻辑说明：
 *       - 所有电阻默认都串联在电路中
 *       - 低阻值组：IO置0时短接（旁路）对应的电阻
 *       - 高阻值组：IO置0时短接（旁路）对应的电阻
 *       - 最终阻值 = 总阻值 - 被短接的阻值
 */
ResistorCalc_Status ResistorCalc_CalculateControlMasks(uint32_t target_resistance, 
                                                      uint16_t *low_mask, 
                                                      uint8_t *high_mask);

/**
 * @brief 根据控制掩码计算实际阻值
 * @param low_mask 低阻值组控制掩码
 * @param high_mask 高阻值组控制掩码  
 * @param actual_resistance 输出实际电阻值(Ω)
 * @return 计算状态
 */
ResistorCalc_Status ResistorCalc_CalculateActualResistance(uint16_t low_mask, 
                                                          uint8_t high_mask, 
                                                          uint32_t *actual_resistance);

/**
 * @brief 验证目标阻值是否可以实现
 * @param target_resistance 目标电阻值(Ω)
 * @return 验证结果
 */
ResistorCalc_Status ResistorCalc_ValidateTargetResistance(uint32_t target_resistance);

/**
 * @brief 打印电阻计算调试信息
 * @param target_resistance 目标阻值
 * @param low_mask 低阻值组掩码
 * @param high_mask 高阻值组掩码
 */
void ResistorCalc_PrintDebugInfo(uint32_t target_resistance, uint16_t low_mask, uint8_t high_mask);

#endif // RESISTOR_CALCULATOR_H