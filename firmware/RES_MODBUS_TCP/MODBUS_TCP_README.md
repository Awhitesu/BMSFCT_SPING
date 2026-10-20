# RN-EOL可编程电阻板卡 - Modbus TCP接口文档

## 概述

本项目实现了基于STM32F407VET6的RN-EOL可编程电阻板卡控制系统，通过继电器阵列实现精确的电阻值编程，并通过Modbus TCP协议提供网络接口。系统可以精确控制电阻值，范围从0Ω到5,000,000Ω (5MΩ，软件限幅)，分辨率为1kΩ。

## 系统特性 ✨ V1.1 新架构

### 硬件特性
- **控制方式**: 18路阻值继电器阵列 + 1路输出隔离继电器 (ULN2803A驱动)
- **电阻范围**: 0Ω - 5,000,000Ω (5MΩ，软件限幅)
- **分辨率**: 1kΩ
- **继电器配置**: 4档位二进制加权 (1kΩ/10kΩ/100kΩ/1MΩ)
- **网络接口**: 以太网 (KSZ8863RLLI PHY, RMII接口)
- **通信协议**: Modbus TCP (端口502)

### 软件特性
- **协议栈**: LwIP TCP/IP
- **最大客户端**: 1个连接（新连接会踢掉旧连接）
- **支持功能码**: 0x03, 0x04, 0x06, 0x10
- **寄存器映射**: 260个寄存器
- **实时更新**: 自动同步输出状态到寄存器
- **贪心算法**: 优化继电器组合，提高精度

## 网络配置

```
默认IP地址: 192.168.1.10
子网掩码: 255.255.255.0
网关: 192.168.1.1
Modbus TCP端口: 502
设备ID: RNEOL_RES_5M
```

## Modbus寄存器映射

### 电阻控制寄存器 (0x0000-0x0001)

虽然保留多通道寄存器结构以保持兼容性，但实际只有通道1有效：

| 寄存器地址 | 描述 | 数据类型 | 单位 | 状态 |
|-----------|------|----------|------|------|
| 0x0000 | CH1电阻值高16位 | UINT16 | ohms | ✅ 有效 |
| 0x0001 | CH1电阻值低16位 | UINT16 | ohms | ✅ 有效 |
| 0x0002-0x000F | CH2-CH8保留 | UINT16 | ohms | ⚠️ 兼容性保留 |

**电阻值计算**: `resistance = (high_reg << 16) | low_reg`

**有效电阻范围**: 0Ω - 5,000,000Ω (1kΩ步进)。写入0Ω时K19保持吸合，输出端断开；写入非0阻值后释放K19并输出设定阻值。

### 系统信息寄存器 (0x0100-0x0109) - 只读

| 寄存器地址 | 描述 | 值 | 说明 |
|-----------|------|-----|------|
| 0x0100 | 系统状态 | 0x0001 | 0x0001=就绪, 0x0000=故障 |
| 0x0101-0x0108 | 设备ID | RNEOL_RES_5M | RN-EOL电阻板卡5M型号(0x0107-0x0108填充0x00) |
| 0x0109 | 固件版本 | 0x0101 | 版本1.1 |

### 网络配置寄存器 (0x0110-0x0116) ✨ 动态配置

| 寄存器地址 | 描述 | 说明 |
|-----------|------|------|
| 0x0110 | IP地址高16位 | IP[0]<<8 \| IP[1] |
| 0x0111 | IP地址低16位 | IP[2]<<8 \| IP[3] |
| 0x0112 | 子网掩码高16位 | 子网掩码高位 |
| 0x0113 | 子网掩码低16位 | 子网掩码低位 |
| 0x0114 | 网关地址高16位 | 网关地址高位 |
| 0x0115 | 网关地址低16位 | 网关地址低位 |
| 0x0116 | 网络配置保存 | 配置保存控制寄存器 |

**网络配置保存寄存器位定义 (0x0116)**:
- 位0: SAVE_CONFIG - 保存网络配置到Flash (写1触发)

### 系统控制寄存器 (0x011F)

| 寄存器地址 | 描述 | 说明 |
|-----------|------|------|
| 0x011F | 软件重启 | 系统重启控制寄存器 |

**软件重启寄存器位定义 (0x011F)**:
- 位0: RESET_TRIGGER - 触发软件重启 (写1立即执行)

## 继电器电阻组合算法

### 电阻档位配置
系统采用4档位二进制加权架构，每档位包含1个基数电阻和4个双倍电阻：

| 档位 | 基数电阻 | 双倍电阻 | 继电器 | 最大组合 |
|------|----------|----------|--------|----------|
| 1kΩ  | 1kΩ      | 2kΩ×4    | K1,K2,K3,K4,K5 | 9kΩ |
| 10kΩ | 10kΩ     | 20kΩ×4   | K6,K7,K8,K9,K10 | 90kΩ |
| 100kΩ| 100kΩ    | 200kΩ×4  | K11,K12,K13,K14,K15 | 900kΩ |
| 1MΩ  | 1MΩ      | 2MΩ×2    | K16,K17,K18 | 5MΩ |

**理论组合总阻值**: 9kΩ + 90kΩ + 900kΩ + 5MΩ = 5.999MΩ（软件限幅到 5MΩ）
**输出隔离**: K19(PC12)为常闭点控制，不参与阻值计算；上电默认吸合以断开0Ω输出。

### 贪心算法优化
系统使用贪心算法计算最优继电器组合：
1. 从大到小遍历K1-K18阻值继电器
2. 如果继电器阻值 ≤ 剩余目标阻值，则启用该继电器
3. 从目标阻值中减去已选继电器阻值
4. 重复直到剩余阻值为0或无法继续组合

## 支持的Modbus功能

### 0x03 - 读取保持寄存器
读取连续的寄存器值，最大125个寄存器。

**请求格式**:
```
功能码: 0x03
起始地址: 2字节
数量: 2字节
```

**示例 - 读取CH1电阻值**:
```
地址: 0x0000, 数量: 2
响应: [高16位] [低16位]
计算: resistance = (响应[0] << 16) | 响应[1]
```

### 0x04 - 读取输入寄存器
与0x03功能相同，用于只读寄存器访问。

### 0x06 - 写入单个寄存器
修改单个寄存器的值。

**请求格式**:
```
功能码: 0x06
寄存器地址: 2字节
寄存器值: 2字节
```

**示例 - 设置CH1电阻值为123000Ω**:
```
# 计算寄存器值
target = 123000
high_value = target >> 16 = 0x0001  # 高16位
low_value = target & 0xFFFF = 0xE040  # 低16位

# 写入寄存器
地址: 0x0000, 值: 0x0001 (高16位)
地址: 0x0001, 值: 0xE040 (低16位)
```

### 0x10 - 写入多个寄存器
批量修改多个连续寄存器。

**请求格式**:
```
功能码: 0x10
起始地址: 2字节
数量: 2字节
字节数: 1字节
数据: N字节
```

**示例 - 批量设置电阻值**:
```
地址: 0x0000, 数量: 4, 字节数: 8
数据: [高16位] [低16位] [下一通道高16位] [下一通道低16位]
```

## 使用示例

### Python客户端示例

```python
from pymodbus.client.tcp import ModbusTcpClient
import time

# 连接到RN-EOL设备
client = ModbusTcpClient('192.168.1.10', port=502)
if not client.connect():
    print("连接失败")
    exit(1)

print("✅ 连接成功到RN-EOL设备")

# 读取设备信息
device_info = client.read_holding_registers(0x0100, 9, unit=1)
if not device_info.isError():
    print(f"系统状态: 0x{device_info.registers[0]:04X}")

    # 组合设备ID (8个寄存器，但只使用前6个有数据的寄存器)
    device_id = ""
    for i in range(8):
        reg_value = device_info.registers[1 + i]
        if reg_value != 0:  # 只处理非零寄存器
            device_id += chr(reg_value >> 8) + chr(reg_value & 0xFF)
    print(f"设备ID: {device_id}")

    print(f"固件版本: v{device_info.registers[8]>>8}.{device_info.registers[8]&0xFF}")

# 读取当前电阻值
current_res = client.read_holding_registers(0x0000, 2, unit=1)
if not current_res.isError():
    high = current_res.registers[0]
    low = current_res.registers[1]
    resistance = (high << 16) | low
    print(f"当前电阻值: {resistance:,} Ω")

# 设置电阻值为示例值
test_values = [1000, 10000, 123000, 1000000, 5000000]  # 1k, 10k, 123k, 1M, 5M

for target in test_values:
    print(f"\n🎯 设置目标电阻值: {target:,} Ω")

    # 计算寄存器值
    high_value = (target >> 16) & 0xFFFF
    low_value = target & 0xFFFF

    # 写入寄存器
    result1 = client.write_register(0x0000, high_value, unit=1)
    result2 = client.write_register(0x0001, low_value, unit=1)

    if result1.isError() or result2.isError():
        print("❌ 写入失败")
        continue

    # 等待硬件响应
    time.sleep(0.1)

    # 读取实际值
    actual_res = client.read_holding_registers(0x0000, 2, unit=1)
    if not actual_res.isError():
        actual = (actual_res.registers[0] << 16) | actual_res.registers[1]
        error = actual - target
        print(f"✅ 实际电阻值: {actual:,} Ω (误差: {error:,} Ω)")

client.close()
print("🔚 连接已关闭")
```

### 动态网络配置示例

```python
# 修改IP地址为 192.168.1.100
client.write_register(0x0110, 0xC0A8)  # 192.168 (高16位)
client.write_register(0x0111, 0x0164)  # 1.100 (低16位)

# 保存网络配置到Flash
client.write_register(0x0116, 0x0001)  # SAVE_CONFIG

print("✅ 网络配置已保存")

# 软件重启设备
client.write_register(0x011F, 0x0001)  # RESET_TRIGGER
print("🔄 设备即将重启...")
```

### C# 客户端示例

```csharp
using EasyModbus;

ModbusClient client = new ModbusClient("192.168.1.10", 502);
client.Connect();

// 读取当前电阻值
int[] values = client.ReadHoldingRegisters(0, 2);
uint resistance = ((uint)values[0] << 16) | (uint)values[1];
Console.WriteLine($"当前电阻值: {resistance:N0} Ω");

// 设置电阻值为2,345,000Ω
uint target = 2345000;
client.WriteSingleRegister(0, (int)(target >> 16));    // 高16位
client.WriteSingleRegister(1, (int)(target & 0xFFFF)); // 低16位

// 读取设备信息
int[] deviceInfo = client.ReadHoldingRegisters(0x0100, 9);

// 组合设备ID (8个寄存器，但只使用前6个有数据的寄存器)
string deviceId = "";
for (int i = 0; i < 8; i++) {
    int regValue = deviceInfo[1 + i];
    if (regValue != 0)  // 只处理非零寄存器
    {
        deviceId += Convert.ToChar(regValue >> 8);
        deviceId += Convert.ToChar(regValue & 0xFF);
    }
}
Console.WriteLine($"设备ID: {deviceId}");

Console.WriteLine($"固件版本: v{deviceInfo[8] >> 8}.{deviceInfo[8] & 0xFF}");

client.Disconnect();
```

## 错误处理

系统支持标准Modbus异常响应：

| 异常码 | 说明 | 常见原因 |
|-------|------|----------|
| 0x01 | 非法功能码 | 不支持的功能码 |
| 0x02 | 非法数据地址 | 寄存器地址超出范围 |
| 0x03 | 非法数据值 | 电阻值超出有效范围 (0-5,000,000Ω) |
| 0x04 | 从设备故障 | 继电器控制错误或硬件故障 |

## 性能指标 ✨ 优化提升

| 指标 | 值 | 说明 |
|------|-----|------|
| 响应时间 | < 20ms | 包含继电器动作时间 |
| 并发连接 | 最多4个客户端 | TCP连接池管理 |
| 操作频率 | > 50次/秒 | 稳定状态下的测试值 |
| 电阻精度 | ±1kΩ | 受继电器组合算法限制 |
| 继电器寿命 | > 100万次 | ULN2803A驱动优化 |
| 网络延迟 | < 5ms | 局域网环境 |

## 故障排除

### 连接问题
1. **检查网络连接**: 确认IP配置和网络链路
2. **防火墙设置**: 允许502端口通信
3. **PHY状态**: 检查以太网链路LED指示
4. **设备重启**: 软件重启或硬件重启设备

### 通信错误
1. **寄存器地址**: 确认在有效范围内 (0x0000-0x01FF)
2. **电阻值范围**: 确认在0-5,000,000Ω范围内
3. **功能码**: 只支持0x03, 0x04, 0x06, 0x10
4. **字节序**: 确认大端字节序格式

### 硬件问题
1. **继电器故障**: 检查ULN2803A驱动和继电器线圈
2. **电源供应**: 确认24V继电器电源正常
3. **GPIO配置**: 验证继电器控制引脚配置
4. **串口调试**: 查看115200波特率的详细错误信息

### 电阻精度问题
1. **算法限制**: 1kΩ分辨率是物理限制
2. **继电器响应**: 允许20ms的继电器稳定时间
3. **读取验证**: 设置后等待100ms再读取确认
4. **误差预期**: 最大误差不超过1kΩ

## 测试验证

### 基本功能测试
```bash
# 测试网络连接
ping 192.168.1.10

# 测试Modbus TCP端口
telnet 192.168.1.10 502

# 安装Python测试依赖
pip install pymodbus

# 运行功能测试脚本
python test_resistor_modbus.py
```

### 性能基准测试
- **连接建立**: < 100ms
- **寄存器读取**: < 10ms
- **寄存器写入**: < 20ms (含继电器动作)
- **并发处理**: 支持4客户端同时操作
- **长期稳定性**: 24小时连续运行测试

### 电阻精度验证
推荐测试阻值：
- **小值**: 1kΩ, 2kΩ, 5kΩ
- **中值**: 100kΩ, 500kΩ, 1MΩ
- **大值**: 2MΩ, 3.5MΩ, 5MΩ
- **边界值**: 0Ω, 5,000,000Ω

## 开发说明

### 扩展电阻范围
如需扩展电阻范围，需要：
1. 修改 `relay_control.c` 中的继电器映射表
2. 更新 `relay_control.h` 中的电阻值定义
3. 调整 `Resistor_New_GetMaxResolution()` 函数
4. 重新验证贪心算法

### 添加新功能码
在 `src/modbus_tcp.c` 的 `Modbus_Process_Request` 函数中添加新的case分支。

### 优化性能建议
- 调整TCP缓冲区大小
- 优化继电器批量操作
- 减少不必要的寄存器同步频率
- 考虑增加本地缓存

## 技术支持

### 调试信息获取
1. **串口输出**: 连接COM3, 115200波特率查看实时日志
2. **系统状态**: 读取寄存器0x0100-0x0109获取设备状态
3. **继电器状态**: 系统启动时自动打印继电器配置信息
4. **网络诊断**: 使用ping和telnet命令验证网络连接

### 常见问题解答

**Q: 为什么实际电阻值与目标值有误差？**
A: 这是由于1kΩ分辨率和贪心算法的限制，最大误差不超过1kΩ。

**Q: 支持Modbus RTU吗？**
A: 当前版本仅支持Modbus TCP，暂不支持RTU。

**Q: 如何恢复出厂网络设置？**
A: 写入默认网络参数到配置寄存器，然后调用保存功能即可。

**Q: 如何通过Modbus重启设备？**
A: 写入0x0001到寄存器0x011F即可触发软件重启，系统会先发送响应再执行重启。

---

**版本**: v1.1 (RN-EOL)
**最后更新**: 2025年8月
**硬件平台**: RN-EOL V1.1 继电器控制板卡
**兼容性**: STM32F407VET6, LwIP 2.x, Modbus TCP标准
