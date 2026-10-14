#include "main.h"
#include "lwip.h"
#include "usart.h"
#include "gpio.h"
#include "app_config.h"
#include "relay_control.h"
#include "modbus_tcp.h"
#include "network_config.h"
#include <stdio.h>

static Relay_Handle relay_handle;
static Modbus_TCP_Server modbus_server;
extern struct netif gnetif;

void SystemClock_Config(void);
static void LED_Process(void);

int __io_putchar(int ch)
{
  HAL_UART_Transmit(&huart1, (uint8_t *)&ch, 1, HAL_MAX_DELAY);
  return ch;
}

int main(void)
{
  HAL_Init();
  SystemClock_Config();

  MX_GPIO_Init();
  MX_USART1_UART_Init();

  if (NetworkConfig_Init() != NETWORK_CONFIG_OK) {
    Error_Handler();
  }

  if (Relay_Init(&relay_handle) != RELAY_OK) {
    Error_Handler();
  }

  MX_LWIP_Init();

  if (Modbus_TCP_Init(&modbus_server, &relay_handle) != MODBUS_OK ||
      Modbus_TCP_Start(&modbus_server) != MODBUS_OK) {
    Error_Handler();
  }

  printf("%s ready. Device ID: RC. Modbus TCP coils 0-%u map to K1-K%u.\r\n",
         RN_BOARD_NAME,
         (unsigned int)(Relay_GetChannelCount() - 1U),
         (unsigned int)Relay_GetChannelCount());

  while (1) {
    MX_LWIP_Process();
    Modbus_TCP_Process(&modbus_server);
    LED_Process();
  }
}

void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 4;
  RCC_OscInitStruct.PLL.PLLN = 168;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 4;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) {
    Error_Handler();
  }

  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                                RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK) {
    Error_Handler();
  }

  HAL_RCC_EnableCSS();
}

static void LED_Process(void)
{
  static uint32_t last_toggle = 0;
  uint32_t interval = netif_is_link_up(&gnetif) ? 500U : 1500U;

  if (HAL_GetTick() - last_toggle >= interval) {
    last_toggle = HAL_GetTick();
    HAL_GPIO_TogglePin(USER_LED_GPIO_Port, USER_LED_Pin);
  }
}

void Error_Handler(void)
{
  __disable_irq();
  while (1) {
    HAL_GPIO_TogglePin(USER_LED_GPIO_Port, USER_LED_Pin);
    HAL_Delay(100);
  }
}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
  (void)file;
  (void)line;
}
#endif
