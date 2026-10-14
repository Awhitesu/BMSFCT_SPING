# RN-EOL 可编程电阻板卡 - 嵌入式开发手册

**硬件版本:** V1.1  
**核心MCU:** STM32F407VET6  
**功能:** 通过继电器阵列实现 1kΩ ~ 5MΩ 的电阻输出控制。

---

## 1. 系统基础配置 (System Config)

### 1.1 时钟配置 (Clock Tree)
*   **HSE (外部高速时钟):** 8 MHz (晶振 X2)
*   **LSE (外部低速时钟):** 32.768 kHz (晶振 X3)
*   **主频建议:** STM32F407 标准最高主频 168 MHz (通过 PLL 倍频)

### 1.2 电源与复位
*   **系统电压:** 3.3V
*   **复位方式:** 低电平复位 (NRST 引脚)，板载复位按键 SW1。

---

## 2. 继电器控制接口 (核心功能)

板卡包含18个阻值继电器 (K1-K18) 和1个输出隔离继电器 (K19)，通过 ULN2803A (达林顿管阵列) 驱动。
*   **控制逻辑:**
    *   **GPIO 输出高电平 (High, 1):** ULN2803 导通 -> 继电器吸合 -> **电阻接入电路**。
    *   **GPIO 输出低电平 (Low, 0):** ULN2803 截止 -> 继电器释放 -> **电阻被短路 (旁路)**。
    *   **K19 (PC12, 常闭点):** 上电默认输出高电平吸合，断开0Ω输出；设置非0阻值后释放K19，闭合输出路径。

### 2.1 GPIO 映射表

| 继电器 | 阻值 | 档位(位权) | MCU引脚 | GPIO Port | GPIO Pin | 
| :--- | :--- | :--- | :--- | :--- | :--- |
| **K1** | 1 kΩ | 1k 基数 | **PE13** | GPIOE | Pin 13 |
| **K2** | 2 kΩ | 1k 组合 | **PE12** | GPIOE | Pin 12 |
| **K3** | 2 kΩ | 1k 组合 | **PE11** | GPIOE | Pin 11 |
| **K4** | 2 kΩ | 1k 组合 | **PE10** | GPIOE | Pin 10 |
| **K5** | 2 kΩ | 1k 组合 | **PE9** | GPIOE | Pin 9 |
| | | | | | |
| **K6** | 10 kΩ | 10k 基数 | **PE8** | GPIOE | Pin 8 |
| **K7** | 20 kΩ | 10k 组合 | **PB6** | GPIOB | Pin 6 |
| **K8** | 20 kΩ | 10k 组合 | **PB5** | GPIOB | Pin 5 |
| **K9** | 20 kΩ | 10k 组合 | **PB4** | GPIOB | Pin 4 |
| **K10** | 20 kΩ | 10k 组合 | **PB3** | GPIOB | Pin 3 |
| | | | | | |
| **K11** | 100 kΩ | 100k 基数| **PD7** | GPIOD | Pin 7 |
| **K12** | 200 kΩ | 100k 组合| **PD6** | GPIOD | Pin 6 |
| **K13** | 200 kΩ | 100k 组合| **PD5** | GPIOD | Pin 5 |
| **K14** | 200 kΩ | 100k 组合| **PD4** | GPIOD | Pin 4 |
| **K15** | 200 kΩ | 100k 组合| **PD3** | GPIOD | Pin 3 |
| | | | | | |
| **K16** | 1 MΩ | 1M 基数 | **PD2** | GPIOD | Pin 2 |
| **K17** | 2 MΩ | 1M 组合 | **PD1** | GPIOD | Pin 1 |
| **K18** | 2 MΩ | 1M 组合 | **PD0** | GPIOD | Pin 0 |
| **K19** | 输出隔离 | 常闭点 | **PC12** | GPIOC | Pin 12 |

### 2.2 电阻组合算法建议
每一级（如1k级、10k级）的电阻均由 **1个基数 (1x)** 和 **4个双倍数 (2x)** 组成。
例如 1kΩ 档位：1k, 2k, 2k, 2k, 2k。最大可组成 9kΩ。

**推荐组合逻辑 (实现 0-9 的系数):**
*   **1:** 1k (K1)
*   **2:** 2k (K2)
*   **3:** 1k + 2k (K1 + K2)
*   **4:** 2k + 2k (K2 + K3)
*   **5:** 1k + 2k + 2k (K1 + K2 + K3)
*   **6:** 2k + 2k + 2k (K2 + K3 + K4)
*   **7:** 1k + 2k + 2k + 2k (K1 + K2 + K3 + K4)
*   **8:** 2k + 2k + 2k + 2k (K2 + K3 + K4 + K5)
*   **9:** 1k + 2k + 2k + 2k + 2k (全开)

---

## 3. 通信接口 (Communication)

### 3.1 以太网 (Ethernet)
*   **PHY芯片:** YT8512C
*   **接口模式:** RMII
*   **MCU外设:** ETH_MAC

**RMII 引脚定义:**

| 功能 | MCU引脚 | 备注 |
| :--- | :--- | :--- |
| **REF_CLK** | PA1 | 50MHz 参考时钟 |
| **MDIO** | PA2 | 管理数据 |
| **MDC** | PC1 | 管理时钟 |
| **CRS_DV** | PA7 | 载波侦听/数据有效 |
| **RXD0** | PC4 | 接收数据0 |
| **RXD1** | PC5 | 接收数据1 |
| **TX_EN** | PB11 | 发送使能 |
| **TXD0** | PB12 | 发送数据0 |
| **TXD1** | PB13 | 发送数据1 |
| **RESET** | PD8 | PHY硬件复位引脚 (低电平有效) |

*注意：YT8512C 的 PHY 地址通常由硬件 Strapping 决定，需在驱动中扫描地址（通常为 0 或 1）。*

### 3.2 串口 (UART / Debug)
用于打印日志或指令控制。
*   **端口:** USART1
*   **TX:** PA9
*   **RX:** PA10
*   **波特率:** 常用 115200 bps

---

## 4. 其他外设

### 4.1 指示灯 (LED)
*   **用户 LED (LED20):** 绿色
    *   **网络标号:** `LED`
    *   **GPIO:** 需检查具体定义，通常连接至 MCU 的 **PE7** 或 **PD13** (图纸中未明确连线至MCU引脚，但在通用STM32板卡设计中常见，建议编写简单的翻转IO程序测试)。
*   **继电器状态灯 (LED1-18):** 硬件直接并联在继电器线圈上，随继电器动作自动亮灭，**无需软件控制**。

### 4.2 调试接口 (SWD)
*   **SWDIO:** PA13
*   **SWCLK:** PA14

---

## 5. 初始化代码伪代码 (Reference)

```c
// 1. 继电器 GPIO 初始化
void Relay_GPIO_Init(void) {
    // 开启 GPIOC, GPIOE, GPIOB, GPIOD 时钟
    __HAL_RCC_GPIOC_CLK_ENABLE();
    __HAL_RCC_GPIOE_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOD_CLK_ENABLE();

    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP; // 推挽输出
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;

    // 配置阻值继电器引脚 (PE13, PE9... PB4... PD5...)
    GPIO_InitStruct.Pin = GPIO_PIN_13 | GPIO_PIN_9 | ...;
    HAL_GPIO_Init(GPIOE, &GPIO_InitStruct);
    // ... (重复配置 Port B 和 Port D)

    // 配置K19输出隔离继电器(PC12)，上电默认吸合断开0Ω输出。
    GPIO_InitStruct.Pin = GPIO_PIN_12;
    HAL_GPIO_Init(GPIOC, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_12, GPIO_PIN_SET);

    // K1-K18默认全部关闭。
    // 根据图纸: Relay OFF = Resistor Bypassed (0 Ohm). 
    // Relay ON = Resistor in series.
    HAL_GPIO_WritePin(GPIOE, GPIO_PIN_13, GPIO_PIN_RESET);
}

// 2. 设置阻值函数
void Set_Resistance(uint32_t resistance_ohm) {
    // 分解 resistance_ohm 到各个档位 (1k, 10k, 100k, 1M)
    // 计算每一级需要的系数 (0-9)
    // 根据系数控制对应的 GPIO High/Low
}
```
