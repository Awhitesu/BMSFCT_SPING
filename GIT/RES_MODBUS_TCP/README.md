# RN-EOL 可编程（绝缘）电阻板卡系统

基于STM32F407VET6的RN-EOL可编程电阻板卡控制系统，支持通过继电器阵列实现精确的电阻值编程和Modbus TCP远程控制。

## 系统概览

### 硬件平台 ✨ V1.1 新架构
- **微控制器**: STM32F407VET6 (168MHz, 512KB Flash, 128KB RAM)
- **硬件版本**: RN-EOL V1.1 继电器控制板卡
- **网络芯片**: KSZ8863RLLI 以太网交换芯片 (RMII接口，仅支持该型号)
- **继电器驱动**: ULN2803A 达林顿管阵列
- **控制方式**: 18路阻值继电器 + 1路输出隔离继电器直接GPIO控制
- **调试接口**: CMSIS-DAP (SWD)

### 电阻控制规格
- **通道数**: 1路可编程电阻控制 (兼容多通道寄存器结构)
- **阻值范围**: 0Ω - 5,000,000Ω (5MΩ，软件限幅)
- **分辨率**: 1,000Ω (1kΩ)
- **继电器数量**: 19个 (K1-K18阻值继电器 + K19输出隔离)
- **控制逻辑**: GPIO高电平 → 继电器吸合 → 电阻接入电路
- **输出隔离**: K19(PC12)使用常闭点，上电默认吸合断开0Ω输出；写入非0阻值后释放K19输出阻值
- **有效电阻计算**: `resistance = (high_reg << 16) | low_reg`

### 继电器电阻配置（与EOL硬件表一致）
| 继电器 | 阻值 | 档位 | GPIO引脚 |
|--------|------|------|----------|
| K1     | 1kΩ  | 1k基数 | PE13 |
| K2-K5  | 2kΩ  | 1k组合 | PE12, PE11, PE10, PE9 |
| K6     | 10kΩ | 10k基数 | PE8 |
| K7-K10 | 20kΩ | 10k组合 | PB6, PB5, PB4, PB3 |
| K11    | 100kΩ| 100k基数| PD7 |
| K12-K15| 200kΩ| 100k组合| PD6, PD5, PD4, PD3 |
| K16    | 1MΩ  | 1M基数 | PD2 |
| K17-K18| 2MΩ  | 1M组合 | PD1, PD0 |
| K19    | 输出隔离 | 常闭点 | PC12 |

### 网络通信
- **协议**: Modbus TCP (端口502)
- **默认IP地址**: 192.168.1.10 (可动态配置)
- **网络配置**: 支持通过Modbus TCP动态设置IP/网关/子网掩码
- **配置存储**: Flash存储，断电保持，支持软件重启
- **最大客户端**: 1个连接（新连接会踢掉旧连接）
- **设备ID**: RNEOL_RES_5M

## 快速开始

### 编译和上传
```bash
# 安装PlatformIO
pip install platformio

# 编译项目
pio run

# 上传到目标板
pio run --target upload

# 监视串口输出 (COM3 @ 115200)
pio run --target monitor
```

### 网络测试
```bash
# 测试网络连接
ping 192.168.1.10

# 测试Modbus TCP连接
telnet 192.168.1.10 502
```

## Modbus TCP寄存器映射

### 电阻控制寄存器 (0x0000-0x0001)
| 地址 | 功能 | 描述 |
|------|------|------|
| 0x0000 | CH1电阻值高16位 | 电阻值高位 |
| 0x0001 | CH1电阻值低16位 | 电阻值低位 |
| 0x0002-0x000F | CH2-CH8保留 | 兼容性保留 |

**电阻值计算**: `resistance = (high_reg << 16) | low_reg`

### 系统信息寄存器 (0x0100-0x0109) - 只读
| 地址 | 功能 | 值 |
|------|------|-----|
| 0x0100 | 系统状态 | 0x0001 (就绪) |
| 0x0101-0x0108 | 设备ID | "RNEOL_RES_5M" (13字符，每寄存器存2字符) |
| 0x0109 | 固件版本 | 0x0101 (v1.1) |

### 网络配置寄存器 (0x0110-0x0116)
| 地址 | 功能 | 描述 |
|------|------|------|
| 0x0110 | IP地址高16位 | IP[0]<<8 \| IP[1] |
| 0x0111 | IP地址低16位 | IP[2]<<8 \| IP[3] |
| 0x0112 | 子网掩码高16位 | 网络掩码高位 |
| 0x0113 | 子网掩码低16位 | 网络掩码低位 |
| 0x0114 | 网关地址高16位 | 网关地址高位 |
| 0x0115 | 网关地址低16位 | 网关地址低位 |
| 0x0116 | 网络配置保存 | 保存配置到Flash |

### 系统控制寄存器 (0x011F)
| 地址 | 功能 | 描述 |
|------|------|------|
| 0x011F | 软件重启 | 系统重启控制寄存器 |

## 支持的Modbus功能码

- **0x03**: 读取保持寄存器
- **0x04**: 读取输入寄存器
- **0x06**: 写入单个寄存器
- **0x10**: 写入多个寄存器

## 核心功能特性

### 🔧 继电器控制系统 ✨ 新架构
- 18个阻值继电器精密电阻阵列控制，K19负责输出隔离
- 0Ω到5MΩ范围，1kΩ精度
- 贪心算法优化电阻组合
- ULN2803A达林顿管驱动
- 实时继电器状态监控

### 🌐 动态网络配置
- **动态IP设置**: 通过Modbus TCP实时修改IP地址、子网掩码、网关
- **配置持久化**: 手动保存到Flash，断电保持
- **软件重启**: 支持软件重启应用新配置
- **参数验证**: 自动验证网络参数合法性
- **简化控制**: 独立的配置保存和重启寄存器

### 💡 智能电阻控制
- **二进制加权**: 每档位采用1+4×2的配置实现0-9系数
- **贪心算法**: 自动计算最优继电器组合
- **误差补偿**: 显示实际阻值与目标值的差异
- **安全模式**: 上电默认K19吸合，断开0Ω输出；非0阻值写入后才输出

## 电阻组合算法

### 1kΩ档位组合 (9kΩ总量)
- 1kΩ: K1
- 2kΩ: K2, K3, K4, K5
- 最大组合: 1k + 2k + 2k + 2k + 2k = 9kΩ

### 10kΩ档位组合 (90kΩ总量)
- 10kΩ: K6
- 20kΩ: K7, K8, K9, K10
- 最大组合: 10k + 20k + 20k + 20k + 20k = 90kΩ

### 100kΩ档位组合 (900kΩ总量)
- 100kΩ: K11
- 200kΩ: K12, K13, K14, K15
- 最大组合: 100k + 200k + 200k + 200k + 200k = 900kΩ

### 1MΩ档位组合 (5MΩ总量)
- 1MΩ: K16
- 2MΩ: K17, K18
- 最大组合: 1M + 2M + 2M = 5MΩ（整体输出限幅 5MΩ）

**总计范围**: 0Ω - 5MΩ（软件限幅，防止超出硬件标称范围）

## 项目结构

```
├── src/                    # 源代码
│   ├── main.c             # 主程序
│   ├── modbus_tcp.c       # Modbus TCP服务器
│   ├── network_config.c   # 网络配置管理
│   ├── relay_control.c    # ✨ 继电器控制系统
│   ├── resistor_control_new.c # ✨ 新电阻控制接口
│   ├── ksz8863rlli.c          # 以太网PHY驱动（支持KSZ8863RLLI）
│   └── ...                # 其他源文件
├── include/               # 头文件
│   ├── relay_control.h    # ✨ 继电器控制接口
│   ├── resistor_control_new.h # ✨ 新电阻控制头文件
│   ├── modbus_tcp.h       # Modbus TCP协议定义
│   ├── ksz8863rlli.h          # PHY驱动头文件（含KSZ8863RLLI扩展定义）
│   └── ...                # 其他头文件
└── lib/LwIP/             # LwIP TCP/IP协议栈
```

## 性能指标

| 指标 | 值 |
|------|-----|
| Flash使用 | 55KB / 512KB (10.6%) |
| RAM使用 | 63KB / 128KB (49.4%) |
| 网络延迟 | < 5ms |
| Modbus响应时间 | < 100ms |
| 最大吞吐量 | > 50 操作/秒 |
| 继电器响应时间 | < 20ms |
| 电阻精度 | ±1kΩ |

## 快速使用示例

### 电阻控制
```python
from pymodbus.client.sync import ModbusTcpClient

client = ModbusTcpClient('192.168.1.10', port=502)
client.connect()

# 设置电阻为123000欧姆 (123kΩ)
resistance = 123000
client.write_register(0x0000, resistance >> 16)      # 高16位
client.write_register(0x0001, resistance & 0xFFFF)   # 低16位

# 读取当前阻值
result = client.read_holding_registers(0x0000, 2)
current_resistance = (result.registers[0] << 16) | result.registers[1]
print(f"当前阻值: {current_resistance}Ω")
```

### 动态网络配置
```python
# 设置新的IP地址为 192.168.1.50
client.write_register(0x0110, 0xC0A8)  # 192.168 (高16位)
client.write_register(0x0111, 0x0132)  # 1.50 (低16位)

# 保存网络配置到Flash
client.write_register(0x0116, 0x0001)  # SAVE_CONFIG

# 软件重启设备使新配置生效
client.write_register(0x011F, 0x0001)  # RESET_TRIGGER

# 设备将重启并使用新IP地址 192.168.1.50
```

## 硬件连接说明

### 以太网接口 (RMII)
**PHY芯片**: KSZ8863RLLI (固定支持该型号)

| 功能 | MCU引脚 | 说明 |
|------|---------|------|
| REF_CLK | PA1 | 50MHz参考时钟 |
| MDIO | PA2 | 管理数据接口 (SMI总线数据) |
| MDC | PC1 | 管理时钟 (SMI总线时钟) |
| CRS_DV | PA7 | 载波侦听/数据有效 |
| RXD0 | PC4 | 接收数据位0 |
| RXD1 | PC5 | 接收数据位1 |
| TX_EN | PB11 | 发送使能 |
| TXD0 | PB12 | 发送数据位0 |
| TXD1 | PB13 | 发送数据位1 |
| RESET | PA3 | PHY硬件复位引脚（低电平有效） |

**PHY驱动特性**:
- 仅支持KSZ8863RLLI
- 通过MIIM访问KSZ8863RLLI端口PHY寄存器
- 固定配置MAC为100Mbps全双工

### 串口调试接口
- **TX**: PA9
- **RX**: PA10
- **波特率**: 115200

## 功能验证

### 基本连接测试
```bash
# 测试设备网络连接
ping 192.168.1.10

# 测试Modbus TCP端口
telnet 192.168.1.10 502
```

### 继电器控制测试
```python
from pymodbus.client.sync import ModbusTcpClient

client = ModbusTcpClient('192.168.1.10', port=502)
client.connect()

# 测试基本电阻值设置
test_values = [1000, 10000, 100000, 1000000]  # 1k, 10k, 100k, 1M

for value in test_values:
    client.write_register(0x0000, value >> 16)
    client.write_register(0x0001, value & 0xFFFF)

    # 读取实际值
    result = client.read_holding_registers(0x0000, 2)
    actual = (result.registers[0] << 16) | result.registers[1]
    print(f"目标: {value}Ω, 实际: {actual}Ω, 误差: {actual-value}Ω")

client.close()
```

## 开发环境

- **IDE**: PlatformIO
- **框架**: STM32Cube HAL
- **编译器**: GCC ARM None EABI
- **调试工具**: CMSIS-DAP
- **目标板**: Black F407ZE (兼容RN-EOL V1.1)

## 版本历史

### v1.0 (2026-01) - RN-EOL继电器控制版本 ✨
- ✅ 完全重构为继电器控制架构
- ✅ 支持18继电器精密电阻阵列
- ✅ 阻值范围支持0-5MΩ，分辨率1kΩ
- ✅ 新增贪心算法优化电阻组合
- ✅ 移除I²C依赖，改为GPIO直接控制
- ✅ 更新硬件配置为RN-EOL V1.1板卡
- ✅ 保持Modbus TCP协议兼容性
- ✅ 优化网络配置和固件升级功能
- ✅ 更新设备ID为RNEOL_RES_5M (13字符)
- ✅ 新增网络配置保存和软件重启功能
- ✅ KSZ8863RLLI PHY完整支持
- ✅ 固定链路配置（100Mbps全双工）
- ✅ PA3硬件复位、PC11状态LED

## 硬件文档

- 📗 **[RN-EOL板卡开发手册](EOL.md)** - 完整的硬件设计文档
- 📘 **[网络配置详细指南](NETWORK_CONFIG_README.md)** - 网络配置功能说明
- 📙 **[Modbus TCP用户手册](MODBUS_TCP_README.md)** - Modbus协议使用指南
- 📔 **[开发者指南](CLAUDE.md)** - 系统架构和开发说明

## 版权信息

Copyright (c) 2025 RN-EOL项目组。
本项目基于STM32Cube HAL库开发，专为EOL测试场景设计。
