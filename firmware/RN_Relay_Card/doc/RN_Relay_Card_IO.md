# RN Relay IO 控制卡引脚定义

> 硬件版本：HW1.00  
> MCU：STM32F407VET6  

---

## 1. 18 路继电器控制 IO

| 通道 | MCU IO | 控制网络 | 驱动芯片 | 继电器 |
|---:|---|---|---|---|
| 1 | PD4 | OUT_1 | U6 ULN2803A | K1 |
| 2 | PD5 | OUT_2 | U6 ULN2803A | K2 |
| 3 | PD6 | OUT_3 | U6 ULN2803A | K3 |
| 4 | PD7 | OUT_4 | U6 ULN2803A | K4 |
| 5 | PB3 | OUT_5 | U6 ULN2803A | K5 |
| 6 | PB4 | OUT_6 | U6 ULN2803A | K6 |
| 7 | PB5 | OUT_7 | U6 ULN2803A | K7 |
| 8 | PB6 | OUT_8 | U6 ULN2803A | K8 |
| 9 | PA15 | OUT_9 | U7 ULN2803A | K9 |
| 10 | PC10 | OUT_10 | U7 ULN2803A | K10 |
| 11 | PC11 | OUT_11 | U7 ULN2803A | K11 |
| 12 | PC12 | OUT_12 | U7 ULN2803A | K12 |
| 13 | PD0 | OUT_13 | U7 ULN2803A | K13 |
| 14 | PD1 | OUT_14 | U7 ULN2803A | K14 |
| 15 | PD2 | OUT_15 | U7 ULN2803A | K15 |
| 16 | PD3 | OUT_16 | U7 ULN2803A | K16 |
| 17 | PB7 | OUT_17 | U8 ULN2803A | K17 |
| 18 | PB8 | OUT_18 | U8 ULN2803A | K18 |

### 继电器控制说明

- 继电器线圈由 `ULN2803A` 下拉驱动。
- MCU GPIO 输出高电平时，对应 ULN2803A 通道导通，继电器吸合。

---

## 2. 以太网 RMII / PHY IO

- PHY芯片型号:KSZ8863RLLI

| MCU IO | 信号 | 说明 |
|---|---|---|
| PA1 | RMII_REF_CLK | RMII 50 MHz 参考时钟 |
| PA2 | ETH_MDIO | PHY 管理数据 |
| PC1 | ETH_MDC | PHY 管理时钟 |
| PA7 | RMII_CRS_DV | RMII 接收数据有效 |
| PC4 | RMII_RXD0 | RMII 接收数据 0 |
| PC5 | RMII_RXD1 | RMII 接收数据 1 |
| PB11 | RMII_TX_EN | RMII 发送使能 |
| PB12 | RMII_TXD0 | RMII 发送数据 0 |
| PB13 | RMII_TXD1 | RMII 发送数据 1 |
| PA3 | ETH_RESET | 以太网芯片复位 |

---

## 3. STM32 调试 / 串口 / 系统 IO

| MCU IO | 信号 | 说明 |
|---|---|---|
| PA13 | SWDIO | STM32 SWD 调试数据 |
| PA14 | SWCLK | STM32 SWD 调试时钟 |
| PA9 | USART1_TX | STM32 串口发送 |
| PA10 | USART1_RX | STM32 串口接收 |
| PE2 | LED | 板载 LED |
| PH0 | OSC_IN | 8 MHz 晶振输入 |
| PH1 | OSC_OUT | 8 MHz 晶振输出 |

