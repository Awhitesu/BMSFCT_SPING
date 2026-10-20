/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
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

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32f4xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define ETH_RESET_Pin GPIO_PIN_3
#define ETH_RESET_GPIO_Port GPIOA

/* USER CODE BEGIN Private defines */

// Relay IO control pins from doc/RN_Relay_Card_IO.md.
#define RELAY_K1_Pin GPIO_PIN_4
#define RELAY_K1_GPIO_Port GPIOD
#define RELAY_K2_Pin GPIO_PIN_5
#define RELAY_K2_GPIO_Port GPIOD
#define RELAY_K3_Pin GPIO_PIN_6
#define RELAY_K3_GPIO_Port GPIOD
#define RELAY_K4_Pin GPIO_PIN_7
#define RELAY_K4_GPIO_Port GPIOD
#define RELAY_K5_Pin GPIO_PIN_3
#define RELAY_K5_GPIO_Port GPIOB
#define RELAY_K6_Pin GPIO_PIN_4
#define RELAY_K6_GPIO_Port GPIOB
#define RELAY_K7_Pin GPIO_PIN_5
#define RELAY_K7_GPIO_Port GPIOB
#define RELAY_K8_Pin GPIO_PIN_6
#define RELAY_K8_GPIO_Port GPIOB
#define RELAY_K9_Pin GPIO_PIN_15
#define RELAY_K9_GPIO_Port GPIOA
#define RELAY_K10_Pin GPIO_PIN_10
#define RELAY_K10_GPIO_Port GPIOC
#define RELAY_K11_Pin GPIO_PIN_11
#define RELAY_K11_GPIO_Port GPIOC
#define RELAY_K12_Pin GPIO_PIN_12
#define RELAY_K12_GPIO_Port GPIOC
#define RELAY_K13_Pin GPIO_PIN_0
#define RELAY_K13_GPIO_Port GPIOD
#define RELAY_K14_Pin GPIO_PIN_1
#define RELAY_K14_GPIO_Port GPIOD
#define RELAY_K15_Pin GPIO_PIN_2
#define RELAY_K15_GPIO_Port GPIOD
#define RELAY_K16_Pin GPIO_PIN_3
#define RELAY_K16_GPIO_Port GPIOD
#define RELAY_K17_Pin GPIO_PIN_7
#define RELAY_K17_GPIO_Port GPIOB
#define RELAY_K18_Pin GPIO_PIN_8
#define RELAY_K18_GPIO_Port GPIOB

#define USER_LED_Pin GPIO_PIN_2
#define USER_LED_GPIO_Port GPIOE

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
