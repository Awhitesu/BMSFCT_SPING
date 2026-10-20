/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
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
#include "main.h"
#include "i2c.h"
#include "lwip.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "resistor_control.h"
#include "modbus_tcp.h"
#include "network_config.h"
#include <stdio.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
static Resistor_Handle resistor_handle;
static Modbus_TCP_Server modbus_server;
extern struct netif gnetif;
static IWDG_HandleTypeDef hiwdg;

// LED控制变量
static uint32_t led_last_toggle = 0;
static uint8_t led_state = 0;
static uint8_t system_initialized = 0;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_IWDG_Init(void);
/* USER CODE BEGIN PFP */
static void Print_Reset_Cause(void);
void Print_System_Info(void);
void LED_Control_Handler(void);
/* USER CODE END PFP */

/* USER CODE BEGIN 0 */
// Printf redirection to UART1
int __io_putchar(int ch)
{
  HAL_UART_Transmit(&huart1, (uint8_t *)&ch, 1, HAL_MAX_DELAY);
  return ch;
}
/* USER CODE END 0 */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USART1_UART_Init();
  Print_Reset_Cause();
  MX_IWDG_Init();
  MX_LWIP_Init();
  /* USER CODE BEGIN 2 */
  
  // 延迟等待网络初始化完成
  HAL_Delay(1000);
  HAL_IWDG_Refresh(&hiwdg);
  
  // 等待网络链路建立
  uint32_t link_wait_start = HAL_GetTick();
  printf("等待网络链路建立...\r\n");
  while (!netif_is_link_up(&gnetif) && (HAL_GetTick() - link_wait_start < 5000)) {
    HAL_IWDG_Refresh(&hiwdg);
    MX_LWIP_Process();
    HAL_Delay(100);
  }
  
  if (netif_is_link_up(&gnetif)) {
    printf("网络链路已建立\r\n");
  } else {
    printf("警告: 网络链路未建立，但继续初始化\r\n");
  }
  
  // 初始化网络配置模块 (从Flash加载配置，失败则使用默认值并保存)
  printf("初始化网络配置模块...\r\n");
  NetworkConfig_Status net_status = NetworkConfig_Init();
  if (net_status == NETWORK_CONFIG_OK) {
    printf("网络配置从Flash加载成功\r\n");
  } else if (net_status == NETWORK_CONFIG_ERROR) {
    printf("警告: Flash无有效配置，已写入默认值\r\n");
  } else {
    printf("错误: 网络配置初始化失败，状态码=%d\r\n", net_status);
  }

  // 初始化RN-EOL电阻控制系统
  printf("初始化RN-EOL可编程电阻板卡控制系统...\r\n");
  Resistor_Status res_status = Resistor_Init(&resistor_handle);
  if (res_status == RESISTOR_OK) {
    printf("电阻控制系统初始化成功\r\n");
  } else {
    printf("错误: 电阻控制系统初始化失败，状态码=%d\r\n", res_status);
  }

  // 运行系统诊断

  // 初始化Modbus TCP服务器
  printf("初始化Modbus TCP服务器...\r\n");
  Modbus_Status status = Modbus_TCP_Init(&modbus_server, &resistor_handle);
  if (status == MODBUS_OK) {
    status = Modbus_TCP_Start(&modbus_server);
    if (status == MODBUS_OK) {
      printf("Modbus TCP服务器启动成功，监听端口502\r\n");
    } else {
      printf("错误: Modbus TCP服务器启动失败\r\n");
    }
  } else {
    printf("错误: Modbus TCP服务器初始化失败\r\n");
  }
  
  // 标记系统初始化完成（必须在打印和LED控制之前设置）
  system_initialized = 1;

  // 打印系统信息
  Print_System_Info();

  printf("系统初始化完成，LED控制已启用\r\n");
  printf("LED状态指示: ");
  if (netif_is_link_up(&gnetif)) {
    printf("正常 (500ms闪烁)\r\n");
  } else {
    printf("网络断开 (2s闪烁)\r\n");
  }

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  uint32_t last_status_print = 0;
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    // 处理LwIP网络协议栈
    MX_LWIP_Process();
    
    // 处理Modbus TCP服务器
    Modbus_TCP_Process(&modbus_server);

    // LED控制处理
    LED_Control_Handler();

    // 每30秒输出一次系统状态
    if (HAL_GetTick() - last_status_print > 30000) {
      last_status_print = HAL_GetTick();
      printf("系统运行中... 网络状态: %s, Modbus服务: %s\r\n",
             netif_is_link_up(&gnetif) ? "连接" : "断开",
             modbus_server.server_running ? "运行" : "停止");

      // 显示客户端连接状态
      int active_clients = 0;
      for (int i = 0; i < MODBUS_MAX_CLIENTS; i++) {
        if (modbus_server.clients[i].active) {
          active_clients++;
        }
      }
      printf("活跃Modbus客户端: %d/%d\r\n", active_clients, MODBUS_MAX_CLIENTS);
    }

    HAL_IWDG_Refresh(&hiwdg);

    // 小延迟以避免过度占用CPU (优化网络延迟：移除延迟)
    //HAL_Delay(1);
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 4;
  RCC_OscInitStruct.PLL.PLLN = 168;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 4;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK)
  {
    Error_Handler();
  }

  /** Enables the Clock Security System
  */
  HAL_RCC_EnableCSS();
}

/* USER CODE BEGIN 4 */

static void MX_IWDG_Init(void)
{
    __HAL_RCC_LSI_ENABLE();
    while (__HAL_RCC_GET_FLAG(RCC_FLAG_LSIRDY) == RESET) {
    }

    hiwdg.Instance = IWDG;
    hiwdg.Init.Prescaler = IWDG_PRESCALER_256;
    hiwdg.Init.Reload = 1250; // ~10s timeout @ 32 kHz LSI

    if (HAL_IWDG_Init(&hiwdg) != HAL_OK) {
        Error_Handler();
    }
}

/**
 * @brief 打印系统基本信息
 */
static void Print_Reset_Cause(void)
{
    printf("Reset cause:");

    if (__HAL_RCC_GET_FLAG(RCC_FLAG_IWDGRST) != RESET) {
        printf(" IWDG");
    }
    if (__HAL_RCC_GET_FLAG(RCC_FLAG_WWDGRST) != RESET) {
        printf(" WWDG");
    }
    if (__HAL_RCC_GET_FLAG(RCC_FLAG_SFTRST) != RESET) {
        printf(" SOFT");
    }
    if (__HAL_RCC_GET_FLAG(RCC_FLAG_PORRST) != RESET) {
        printf(" POR/PDR");
    }
    if (__HAL_RCC_GET_FLAG(RCC_FLAG_PINRST) != RESET) {
        printf(" PIN");
    }
    if (__HAL_RCC_GET_FLAG(RCC_FLAG_BORRST) != RESET) {
        printf(" BOR");
    }
    if (__HAL_RCC_GET_FLAG(RCC_FLAG_LPWRRST) != RESET) {
        printf(" LOW_POWER");
    }

    printf("\r\n");
    __HAL_RCC_CLEAR_RESET_FLAGS();
}

void Print_System_Info(void)
{
    // 使用新的系统信息打印函数
    Resistor_PrintSystemInfo(&resistor_handle);
}

/**
 * @brief LED控制处理函数
 * @note  根据系统状态控制LED闪烁频率：
 *        - 系统初始化完成且以太网链路建立：500ms闪烁
 *        - 其他情况：2s闪烁
 */
void LED_Control_Handler(void)
{
    uint32_t current_time = HAL_GetTick();
    uint32_t blink_interval;

    // 根据系统状态确定闪烁间隔
    if (system_initialized && netif_is_link_up(&gnetif)) {
        // 系统正常：500ms闪烁
        blink_interval = 500;
    } else {
        // 系统异常：2s闪烁
        blink_interval = 2000;
    }

    // 控制LED闪烁
    if (current_time - led_last_toggle >= blink_interval) {
        led_last_toggle = current_time;
        led_state = !led_state;

        // 控制LED
        if (led_state) {
            HAL_GPIO_WritePin(USER_LED_GPIO_Port, USER_LED_Pin, GPIO_PIN_SET);
        } else {
            HAL_GPIO_WritePin(USER_LED_GPIO_Port, USER_LED_Pin, GPIO_PIN_RESET);
        }
    }
}

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
