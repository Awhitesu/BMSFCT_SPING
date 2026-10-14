/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * File Name          : Target/lwipopts.h
  * Description        : This file overrides LwIP stack default configuration
  *                      done in opt.h file.
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

/* Define to prevent recursive inclusion --------------------------------------*/
#ifndef __LWIPOPTS__H__
#define __LWIPOPTS__H__

#include "main.h"

/*-----------------------------------------------------------------------------*/
/* Current version of LwIP supported by CubeMx: 2.1.2 -*/
/*-----------------------------------------------------------------------------*/

/* Within 'USER CODE' section, code will be kept by default at each generation */
/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

#ifdef __cplusplus
 extern "C" {
#endif

/* STM32CubeMX Specific Parameters (not defined in opt.h) ---------------------*/
/* Parameters set in STM32CubeMX LwIP Configuration GUI -*/
/*----- WITH_RTOS disabled (Since FREERTOS is not set) -----*/
#define WITH_RTOS 0
/*----- CHECKSUM_BY_HARDWARE enabled -----*/
#define CHECKSUM_BY_HARDWARE 1
/*-----------------------------------------------------------------------------*/

/* LwIP Stack Parameters (modified compared to initialization value in opt.h) -*/
/* Parameters set in STM32CubeMX LwIP Configuration GUI -*/
/*----- Value in opt.h for NO_SYS: 0 -----*/
#define NO_SYS 1
/*----- Value in opt.h for SYS_LIGHTWEIGHT_PROT: 1 -----*/
#define SYS_LIGHTWEIGHT_PROT 0
/*----- Value in opt.h for MEM_ALIGNMENT: 1 -----*/
#define MEM_ALIGNMENT 4
/*----- Value in opt.h for LWIP_ETHERNET: LWIP_ARP || PPPOE_SUPPORT -*/
#define LWIP_ETHERNET 1
/*----- Value in opt.h for LWIP_DNS_SECURE: (LWIP_DNS_SECURE_RAND_XID | LWIP_DNS_SECURE_NO_MULTIPLE_OUTSTANDING | LWIP_DNS_SECURE_RAND_SRC_PORT) -*/
#define LWIP_DNS_SECURE 7
/*----- Value in opt.h for TCP_SND_QUEUELEN: (4*TCP_SND_BUF + (TCP_MSS - 1))/TCP_MSS -----*/
/* 注释掉原始定义,在USER CODE中重新定义优化值 */
/* #define TCP_SND_QUEUELEN 9 */
/*----- Value in opt.h for TCP_SNDLOWAT: LWIP_MIN(LWIP_MAX(((TCP_SND_BUF)/2), (2 * TCP_MSS) + 1), (TCP_SND_BUF) - 1) -*/
#define TCP_SNDLOWAT 1071
/*----- Value in opt.h for TCP_SNDQUEUELOWAT: LWIP_MAX(TCP_SND_QUEUELEN)/2, 5) -*/
#define TCP_SNDQUEUELOWAT 5
/*----- Value in opt.h for TCP_WND_UPDATE_THRESHOLD: LWIP_MIN(TCP_WND/4, TCP_MSS*4) -----*/
#define TCP_WND_UPDATE_THRESHOLD 536
/*----- Value in opt.h for LWIP_NETIF_LINK_CALLBACK: 0 -----*/
#define LWIP_NETIF_LINK_CALLBACK 1
/*----- Value in opt.h for LWIP_NETCONN: 1 -----*/
#define LWIP_NETCONN 0
/*----- Value in opt.h for LWIP_SOCKET: 1 -----*/
#define LWIP_SOCKET 0
/*----- Value in opt.h for RECV_BUFSIZE_DEFAULT: INT_MAX -----*/
#define RECV_BUFSIZE_DEFAULT 2000000000
/*----- Value in opt.h for LWIP_STATS: 1 -----*/
#define LWIP_STATS 0
/*----- Value in opt.h for CHECKSUM_GEN_IP: 1 -----*/
#define CHECKSUM_GEN_IP 0
/*----- Value in opt.h for CHECKSUM_GEN_UDP: 1 -----*/
#define CHECKSUM_GEN_UDP 0
/*----- Value in opt.h for CHECKSUM_GEN_TCP: 1 -----*/
#define CHECKSUM_GEN_TCP 0
/*----- Value in opt.h for CHECKSUM_GEN_ICMP: 1 -----*/
#define CHECKSUM_GEN_ICMP 0
/*----- Value in opt.h for CHECKSUM_GEN_ICMP6: 1 -----*/
#define CHECKSUM_GEN_ICMP6 0
/*----- Value in opt.h for CHECKSUM_CHECK_IP: 1 -----*/
#define CHECKSUM_CHECK_IP 0
/*----- Value in opt.h for CHECKSUM_CHECK_UDP: 1 -----*/
#define CHECKSUM_CHECK_UDP 0
/*----- Value in opt.h for CHECKSUM_CHECK_TCP: 1 -----*/
#define CHECKSUM_CHECK_TCP 0
/*----- Value in opt.h for CHECKSUM_CHECK_ICMP: 1 -----*/
#define CHECKSUM_CHECK_ICMP 0
/*----- Value in opt.h for CHECKSUM_CHECK_ICMP6: 1 -----*/
#define CHECKSUM_CHECK_ICMP6 0
/*-----------------------------------------------------------------------------*/
/* USER CODE BEGIN 1 */

/* 确保ICMP协议启用 - ping功能需要 */
#define LWIP_ICMP 1

/* 关闭调试选项以提高性能 */
#define LWIP_DEBUG 0
#define NETIF_DEBUG LWIP_DBG_OFF
#define ETHARP_DEBUG LWIP_DBG_OFF
#define ICMP_DEBUG LWIP_DBG_OFF
#define IP_DEBUG LWIP_DBG_OFF
#define PBUF_DEBUG LWIP_DBG_OFF

/* 生产环境关闭统计功能以节省CPU和内存 */
#define LWIP_STATS_DISPLAY 0

/* ========================================================================== */
/* TCP性能优化配置 - 针对Modbus TCP优化响应速度 */
/* ========================================================================== */

/* TCP Maximum Segment Size - 以太网最大MSS (1500-40=1460) */
#define TCP_MSS                         1460

/* TCP接收窗口大小 - 增大以提高吞吐量 */
#define TCP_WND                         (8 * TCP_MSS)

/* TCP发送缓冲区大小 - 增大以支持更大的响应数据 */  
#define TCP_SND_BUF                     (4 * TCP_MSS)

/* TCP发送队列长度 - 增加以支持更多并发发送 */
#define TCP_SND_QUEUELEN                16

/* TCP发送队列低水位 - 自动计算 */
#undef TCP_SNDQUEUELOWAT
#define TCP_SNDQUEUELOWAT               LWIP_MAX((TCP_SND_QUEUELEN)/2, 5)

/* TCP发送低水位 - 重新计算基于新的SND_BUF */
#undef TCP_SNDLOWAT  
#define TCP_SNDLOWAT                    LWIP_MIN(LWIP_MAX(((TCP_SND_BUF)/2), (2 * TCP_MSS) + 1), (TCP_SND_BUF) - 1)

/* TCP窗口更新阈值 - 重新计算 */
#undef TCP_WND_UPDATE_THRESHOLD
#define TCP_WND_UPDATE_THRESHOLD        LWIP_MIN((TCP_WND / 4), (TCP_MSS * 4))

/* TCP快速重传 - 启用以快速恢复丢包 */
#define LWIP_TCP_TIMESTAMPS             0
#define TCP_QUEUE_OOSEQ                 1

/* TCP Nagle算法优化 - 对小包场景优化 */
#define TCP_OVERSIZE                    TCP_MSS

/* ========================================================================== */
/* 内存池优化配置 */
/* ========================================================================== */

/* PBUF池大小 - 优化内存使用，适配4个Modbus客户端 */
#define PBUF_POOL_SIZE                  24

/* PBUF池缓冲区大小 - 自动计算基于新MSS */
#undef PBUF_POOL_BUFSIZE
#define PBUF_POOL_BUFSIZE               LWIP_MEM_ALIGN_SIZE(TCP_MSS+40+PBUF_LINK_ENCAPSULATION_HLEN+PBUF_LINK_HLEN)

/* TCP段数量 - 增加以支持更多TCP段 */
#define MEMP_NUM_TCP_SEG                32

/* TCP PCB数量 - 增加以支持更多连接 */
#define MEMP_NUM_TCP_PCB                8
#define MEMP_NUM_TCP_PCB_LISTEN         4

/* ========================================================================== */
/* 以太网和硬件优化配置 */  
/* ========================================================================== */

/* 启用以太网统计 */
#define LWIP_STATS_LARGE                1

/* 优化内存拷贝 - 使用STM32优化版本 */
#define LWIP_CHKSUM_ALGORITHM           3

/* ARP表大小优化 */
#define ARP_TABLE_SIZE                  10
#define ARP_QUEUEING                    1

/* IP分片支持(通常Modbus TCP不需要,但保留以防万一) */
#define IP_FRAG                         1
#define IP_REASSEMBLY                   1

/* ========================================================================== */
/* Modbus TCP专用优化 */
/* ========================================================================== */

/* TCP保持活跃 - 用于检测断开的连接 */
#define LWIP_TCP_KEEPALIVE              1

/* TCP连接超时优化 */
#define TCP_MAXRTX                      6    // 减少重传次数以快速检测故障
#define TCP_SYNMAXRTX                   3    // 减少SYN重传次数

/* 内存对齐优化 - STM32F4使用32位对齐 */
#undef MEM_ALIGNMENT
#define MEM_ALIGNMENT                   4

/* USER CODE END 1 */

#ifdef __cplusplus
}
#endif
#endif /*__LWIPOPTS__H__ */
