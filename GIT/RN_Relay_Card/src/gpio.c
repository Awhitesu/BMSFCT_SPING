/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file    gpio.c
  * @brief   GPIO configuration for RN Relay IO control card.
  ******************************************************************************
  */
/* USER CODE END Header */

#include "gpio.h"

void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();

  HAL_GPIO_WritePin(ETH_RESET_GPIO_Port, ETH_RESET_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(USER_LED_GPIO_Port, USER_LED_Pin, GPIO_PIN_RESET);

  HAL_GPIO_WritePin(GPIOD, RELAY_K1_Pin | RELAY_K2_Pin | RELAY_K3_Pin | RELAY_K4_Pin |
                           RELAY_K13_Pin | RELAY_K14_Pin | RELAY_K15_Pin | RELAY_K16_Pin,
                    GPIO_PIN_RESET);
  HAL_GPIO_WritePin(GPIOB, RELAY_K5_Pin | RELAY_K6_Pin | RELAY_K7_Pin | RELAY_K8_Pin |
                           RELAY_K17_Pin | RELAY_K18_Pin,
                    GPIO_PIN_RESET);
  HAL_GPIO_WritePin(GPIOA, RELAY_K9_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(GPIOC, RELAY_K10_Pin | RELAY_K11_Pin | RELAY_K12_Pin, GPIO_PIN_RESET);

  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;

  GPIO_InitStruct.Pin = USER_LED_Pin;
  HAL_GPIO_Init(USER_LED_GPIO_Port, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = RELAY_K1_Pin | RELAY_K2_Pin | RELAY_K3_Pin | RELAY_K4_Pin |
                        RELAY_K13_Pin | RELAY_K14_Pin | RELAY_K15_Pin | RELAY_K16_Pin;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = RELAY_K5_Pin | RELAY_K6_Pin | RELAY_K7_Pin | RELAY_K8_Pin |
                        RELAY_K17_Pin | RELAY_K18_Pin;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = RELAY_K9_Pin;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = RELAY_K10_Pin | RELAY_K11_Pin | RELAY_K12_Pin;
  HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = ETH_RESET_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(ETH_RESET_GPIO_Port, &GPIO_InitStruct);
}
