# RN Relay IO Cards

STM32F407VET6 多型号继电器 Modbus-TCP 控制程序。

本工程基于 PlatformIO + STM32Cube HAL，使用 LwIP 裸机 TCP/IP 协议栈，通过 KSZ8863RLLI RMII 以太网交换芯片提供 Modbus-TCP 服务。不同板卡共用同一套 IO 定义和业务代码，通过 PlatformIO env 切换有效继电器数量。

## 支持板卡

| PlatformIO env | 板卡名称 | 有效继电器数 | `0x0103` 通道数量 | 有效线圈地址 |
|---|---|---:|---:|---|
| `DMM_Card` | DMM Card | 18 | 18 | `0..17` |
| `HSD_Card` | HSD Card | 15 | 15 | `0..14` |
| `HV_Card` | HV Card | 10 | 10 | `0..9` |
| `Relay_Card` | Relay Card | 12 | 12 | `0..11` |

访问不存在的线圈地址会返回 Modbus 异常 `ILLEGAL_DATA_ADDRESS`。

## 硬件概览

- MCU：STM32F407VET6
- 以太网：KSZ8863RLLI，RMII 接口
- 继电器：最多 18 路，ULN2803A 下拉驱动
- 板载 LED：PE2
- 调试/下载：SWD，默认 `cmsis-dap`
- 串口：USART1，115200 bps

详细 IO 定义见：

- `doc/RN_Relay_Card_IO.md`
- `doc/KSZ8863RLLI_编程相关内容整理.md`

## 网络参数

默认静态 IPv4 地址在 `src/lwip.c` 中配置：

| 项目 | 默认值 |
|---|---|
| IP | `192.168.1.10` |
| Netmask | `255.255.255.0` |
| Gateway | `192.168.1.1` |
| Modbus-TCP Port | `502` |
| 最大客户端数 | `1` |

MAC 地址由 STM32 UID 生成。

## Modbus-TCP 功能

继电器控制使用线圈功能；系统信息和网络配置保留兼容保持寄存器。

| 功能码 | 名称 | 说明 |
|---:|---|---|
| `0x01` | Read Coils | 读取继电器状态 |
| `0x05` | Write Single Coil | 控制单路继电器 |
| `0x0F` | Write Multiple Coils | 批量控制继电器 |

兼容 `RES_MODBUS_TCP` 项目的系统信息和网络配置保持寄存器：

| 功能码 | 名称 | 说明 |
|---:|---|---|
| `0x03` | Read Holding Registers | 读取系统信息/网络配置 |
| `0x04` | Read Input Registers | 读取系统信息/网络配置 |
| `0x06` | Write Single Register | 写入网络配置/控制 |
| `0x10` | Write Multiple Registers | 批量写入网络配置/控制 |

系统信息寄存器：

| 地址 | 名称 | 当前定义 |
|---:|---|---|
| `0x0100` | 系统状态 | `0x0001` 正常 |
| `0x0101` | 设备 ID | `0x5243`，ASCII `"RC"` |
| `0x0102` | 固件版本 | `0x0200` |
| `0x0103` | 通道使能/通道数量 | 当前板卡有效继电器数量 |
| `0x0104` | IO 状态 | `0x0001` 表示继电器 IO 已初始化 |

网络配置寄存器：

| 地址 | 名称 | 说明 |
|---:|---|---|
| `0x0110` | IP 地址高 16 位 | `IP[0]<<8 | IP[1]` |
| `0x0111` | IP 地址低 16 位 | `IP[2]<<8 | IP[3]` |
| `0x0112` | 子网掩码高 16 位 | `MASK[0]<<8 | MASK[1]` |
| `0x0113` | 子网掩码低 16 位 | `MASK[2]<<8 | MASK[3]` |
| `0x0114` | 网关高 16 位 | `GW[0]<<8 | GW[1]` |
| `0x0115` | 网关低 16 位 | `GW[2]<<8 | GW[3]` |
| `0x0116` | 网络控制 | 见下表 |
| `0x011F` | 软件重启 | 写 `0x0001` 触发软件重启 |

网络控制寄存器位定义：

| 位 | 名称 | 描述 |
|---:|---|---|
| 0 | `APPLY_CONFIG` | 写 1 保存网络配置到 Flash |
| 1 | `RESTORE_DEFAULTS` | 写 1 恢复默认配置 |
| 8 | `REBOOT_REQUIRED` | 只读，需要重启标志 |
| 12 | `SOFTWARE_RESET` | 写 1 触发软件重启 |

线圈地址映射从 K1 开始连续排列：

| 线圈地址 | 继电器 | MCU IO |
|---:|---|---|
| `0` | K1 | PD4 |
| `1` | K2 | PD5 |
| `2` | K3 | PD6 |
| `3` | K4 | PD7 |
| `4` | K5 | PB3 |
| `5` | K6 | PB4 |
| `6` | K7 | PB5 |
| `7` | K8 | PB6 |
| `8` | K9 | PA15 |
| `9` | K10 | PC10 |
| `10` | K11 | PC11 |
| `11` | K12 | PC12 |
| `12` | K13 | PD0 |
| `13` | K14 | PD1 |
| `14` | K15 | PD2 |
| `15` | K16 | PD3 |
| `16` | K17 | PB7 |
| `17` | K18 | PB8 |

线圈写入规则：

- `0xFF00`：继电器吸合
- `0x0000`：继电器释放
- 其他写入值返回 Modbus 异常

## 板卡配置

板卡差异集中在：

- `platformio.ini`：定义每个 env 的板卡类型宏、`RELAY_CHANNEL_COUNT`
- `include/app_config.h`：统一生成 `RN_BOARD_NAME`，并校验继电器数量范围
- `include/relay_control.h` / `src/relay_control.c`：保留 18 路 IO 表，但只允许访问当前 env 的有效通道数
- `include/modbus_tcp.h`：`MODBUS_COIL_COUNT` 跟随 `RELAY_CHANNEL_COUNT`

## KSZ8863RLLI 初始化流程

当前程序按推荐流程组织以太网初始化：

1. 初始化 STM32 ETH、RMII GPIO、MDC/MDIO。
2. 复位并释放 KSZ8863RLLI。
3. 通过 MIIM 读取 PHY1/PHY2 ID，确认通信正常。
4. 对 PHY1/PHY2 重启自协商。
5. 将 STM32 MAC 配置为固定 `100M Full Duplex`。
6. 周期读取 PHY link 状态，有链路时启动 ETH DMA，无链路时关闭网络接口。

注意：当前只使用 STM32 ETH HAL 提供的标准 MIIM 读写 PHY 寄存器。KSZ8863RLLI 的 8-bit 全局/端口寄存器，例如 Register 198、Switch Start bit，需要后续接入 SMI/I2C/SPI 寄存器管理接口后才能完整配置。

## 工程结构

| 路径 | 说明 |
|---|---|
| `platformio.ini` | 多板卡 env 和编译宏 |
| `include/app_config.h` | 板卡名称、有效继电器数量配置 |
| `src/main.c` | 主循环，初始化 GPIO、USART、LwIP、Modbus-TCP |
| `src/relay_control.c` | 继电器抽象和 GPIO 控制 |
| `src/modbus_tcp.c` | Modbus-TCP 线圈协议处理 |
| `src/ksz8863rlli.c` | KSZ8863RLLI 驱动 |
| `src/ethernetif.c` | STM32 ETH + LwIP 网卡适配 |
| `src/lwip.c` | LwIP 初始化和静态 IP 配置 |
| `doc/` | 硬件 IO 和 KSZ8863RLLI 文档 |

## 编译

默认 env 为 `DMM_Card`。

```powershell
pio run
```

指定板卡编译：

```powershell
pio run -e DMM_Card
pio run -e HSD_Card
pio run -e HV_Card
pio run -e Relay_Card
```

一次编译全部板卡：

```powershell
pio run -e DMM_Card -e HSD_Card -e HV_Card -e Relay_Card
```

生成文件位于：

```text
.pio/build/<env>/firmware.bin
.pio/build/<env>/firmware.elf
```

## 烧录

```powershell
pio run -e DMM_Card -t upload
pio run -e HSD_Card -t upload
pio run -e HV_Card -t upload
pio run -e Relay_Card -t upload
```

## 串口监视

```powershell
pio device monitor -b 115200
```

启动后串口会打印当前板卡名称、设备 ID 和有效 Modbus 线圈范围。

## 维护说明

- 修改继电器 IO：优先更新 `doc/RN_Relay_Card_IO.md`，再同步 `include/main.h`、`src/gpio.c`、`src/relay_control.c`。
- 新增板卡：在 `platformio.ini` 新增 env，设置板卡类型宏、`RELAY_CHANNEL_COUNT`，必要时同步 `include/app_config.h` 的板卡名称。
- 修改 IP 地址：编辑 `src/lwip.c` 中的 `IP_ADDRESS`、`NETMASK_ADDRESS`、`GATEWAY_ADDRESS`。
- 修改 Modbus 映射：编辑 `include/modbus_tcp.h` 和 `src/modbus_tcp.c`。
- 修改 KSZ8863RLLI 管理流程：优先在 `include/ksz8863rlli.h`、`src/ksz8863rlli.c` 内封装，避免把 PHY 芯片细节散落到业务代码。
