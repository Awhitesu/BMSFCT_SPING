/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file    gpio.c
  * @brief   This file provides code for the configuration
  *          of all used GPIO pins.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2025 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "gpio.h"

/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/*----------------------------------------------------------------------------*/
/* Configure GPIO                                                             */
/*----------------------------------------------------------------------------*/
/* USER CODE BEGIN 1 */

/* USER CODE END 1 */

/** Configure pins as
        * Analog
        * Input
        * Output
        * EVENT_OUT
        * EXTI
*/
void MX_GPIO_Init(void)
{

  GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOF_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOG_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(ETH_RESET_GPIO_Port, ETH_RESET_Pin, GPIO_PIN_RESET);

  /*Configure USER_LED Pin Output Level */
  HAL_GPIO_WritePin(USER_LED_GPIO_Port, USER_LED_Pin, GPIO_PIN_RESET);

  /*Configure output isolation relay K19 default ON.
    K19 uses NC contact: coil ON opens output path and avoids default 0Ω output. */
  HAL_GPIO_WritePin(RELAY_K19_GPIO_Port, RELAY_K19_Pin, GPIO_PIN_SET);

  /*Configure resistance relay pins output level (K1-K18关闭/旁路) */
  // GPIOE继电器 (K1-K6)
  HAL_GPIO_WritePin(GPIOE, RELAY_K1_Pin | RELAY_K2_Pin | RELAY_K3_Pin | RELAY_K4_Pin |
                           RELAY_K5_Pin | RELAY_K6_Pin, GPIO_PIN_RESET);

  // GPIOB继电器 (K7-K10)
  HAL_GPIO_WritePin(GPIOB, RELAY_K7_Pin | RELAY_K8_Pin | RELAY_K9_Pin | RELAY_K10_Pin, GPIO_PIN_RESET);

  // GPIOD继电器 (K11-K18)
  HAL_GPIO_WritePin(GPIOD, RELAY_K11_Pin | RELAY_K12_Pin | RELAY_K13_Pin | RELAY_K14_Pin |
                           RELAY_K15_Pin | RELAY_K16_Pin | RELAY_K17_Pin | RELAY_K18_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pins : PF1 PF2 PF3 PF4
                           PF5 */
  GPIO_InitStruct.Pin = GPIO_PIN_1|GPIO_PIN_2|GPIO_PIN_3|GPIO_PIN_4
                          |GPIO_PIN_5;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOF, &GPIO_InitStruct);

  /*Configure GPIO pins : PD9 PD10 PD11 PD12
                           PD13 */
  GPIO_InitStruct.Pin = GPIO_PIN_9|GPIO_PIN_10|GPIO_PIN_11|GPIO_PIN_12
                          |GPIO_PIN_13;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);

  /*Configure KSZ8863RLLI PHY reset pin : PA3 */
  GPIO_InitStruct.Pin = ETH_RESET_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(ETH_RESET_GPIO_Port, &GPIO_InitStruct);

  /*Configure USER_LED_Pin (PC11) */
  GPIO_InitStruct.Pin = USER_LED_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(USER_LED_GPIO_Port, &GPIO_InitStruct);

  /*Configure output isolation relay K19 (PC12) */
  GPIO_InitStruct.Pin = RELAY_K19_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(RELAY_K19_GPIO_Port, &GPIO_InitStruct);

  /*Configure Relay Pins - GPIOE (K1-K6) */
  GPIO_InitStruct.Pin = RELAY_K1_Pin | RELAY_K2_Pin | RELAY_K3_Pin | RELAY_K4_Pin |
                        RELAY_K5_Pin | RELAY_K6_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOE, &GPIO_InitStruct);

  /*Configure Relay Pins - GPIOB (K7-K10) */
  GPIO_InitStruct.Pin = RELAY_K7_Pin | RELAY_K8_Pin | RELAY_K9_Pin | RELAY_K10_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /*Configure Relay Pins - GPIOD (K11-K18) */
  GPIO_InitStruct.Pin = RELAY_K11_Pin | RELAY_K12_Pin | RELAY_K13_Pin | RELAY_K14_Pin |
                        RELAY_K15_Pin | RELAY_K16_Pin | RELAY_K17_Pin | RELAY_K18_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);

}

/* USER CODE BEGIN 2 */

/* USER CODE END 2 */
