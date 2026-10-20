# KSZ8863RLLI 编程相关内容整理

> 来源文件：`C631675_以太网收发器_KSZ8863RLLI_规格书_WJ70513.pdf`  
> 适用芯片系列：KSZ8863MLL / KSZ8863FLL / KSZ8863RLL，其中 **KSZ8863RLLI** 主要关注 RMII 接口版本。  
> 本文只整理和“软件配置、寄存器访问、调试、通信接口、上电配置”相关的内容，不包含封装、电气参数、磁性器件等硬件细节。

---

## 1. 编程关注点总览

KSZ8863 是一个 3 端口 10/100M 以太网交换芯片，内部包含：

- 2 个 10/100BASE-T/TX PHY，即 Port 1、Port 2；
- 1 个连接主控的 MAC 端口，即 Port 3；
- KSZ8863RLL 版本的 Port 3 为 **RMII 接口**；
- 内部 Layer-2 交换引擎、MAC 地址表、VLAN 表、MIB 计数器、QoS、限速、镜像、中断等功能；
- 可以通过 **SMI / MIIM / SPI / I2C / EEPROM** 进行配置。

软件上主要需要处理：

1. 选择管理接口模式；
2. 复位后初始化寄存器；
3. 配置 RMII 时钟和 Port 3 模式；
4. 配置 Port 1 / Port 2 的自协商、速率、双工、MDI/MDI-X；
5. 读取 Link 状态、PHY 状态、中断状态；
6. 按需配置 VLAN、QoS、镜像、广播风暴保护、限速；
7. 读取 MIB 计数器；
8. 使用 LinkMD 做网线诊断。

---

## 2. 管理模式选择

芯片既可以做非管理型交换芯片，也可以由 MCU/CPU 管理。

### 2.1 非管理模式

非管理模式下，芯片上电后主要依靠：

- 默认寄存器值；
- Strap 引脚状态；
- 可选 I2C EEPROM 配置。

如果使用 EEPROM，芯片复位释放后会从 EEPROM 顺序读取寄存器 0 到 198 的配置数据。EEPROM 第一个字节需要是 `0x88`，否则 EEPROM 配置会被忽略。

### 2.2 管理模式

管理模式下，外部 MCU/CPU 可以通过以下接口访问寄存器：

| 接口 | 可访问内容 | 典型用途 |
|---|---:|---|
| MIIM / MDIO | 标准 PHY 寄存器，16-bit | 读取 PHY link、自协商、速率、双工等基础状态 |
| SMI | 全部 8-bit 内部寄存器 `0x00 ~ 0xC6` | 推荐用于完整管理配置 |
| I2C Client | 全部 8-bit 内部寄存器 | MCU 使用 I2C 管理芯片 |
| SPI Client | 全部 8-bit 内部寄存器 | MCU 使用 SPI 高速配置或读取计数器 |
| I2C Host + EEPROM | 上电自动加载配置 | 无 MCU 的固定配置场景 |

---

## 3. 串行管理接口 Strap 配置

通过 `P2LED1` 和 `P2LED0` 选择寄存器访问模式。

| P2LED1 | P2LED0 | 模式 | 说明 |
|---:|---:|---|---|
| 0 | 0 | I2C Host / EEPROM | 芯片作为 I2C 主机，从 EEPROM 读取配置 |
| 0 | 1 | I2C Client | 外部 MCU 作为 I2C 主机访问芯片 |
| 1 | 0 | SPI Client | 外部 MCU 作为 SPI 主机访问芯片 |
| 1 | 1 | SMI / MIIM | 使用 MDC / MDIO 访问寄存器 |

相关管脚：

| 管脚号 | 管脚名 | 编程相关用途 |
|---:|---|---|
| 35 | `INTRN` | 中断输出，低有效，可用于 link change 等事件 |
| 36 | `SCL_MDC` | I2C SCL / SPI Clock / MIIM MDC |
| 37 | `SDA_MDIO` | I2C SDA / SPI MOSI / MIIM MDIO |
| 38 | `SPIQ` | SPI MISO |
| 39 | `SPISN` | SPI 片选，低有效；使用 SPI/SMI/MIIM 时建议外部上拉 |
| 45 | `P2LED1` | Strap：管理接口模式选择 |
| 46 | `P2LED0` | Strap：管理接口模式选择 |
| 47 | `RSTN` | 硬件复位，低有效 |

---

## 4. MIIM / MDIO 接口

MIIM 是标准 IEEE 802.3 MII Management Interface，也就是常见的 MDC/MDIO 管理接口。

### 4.1 MIIM 特点

- 数据线：`SDA_MDIO`
- 时钟线：`SCL_MDC`
- 最大时钟：`5 MHz`
- 用于访问 PHY 寄存器；
- 默认 PHY 地址：
  - Port 1 / PHY1：`PHYAD = 0x1`
  - Port 2 / PHY2：`PHYAD = 0x2`
- 支持的 `REGAD`：`0x0 ~ 0x5`、`0x1D`、`0x1F`

### 4.2 MIIM 帧格式

| 操作 | Preamble | ST | OP | PHYAD | REGAD | TA | Data |
|---|---|---|---|---|---|---|---|
| Read | 32 个 1 | `01` | `10` | 5 bit | 5 bit | `Z0` | 16 bit |
| Write | 32 个 1 | `01` | `01` | 5 bit | 5 bit | `10` | 16 bit |

### 4.3 常用 MIIM 寄存器

| PHY 寄存器 | 名称 | 说明 |
|---:|---|---|
| `0x00` | Basic Control | 复位、自协商、速率、双工、Power Down 等控制 |
| `0x01` | Basic Status | Link、自协商完成、能力状态 |
| `0x02` | PHYID High | PHY ID 高 16 位，默认 `0x0022` |
| `0x03` | PHYID Low | PHY ID 低 16 位，默认 `0x1430` |
| `0x04` | Auto-Negotiation Advertisement | 本端能力通告 |
| `0x05` | Auto-Negotiation Link Partner Ability | 对端能力 |
| `0x1D` | LinkMD Control/Status | LinkMD 网线诊断 |
| `0x1F` | PHY Special Control/Status | 极性、MDI-X、远程环回、强制 Link 等 |

---

## 5. SMI 接口

SMI 是 KSZ8863 的非标准 MIIM 扩展接口，可以访问全部内部 8-bit 寄存器。

### 5.1 SMI 特点

- 物理接口仍然是：`SCL_MDC` + `SDA_MDIO`；
- 可访问寄存器：`0x00 ~ 0xC6`；
- 可间接访问 Static MAC Table、VLAN Table、Dynamic MAC Table、MIB Counter；
- 寄存器宽度：8 bit；
- 读操作返回时，高 8 bit 为 0，低 8 bit 为寄存器数据；
- 写操作时，高 8 bit 可忽略，低 8 bit 为写入数据。

### 5.2 SMI 地址拼接方式

SMI 使用 `PHYAD[2:0]` 和 `REGAD[4:0]` 拼成 8-bit 寄存器地址：

```text
寄存器地址[7:0] = { PHYAD[2:0], REGAD[4:0] }
```

### 5.3 SMI 帧格式

| 操作 | Preamble | ST | OP | PHYAD | REGAD | TA | Data |
|---|---|---|---|---|---|---|---|
| Read | 32 个 1 | `01` | `00` | `1xRRR` | `RRRRR` | `Z0` | `00000000_DDDDDDDD` |
| Write | 32 个 1 | `01` | `00` | `0xRRR` | `RRRRR` | `10` | `xxxxxxxx_DDDDDDDD` |

其中：

- `R` 为寄存器地址位；
- Read 时 PHY Address bit4 = `1`；
- Write 时 PHY Address bit4 = `0`；
- PHY Address bit3 未定义，可以为 0 或 1。

---

## 6. I2C Client 接口

### 6.1 I2C Client 特点

- Strap：`P2LED[1:0] = 01`
- 外部 MCU 作为 I2C Host；
- 芯片作为 I2C Client；
- 可以访问 8-bit 内部寄存器；
- 寄存器地址方式类似 AT24C02 EEPROM。

### 6.2 I2C 地址

| 操作 | 二进制地址 | 8-bit 地址 |
|---|---|---:|
| Read | `1011_1111` | `0xBF` |
| Write | `1011_1110` | `0xBE` |

> 注意：这里规格书给的是 8-bit 地址，部分 MCU HAL 使用 7-bit 地址时需要右移 1 位，即 `0x5F`，再由 HAL 添加 R/W bit。

### 6.3 推荐流程

1. Strap 配置为 I2C Client：`P2LED[1:0] = 01`；
2. 上电；
3. 拉低并释放 `RSTN`；
4. 通过 I2C 写入目标寄存器；
5. 通过 I2C 读回校验；
6. 部分配置可以在交换芯片已经启动后动态修改，例如 Aging、自协商、强制速率、Power Down 等。

---

## 7. SPI Client 接口

### 7.1 SPI Client 特点

- Strap：`P2LED[1:0] = 10`
- 外部 MCU 作为 SPI Host；
- 最大 SPI 时钟：`25 MHz`；
- 支持单寄存器读写和连续多寄存器读写；
- 读写连续寄存器时，内部地址自动递增；
- 地址到达最高寄存器后会回绕到 `0x00`。

### 7.2 SPI 管脚连接

| KSZ8863 管脚号 | 信号名 | 外部 MCU 信号 |
|---:|---|---|
| 39 | `SPISN` | SPI CS，低有效 |
| 36 | `SCL / SPIC` | SPI SCK |
| 37 | `SDA / SPID` | SPI MOSI，Host 输出 / Client 输入 |
| 38 | `SPIQ` | SPI MISO，Host 输入 / Client 输出 |

### 7.3 SPI 命令

| 命令 | 二进制 | 十六进制 |
|---|---|---:|
| Read | `0000_0011` | `0x03` |
| Write | `0000_0010` | `0x02` |

### 7.4 SPI 时序要点

- `SPISN` 拉低后开始一次访问；
- 写入顺序：`Command` → `Address` → `Data`；
- 读取顺序：`Command` → `Address` → 读出 `Data`；
- 输入数据在 SPI Clock 上升沿采样；
- 输出数据在 SPI Clock 下降沿更新；
- 连续读/写时保持 `SPISN` 为低，芯片内部地址自动加 1；
- 结束访问时释放 `SPISN` 为高。

### 7.5 SPI 伪代码

```c
uint8_t ksz8863_spi_read(uint8_t reg)
{
    uint8_t val;
    CS_LOW();
    spi_tx(0x03);      // READ
    spi_tx(reg);       // register address
    val = spi_rx();
    CS_HIGH();
    return val;
}

void ksz8863_spi_write(uint8_t reg, uint8_t val)
{
    CS_LOW();
    spi_tx(0x02);      // WRITE
    spi_tx(reg);       // register address
    spi_tx(val);       // data
    CS_HIGH();
}
```

---

## 8. I2C Host / EEPROM 上电配置

当 `P2LED[1:0] = 00` 时，芯片作为 I2C Host 读取外部 EEPROM。

流程：

1. 将 KSZ8863 的 `SCL` / `SDA` 与 EEPROM 相连；
2. Strap 配置：`P2LED[1:0] = 00`；
3. 确保 `RSTN` 连接到外部复位源；
4. 将寄存器配置写入 EEPROM；
5. 上电并释放复位；
6. 芯片从 EEPROM 第一个字节开始读取；
7. 第一个字节必须为 `0x88`；
8. 如果第一个字节不是 `0x88`，EEPROM 配置会被忽略。

EEPROM 配置加载时间规格书示意为：`tprgm < 15 ms`。

---

## 9. 复位和开始编程时序

软件初始化时需要注意：

- `RSTN` 为低有效；
- 复位释放后，不要立即访问管理接口；
- 规格书要求：复位释放后至少等待 **100 us**，再通过 I2C Client / SPI Client / SMI / MIIM 开始编程；
- 如果使用 EEPROM 配置，需要等待 EEPROM 自动加载完成。

推荐 MCU 初始化流程：

```text
1. 配置 MCU GPIO / SPI / I2C / MDIO 外设
2. 释放 KSZ8863 RSTN
3. delay >= 100 us
4. 读取 Chip ID，确认通信正常
5. 写入配置寄存器
6. 读回关键寄存器校验
7. 启动或确认 Switch Start bit
8. 读取 Port Link 状态
```

---

## 10. RMII / Port 3 编程关注点

KSZ8863RLLI 的 Port 3 是 RMII 接口，通常接 MCU/MPU 的 MAC，例如 STM32H7 的 ETH MAC。

### 10.1 RMII 信号

| RMII 信号 | KSZ8863RLL 信号 | 方向说明 |
|---|---|---|
| `REF_CLK` | `REFCLKI_3` / `REFCLKO_3` | 50 MHz RMII 参考时钟 |
| `CRS_DV` | `SMRXDV3` | 芯片输出到 MAC |
| `RXD1` | `SMRXD31` | 芯片输出到 MAC |
| `RXD0` | `SMRXD30` | 芯片输出到 MAC |
| `TX_EN` | `SMTXEN3` | MAC 输出到芯片 |
| `TXD1` | `SMTXD31` | MAC 输出到芯片 |
| `TXD0` | `SMTXD30` | MAC 输出到芯片 |
| `RX_ER` | 未实现 | KSZ8863RLL 不输出 RX_ER |

### 10.2 RMII 时钟配置

RMII 必须有 50 MHz 参考时钟。KSZ8863RLL 可内部生成或外部输入 50 MHz。

相关配置：

| 项目 | 说明 |
|---|---|
| `Pin 17 SMTXD33 / EN_REFCLKO_3` | Strap：是否输出 REFCLKO_3 |
| `Pin 18 SMTXD32` | Strap：X1 时钟源选择，25 MHz 或 50 MHz |
| `Register 198 bit3` | Port 3 RMII Clock Selection，`1 = Internal`，`0 = External` |

如果由 KSZ8863RLL 给 MCU 提供 50 MHz RMII REF_CLK，需要确认：

- `REFCLKO_3` 已使能；
- MCU ETH 外设使用外部 RMII REF_CLK；
- PCB 上时钟走线和时序满足要求。

如果由外部有源晶振提供 50 MHz，需要确认：

- 50 MHz 输入到 `REFCLKI_3`；
- 对应 Strap 和 Register 198 bit3 配置为外部时钟；
- STM32 等主控也使用同一个 50 MHz RMII 参考时钟。

---

## 11. 8-bit 内部寄存器地址总表

| 地址范围 | 十六进制 | 说明 |
|---:|---|---|
| 0 - 1 | `0x00 - 0x01` | Chip ID / Start Switch |
| 2 - 15 | `0x02 - 0x0F` | Global Control Registers |
| 16 - 29 | `0x10 - 0x1D` | Port 1 Control Registers，含部分 PHY 映射 |
| 30 - 31 | `0x1E - 0x1F` | Port 1 Status Registers |
| 32 - 45 | `0x20 - 0x2D` | Port 2 Control Registers，含部分 PHY 映射 |
| 46 - 47 | `0x2E - 0x2F` | Port 2 Status Registers |
| 48 - 57 | `0x30 - 0x39` | Port 3 Control Registers |
| 58 - 62 | `0x3A - 0x3E` | Reserved |
| 63 | `0x3F` | Port 3 Status Register |
| 64 - 95 | `0x40 - 0x5F` | Reserved |
| 96 - 111 | `0x60 - 0x6F` | TOS Priority Control Registers |
| 112 - 117 | `0x70 - 0x75` | Switch Engine MAC Address |
| 118 - 120 | `0x76 - 0x78` | User Defined Registers |
| 121 - 122 | `0x79 - 0x7A` | Indirect Access Control |
| 123 - 131 | `0x7B - 0x83` | Indirect Data Registers |
| 142 - 153 | `0x8E - 0x99` | Station Address 1/2 MAC |
| 154 - 165 | `0x9A - 0xA5` | Egress Data Rate Limit |
| 166 | `0xA6` | Device Mode Indicator |
| 167 - 170 | `0xA7 - 0xAA` | High Priority Packet Buffer Reserved |
| 171 - 174 | `0xAB - 0xAE` | PM Usage Flow Control Select Mode |
| 175 - 186 | `0xAF - 0xBA` | TX Queue Split |
| 187 - 188 | `0xBB - 0xBC` | Link Change Interrupt |
| 189 | `0xBD` | Force Pause Off Iteration Limit Enable |
| 192 | `0xC0` | Fiber Signal Threshold |
| 194 | `0xC2` | Insert SRC PVID |
| 195 | `0xC3` | Power Management and LED Mode |
| 196 | `0xC4` | Sleep Mode |
| 198 | `0xC6` | Forward Invalid VID Frame and Host Mode |

---

## 12. 常用寄存器整理

### 12.1 芯片识别与启动

| 地址 | 名称 | 关键位 | 说明 |
|---:|---|---|---|
| `0x00` | Chip ID0 | `[7:0]` | Family ID，默认 `0x88` |
| `0x01` | Chip ID1 / Start Switch | `[7:4]` Chip ID，`[3:1]` Revision ID，`[0]` Start Switch | bit0 = 1 启动交换，默认启动 |

初始化时建议首先读取：

```text
Reg 0x00 == 0x88
Reg 0x01[7:4] == 0x3
```

### 12.2 Global Control 常用项

| 地址 | 名称 | 典型用途 |
|---:|---|---|
| `0x02` | Global Control 0 | Flush Dynamic MAC Table、Flush Static MAC Table、Pass Flow Control Packet |
| `0x03` | Global Control 1 | Tail Tag、流控、Aging、Fast Age、Aggressive Back-Off |
| `0x04` | Global Control 2 | VLAN 边界、广播/组播风暴保护、Back Pressure、最大帧长度、Huge Packet |
| `0x05` | Global Control 3 | IGMP Snooping、Weighted Fair Queue、Priority Queue 相关功能 |
| `0x06 - 0x07` | Global Control 4/5 | Broadcast Storm Protection Rate |
| `0x08 - 0x0B` | Global Control 6/7/8/9 | Priority、SPI Clock / Switch Clock 相关配置 |
| `0x0C - 0x0D` | Global Control 10/11 | 802.1p Priority Mapping |
| `0x0E` | Global Control 12 | Unknown Unicast 默认转发端口 |
| `0x0F` | Global Control 13 | PHY 地址配置 |

### 12.3 Port Control / Status 地址规律

| 功能 | Port 1 | Port 2 | Port 3 |
|---|---:|---:|---:|
| Control 0 | `0x10` | `0x20` | `0x30` |
| Control 1 | `0x11` | `0x21` | `0x31` |
| Control 2 | `0x12` | `0x22` | `0x32` |
| Control 3 | `0x13` | `0x23` | `0x33` |
| Control 4 | `0x14` | `0x24` | `0x34` |
| Control 5 | `0x15` | `0x25` | `0x35` |
| Ingress Q0-Q3 Rate | `0x16-0x19` | `0x26-0x29` | `0x36-0x39` |
| PHY Special Control/Status | `0x1A` | `0x2A` | 不适用 |
| LinkMD Result | `0x1B` | `0x2B` | 不适用 |
| Control 12 | `0x1C` | `0x2C` | 不适用 |
| Control 13 | `0x1D` | `0x2D` | 不适用 |
| Status 0 | `0x1E` | `0x2E` | 不适用 |
| Status 1 | `0x1F` | `0x2F` | `0x3F` |

### 12.4 Port Control 0：优先级和队列

`0x10 / 0x20 / 0x30`

常用于配置：

- Broadcast Storm Protection Enable；
- DiffServ / TOS Priority；
- 802.1p Priority；
- Port-based Priority；
- TX Queue Split Enable。

### 12.5 Port Control 1：镜像和 Port VLAN

`0x11 / 0x21 / 0x31`

常用于配置：

- Sniffer Port；
- Receive Sniff；
- Transmit Sniff；
- Port VLAN Membership。

### 12.6 Port Control 2：Tag、Ingress VLAN、队列

`0x12 / 0x22 / 0x32`

常用于配置：

- 用户优先级 CEILING；
- Ingress VLAN Filtering；
- Discard Non-PVID Packet；
- Force Remove Tag；
- Force Add Tag；
- 2 Queue Split。

### 12.7 Port Control 3/4：默认 VLAN Tag

| 地址 | 说明 |
|---:|---|
| `0x13 / 0x23 / 0x33` | 默认 Tag 高位，包含 Priority、CFI、VID 高位 |
| `0x14 / 0x24 / 0x34` | 默认 Tag 低位，包含 VID[7:0] |

### 12.8 Port Control 5：Port 3 MII/RMII 和源地址过滤

| 地址 | 说明 |
|---:|---|
| `0x15` | Port 1 Control 5 |
| `0x25` | Port 2 Control 5 |
| `0x35` | Port 3 Control 5，其中 bit7 可设置 Port 3 MII MAC/PHY 模式；RLL 场景还需关注 RMII 相关配置 |

### 12.9 Port 1 / Port 2 PHY 控制

| 地址 | 名称 | 说明 |
|---:|---|---|
| `0x1C` / `0x2C` | Control 12 | 自协商能力、强制速率、强制双工、Pause 能力通告等 |
| `0x1D` / `0x2D` | Control 13 | Disable LED、Disable TX、Restart AN、Power Down、Disable Auto MDI/MDI-X、Force MDI、Loopback 等 |
| `0x1E` / `0x2E` | Status 0 | MDI-X、AN Done、Link Up、Link Partner 能力等 |
| `0x1F` / `0x2F` | Status 1 | PHY 特殊状态，含极性、流控等状态 |

---

## 13. Power Management / LED 配置

### 13.1 Register 195：`0xC3` Power Management and LED Mode

| Bit | 名称 | 说明 |
|---:|---|---|
| 7 | CPU Interface Power Down | CPU 接口低功耗控制 |
| 6 | Switch Power Down | Switch clock tree 低功耗控制 |
| 5:4 | LED Mode Selection | LED0/LED1 功能选择 |
| 3 | LED Output Mode | LED 调试输出模式 |
| 2 | PLL Off Enable | Energy Detect 模式下 PLL 关闭使能 |
| 1:0 | Power Management Mode | `00` Normal，`01` Energy Detect，`10` Software Power Down，`11` Power Saving |

### 13.2 Register 196：`0xC4` Sleep Mode

- 单位：20 ms；
- 默认值：`0x50`；
- 默认低功耗进入等待时间：`0x50 * 20 ms = 1.6 s`。

---

## 14. Register 198：Host Interface / RMII Clock

`0xC6` 用于 Forward Invalid VID Frame 和 Host Mode。

| Bit | 名称 | 说明 |
|---:|---|---|
| 7 | Reserved | 不要修改默认值 |
| 6:4 | Forward Invalid VID Frame | VID 无效帧转发端口 |
| 3 | P3 RMII Clock Selection | `1 = Internal`，`0 = External` |
| 2 | P1 RMII Clock Selection | `1 = Internal`，`0 = External` |
| 1:0 | Host Interface Mode | `00` I2C Host，`01` I2C Client，`10` SPI Client，`11` SMI |

---

## 15. LinkMD 网线诊断

LinkMD 用于铜缆诊断，可检测：正常、开路、短路、测试失败，并估算故障距离。

Port 2 示例流程：

1. 写 `Reg 0x2D bit2 = 1`，关闭 Auto MDI/MDI-X，启用手动控制；
2. 写 `Reg 0x2A bit4 = 1`，启动 Cable Diagnostic；
3. 轮询 `Reg 0x2A bit4`，等待其自动清零；
4. 读取 `Reg 0x2A bit[6:5]`：
   - `00`：正常；
   - `01`：开路；
   - `10`：短路；
   - `11`：测试失败；
5. 故障距离：

```text
Distance(m) ≈ 0.4 × { Reg 0x2A bit0, Reg 0x2B[7:0] }
```

Port 1 对应寄存器为：

| 功能 | Port 1 | Port 2 |
|---|---:|---:|
| PHY Special Control/Status | `0x1A` | `0x2A` |
| LinkMD Result | `0x1B` | `0x2B` |
| Control 13 | `0x1D` | `0x2D` |

---

## 16. 间接访问：Static MAC / VLAN / Dynamic MAC / MIB

间接访问使用以下寄存器：

| 地址 | 名称 | 说明 |
|---:|---|---|
| `0x79` | Indirect Access Control 0 | 选择表类型、读写方向、间接地址高位等 |
| `0x7A` | Indirect Access Control 1 | 间接地址低位 / 触发操作 |
| `0x7B - 0x83` | Indirect Data 8 - 0 | 间接数据寄存器 |

可通过间接访问操作：

- Static MAC Address Table；
- VLAN Table；
- Dynamic MAC Address Table；
- MIB Counters。

### 16.1 Static MAC Table 格式概要

Static MAC Table 有 8 个条目，常用字段：

| Bit | 名称 | 说明 |
|---:|---|---|
| 57:54 | FID | VLAN Filter ID |
| 53 | Use FID | 是否使用 FID + MAC 匹配 |
| 52 | Override | 是否覆盖端口 TX/RX Enable 设置 |
| 51 | Valid | 条目有效位 |
| 50:48 | Forwarding Ports | 转发端口位图 |
| 47:0 | MAC Address | 48-bit MAC 地址 |

转发端口位图：

| 值 | 说明 |
|---:|---|
| `001` | 转发到 Port 1 |
| `010` | 转发到 Port 2 |
| `100` | 转发到 Port 3 |
| `011` | 转发到 Port 1 + Port 2 |
| `101` | 转发到 Port 1 + Port 3 |
| `110` | 转发到 Port 2 + Port 3 |
| `111` | 广播，排除 ingress port |

---

## 17. MIB 计数器读取注意事项

MIB Counters 通过间接访问读取。

软件注意点：

- Per-Port MIB Counters 是 **read clear**，读取后清零；
- All-Port Dropped Packet Counters 读取后不清零，也不提供 overflow / valid 标志，应用层需要自行跟踪；
- 在重负载场景下，计数器可能约 2 分钟溢出；
- 规格书建议软件至少每 **30 秒** 读取一次全部计数器；
- 使用 SPI 读取大量计数器时建议使用高性能 SPI Host，避免读取太慢。

---

## 18. 常见初始化建议

### 18.1 使用 SPI 管理时

```text
1. Strap: P2LED[1:0] = 10
2. MCU 初始化 SPI
3. 释放 RSTN
4. delay >= 100 us
5. SPI read 0x00，确认为 0x88
6. SPI read 0x01，确认 Chip ID / Start Switch
7. 配置 Register 198，确认 Host Interface 和 RMII Clock
8. 配置 Port 1 / Port 2 自协商或强制模式
9. 配置 VLAN / QoS / Storm Protection / Rate Limit
10. 读回关键寄存器
11. 周期读取 Link 状态、中断状态、MIB 计数器
```

### 18.2 使用 I2C 管理时

```text
1. Strap: P2LED[1:0] = 01
2. MCU 初始化 I2C
3. 释放 RSTN
4. delay >= 100 us
5. I2C 读取 0x00，确认为 0x88
6. 写入配置寄存器
7. 读回校验
```

### 18.3 使用 SMI / MDIO 管理时

```text
1. Strap: P2LED[1:0] = 11
2. MCU 初始化 MDC/MDIO
3. 释放 RSTN
4. delay >= 100 us
5. 使用 MIIM 读取 PHY 基础状态
6. 使用 SMI 访问 8-bit 全局/端口/高级寄存器
```

---

## 19. STM32 + KSZ8863RLLI 使用提醒

如果主控是 STM32H743/H750 这类带 ETH MAC 的 MCU，且连接 KSZ8863RLLI 的 Port 3 RMII：

1. STM32 ETH 必须工作在 RMII 模式；
2. RMII 50 MHz `REF_CLK` 必须稳定；
3. 确认 50 MHz 是由 KSZ8863 输出，还是外部晶振统一提供；
4. `REF_CLK` 不应被配置成普通 GPIO；
5. KSZ8863RLL 不提供 `RX_ER` 输出，STM32 侧如有 `ETH_RX_ER` 可按实际硬件处理；
6. 初始化后先读 `Reg 0x00` 和 `Reg 0x01` 验证管理接口；
7. 再读 Port 1/2 Link 状态确认外部网口是否连接；
8. 如需调试交换路径，可以先使用默认交换功能，再逐步启用 VLAN/QoS/限速等高级功能。

---

## 20. 最小 Bring-up 检查表

- [ ] Strap 模式是否正确：I2C / SPI / SMI？
- [ ] `RSTN` 释放后是否等待至少 100 us？
- [ ] 能否读取 `Reg 0x00 = 0x88`？
- [ ] `Reg 0x01 bit0` 是否为 1，交换是否启动？
- [ ] RMII 50 MHz 时钟来源是否正确？
- [ ] STM32/主控 RMII 引脚是否配置为 ETH AF？
- [ ] Port 1/2 是否自协商开启？
- [ ] 是否能读取 Link Up 状态？
- [ ] 是否需要配置 `Register 198 bit3` 的 Port 3 RMII 时钟选择？
- [ ] 是否启用中断 `INTRN` 和 `Reg 0xBB/0xBC`？
- [ ] 是否定期读取 MIB 计数器？

---

## 21. 软件封装建议

建议把驱动分成三层：

```text
ksz8863_bus.c
  - ksz_read8(reg)
  - ksz_write8(reg, val)
  - ksz_read_burst(reg, buf, len)
  - ksz_write_burst(reg, buf, len)

ksz8863_reg.c
  - ksz_chip_id()
  - ksz_start_switch()
  - ksz_set_rmii_clock()
  - ksz_get_link(port)
  - ksz_restart_an(port)
  - ksz_power_down_port(port)
  - ksz_read_mib(port, counter)
  - ksz_linkmd_test(port)

ksz8863_config.c
  - ksz_default_init()
  - ksz_vlan_init()
  - ksz_qos_init()
  - ksz_rate_limit_init()
  - ksz_interrupt_init()
```

核心宏建议：

```c
#define KSZ_REG_CHIP_ID0        0x00
#define KSZ_REG_CHIP_ID1        0x01
#define KSZ_REG_GLOBAL_CTRL0    0x02
#define KSZ_REG_GLOBAL_CTRL1    0x03
#define KSZ_REG_PORT1_CTRL0     0x10
#define KSZ_REG_PORT2_CTRL0     0x20
#define KSZ_REG_PORT3_CTRL0     0x30
#define KSZ_REG_PORT1_STATUS0   0x1E
#define KSZ_REG_PORT2_STATUS0   0x2E
#define KSZ_REG_INTR_ENABLE     0xBB
#define KSZ_REG_LINK_CHANGE     0xBC
#define KSZ_REG_POWER_LED       0xC3
#define KSZ_REG_SLEEP_MODE      0xC4
#define KSZ_REG_HOST_MODE       0xC6
```

---

## 22. 需要重点核对的原文页码

| 内容 | 原文位置 |
|---|---|
| 管脚和 Strap 配置 | Page 6 - Page 10 |
| MIIM 接口 | Page 24 - Page 25 |
| SMI 接口 | Page 25 |
| I2C Host / EEPROM | Page 31 |
| I2C Client | Page 32 |
| SPI Client | Page 32 - Page 33 |
| Register Descriptions | Page 36 - Page 66 |
| Static MAC / VLAN / MIB 间接访问 | Page 66 - Page 72 |
| 管理接口复位后等待时间 | Page 83 |

