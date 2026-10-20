/* USER CODE BEGIN Header */
/**
 ******************************************************************************
  * File Name          : LWIP.c
  * Description        : This file provides initialization code for LWIP
  *                      middleWare.
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
#include "lwip.h"
#include "lwip/init.h"
#include "lwip/netif.h"
#if defined ( __CC_ARM )  /* MDK ARM Compiler */
#include "lwip/sio.h"
#endif /* MDK ARM Compiler */
#include "ethernetif.h"

/* USER CODE BEGIN 0 */
#include "network_config.h"
#include <string.h>
/* USER CODE END 0 */
/* Private function prototypes -----------------------------------------------*/
static void ethernet_link_status_updated(struct netif *netif);
static void Ethernet_Link_Periodic_Handle(struct netif *netif);
/* ETH Variables initialization ----------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN 1 */

/* USER CODE END 1 */
uint32_t EthernetLinkTimer;

/* Variables Initialization */
struct netif gnetif;
ip4_addr_t ipaddr;
ip4_addr_t netmask;
ip4_addr_t gw;
uint8_t IP_ADDRESS[4];
uint8_t NETMASK_ADDRESS[4];
uint8_t GATEWAY_ADDRESS[4];

/* USER CODE BEGIN 2 */

/* USER CODE END 2 */

/**
  * LwIP initialization function
  */
void MX_LWIP_Init(void)
{
/* USER CODE BEGIN IP_ADDRESSES */
  // 初始化网络配置模块并获取保存的配置
  NetworkConfig_Init();
  
  NetworkConfig_t config;
  if (NetworkConfig_GetCurrentConfig(&config) == NETWORK_CONFIG_OK) {
    // 使用保存的网络配置
    IP_ADDRESS[0] = config.ip_address[0];
    IP_ADDRESS[1] = config.ip_address[1];
    IP_ADDRESS[2] = config.ip_address[2];
    IP_ADDRESS[3] = config.ip_address[3];
    
    NETMASK_ADDRESS[0] = config.netmask[0];
    NETMASK_ADDRESS[1] = config.netmask[1];
    NETMASK_ADDRESS[2] = config.netmask[2];
    NETMASK_ADDRESS[3] = config.netmask[3];
    
    GATEWAY_ADDRESS[0] = config.gateway[0];
    GATEWAY_ADDRESS[1] = config.gateway[1];
    GATEWAY_ADDRESS[2] = config.gateway[2];
    GATEWAY_ADDRESS[3] = config.gateway[3];
  } else {
    // 使用默认配置作为备份
    IP_ADDRESS[0] = DEFAULT_IP_ADDR_0;
    IP_ADDRESS[1] = DEFAULT_IP_ADDR_1;
    IP_ADDRESS[2] = DEFAULT_IP_ADDR_2;
    IP_ADDRESS[3] = DEFAULT_IP_ADDR_3;
    
    NETMASK_ADDRESS[0] = DEFAULT_NETMASK_0;
    NETMASK_ADDRESS[1] = DEFAULT_NETMASK_1;
    NETMASK_ADDRESS[2] = DEFAULT_NETMASK_2;
    NETMASK_ADDRESS[3] = DEFAULT_NETMASK_3;
    
    GATEWAY_ADDRESS[0] = DEFAULT_GATEWAY_0;
    GATEWAY_ADDRESS[1] = DEFAULT_GATEWAY_1;
    GATEWAY_ADDRESS[2] = DEFAULT_GATEWAY_2;
    GATEWAY_ADDRESS[3] = DEFAULT_GATEWAY_3;
  }
/* USER CODE END IP_ADDRESSES */

  /* Initialize the LwIP stack without RTOS */
  lwip_init();

  /* IP addresses initialization without DHCP (IPv4) */
  IP4_ADDR(&ipaddr, IP_ADDRESS[0], IP_ADDRESS[1], IP_ADDRESS[2], IP_ADDRESS[3]);
  IP4_ADDR(&netmask, NETMASK_ADDRESS[0], NETMASK_ADDRESS[1] , NETMASK_ADDRESS[2], NETMASK_ADDRESS[3]);
  IP4_ADDR(&gw, GATEWAY_ADDRESS[0], GATEWAY_ADDRESS[1], GATEWAY_ADDRESS[2], GATEWAY_ADDRESS[3]);

  /* add the network interface (IPv4/IPv6) without RTOS */
  netif_add(&gnetif, &ipaddr, &netmask, &gw, NULL, &ethernetif_init, &ethernet_input);

  /* Registers the default network interface */
  netif_set_default(&gnetif);

  /* We must always bring the network interface up connection or not... */
  netif_set_up(&gnetif);

  /* Set the link callback function, this function is called on change of link status*/
  netif_set_link_callback(&gnetif, ethernet_link_status_updated);

/* USER CODE BEGIN 3 */

/**
 * @brief 更新网络配置 (需要重启才能生效)
 * @param config 新的网络配置 (指针类型)
 * @return NetworkConfig_Status
 * @note 由于LwIP的网络接口在初始化后不容易动态修改，建议重启系统应用新配置
 * @note config是NetworkConfig_t*类型指针，使用->访问成员是正确的
 */
__attribute__((unused))
NetworkConfig_Status LWIP_UpdateNetworkConfig(const NetworkConfig_t *config)
{
    if (config == NULL) {
        return NETWORK_CONFIG_INVALID_PARAM;
    }
    
    // 保存新配置到Flash
    NetworkConfig_Status status = NetworkConfig_Save(config);
    if (status != NETWORK_CONFIG_OK) {
        return status;
    }
    
    // 更新全局变量 (下次重启生效)
    IP_ADDRESS[0] = config->ip_address[0];
    IP_ADDRESS[1] = config->ip_address[1];
    IP_ADDRESS[2] = config->ip_address[2];
    IP_ADDRESS[3] = config->ip_address[3];
    
    NETMASK_ADDRESS[0] = config->netmask[0];
    NETMASK_ADDRESS[1] = config->netmask[1];
    NETMASK_ADDRESS[2] = config->netmask[2];
    NETMASK_ADDRESS[3] = config->netmask[3];
    
    GATEWAY_ADDRESS[0] = config->gateway[0];
    GATEWAY_ADDRESS[1] = config->gateway[1];
    GATEWAY_ADDRESS[2] = config->gateway[2];
    GATEWAY_ADDRESS[3] = config->gateway[3];
    
    return NETWORK_CONFIG_OK;
}

/**
 * @brief 获取当前网络配置
 * @param config 配置结构体指针 (NetworkConfig_t*类型)
 * @note config是NetworkConfig_t*类型指针，使用->访问成员是正确的
 */
__attribute__((unused))
void LWIP_GetCurrentNetworkConfig(NetworkConfig_t *config)
{
    if (config == NULL) {
        return;
    }
    
    config->magic = NETWORK_CONFIG_MAGIC;
    
    config->ip_address[0] = IP_ADDRESS[0];
    config->ip_address[1] = IP_ADDRESS[1];
    config->ip_address[2] = IP_ADDRESS[2];
    config->ip_address[3] = IP_ADDRESS[3];
    
    config->netmask[0] = NETMASK_ADDRESS[0];
    config->netmask[1] = NETMASK_ADDRESS[1];
    config->netmask[2] = NETMASK_ADDRESS[2];
    config->netmask[3] = NETMASK_ADDRESS[3];
    
    config->gateway[0] = GATEWAY_ADDRESS[0];
    config->gateway[1] = GATEWAY_ADDRESS[1];
    config->gateway[2] = GATEWAY_ADDRESS[2];
    config->gateway[3] = GATEWAY_ADDRESS[3];
    
    config->dhcp_enable = 0; // 当前固定IP模式
    memset(config->reserved, 0, sizeof(config->reserved));
    
    config->checksum = NetworkConfig_CalculateChecksum(config);
}

/* USER CODE END 3 */
}

#ifdef USE_OBSOLETE_USER_CODE_SECTION_4
/* Kept to help code migration. (See new 4_1, 4_2... sections) */
/* Avoid to use this user section which will become obsolete. */
/* USER CODE BEGIN 4 */
/* USER CODE END 4 */
#endif

/**
  * @brief  Ethernet Link periodic check
  * @param  netif
  * @retval None
  */
static void Ethernet_Link_Periodic_Handle(struct netif *netif)
{
/* USER CODE BEGIN 4_4_1 */
/* USER CODE END 4_4_1 */

  /* Ethernet Link every 100ms */
  if (HAL_GetTick() - EthernetLinkTimer >= 100)
  {
    EthernetLinkTimer = HAL_GetTick();
    ethernet_link_check_state(netif);
  }
/* USER CODE BEGIN 4_4 */
/* USER CODE END 4_4 */
}

/**
 * ----------------------------------------------------------------------
 * Function given to help user to continue LwIP Initialization
 * Up to user to complete or change this function ...
 * Up to user to call this function in main.c in while (1) of main(void)
 *-----------------------------------------------------------------------
 * Read a received packet from the Ethernet buffers
 * Send it to the lwIP stack for handling
 * Handle timeouts if LWIP_TIMERS is set and without RTOS
 * Handle the llink status if LWIP_NETIF_LINK_CALLBACK is set and without RTOS
 */
void MX_LWIP_Process(void)
{
/* USER CODE BEGIN 4_1 */
/* USER CODE END 4_1 */
  Ethernet_Link_Periodic_Handle(&gnetif);

  if (ethernetif_is_started())
  {
    ethernetif_input(&gnetif);
    ethernetif_tx_release();
  }

/* USER CODE BEGIN 4_2 */
/* USER CODE END 4_2 */
  /* Handle timeouts */
  sys_check_timeouts();

/* USER CODE BEGIN 4_3 */
/* USER CODE END 4_3 */
}

/**
  * @brief  Notify the User about the network interface config status
  * @param  netif: the network interface
  * @retval None
  */
static void ethernet_link_status_updated(struct netif *netif)
{
  if (netif_is_up(netif))
  {
/* USER CODE BEGIN 5 */
/* USER CODE END 5 */
  }
  else /* netif is down */
  {
/* USER CODE BEGIN 6 */
/* USER CODE END 6 */
  }
}

#if defined ( __CC_ARM )  /* MDK ARM Compiler */
/**
 * Opens a serial device for communication.
 *
 * @param devnum device number
 * @return handle to serial device if successful, NULL otherwise
 */
sio_fd_t sio_open(u8_t devnum)
{
  sio_fd_t sd;

/* USER CODE BEGIN 7 */
  sd = 0; // dummy code
/* USER CODE END 7 */

  return sd;
}

/**
 * Sends a single character to the serial device.
 *
 * @param c character to send
 * @param fd serial device handle
 *
 * @note This function will block until the character can be sent.
 */
void sio_send(u8_t c, sio_fd_t fd)
{
/* USER CODE BEGIN 8 */
/* USER CODE END 8 */
}

/**
 * Reads from the serial device.
 *
 * @param fd serial device handle
 * @param data pointer to data buffer for receiving
 * @param len maximum length (in bytes) of data to receive
 * @return number of bytes actually received - may be 0 if aborted by sio_read_abort
 *
 * @note This function will block until data can be received. The blocking
 * can be cancelled by calling sio_read_abort().
 */
u32_t sio_read(sio_fd_t fd, u8_t *data, u32_t len)
{
  u32_t recved_bytes;

/* USER CODE BEGIN 9 */
  recved_bytes = 0; // dummy code
/* USER CODE END 9 */
  return recved_bytes;
}

/**
 * Tries to read from the serial device. Same as sio_read but returns
 * immediately if no data is available and never blocks.
 *
 * @param fd serial device handle
 * @param data pointer to data buffer for receiving
 * @param len maximum length (in bytes) of data to receive
 * @return number of bytes actually received
 */
u32_t sio_tryread(sio_fd_t fd, u8_t *data, u32_t len)
{
  u32_t recved_bytes;

/* USER CODE BEGIN 10 */
  recved_bytes = 0; // dummy code
/* USER CODE END 10 */
  return recved_bytes;
}
#endif /* MDK ARM Compiler */

