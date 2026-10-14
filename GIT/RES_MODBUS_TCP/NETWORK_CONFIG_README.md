# 网络配置功能说明

本文档说明了如何使用Modbus TCP协议动态设置设备的IP地址、子网掩码和网关地址。

## 功能概述

新增的网络配置功能允许用户通过Modbus TCP协议动态修改设备的网络参数，包括：
- IP地址
- 子网掩码  
- 网关地址

配置参数会自动保存到设备内部Flash存储器中，上电后自动加载保存的配置。

## Modbus寄存器映射

### 网络配置寄存器 (0x0110-0x0116)

| 寄存器地址 | 名称 | 类型 | 描述 |
|-----------|------|------|------|
| 0x0110 | IP_ADDR_HIGH | R/W | IP地址高16位 (IP[0]<<8 \| IP[1]) |
| 0x0111 | IP_ADDR_LOW | R/W | IP地址低16位 (IP[2]<<8 \| IP[3]) |
| 0x0112 | NETMASK_HIGH | R/W | 子网掩码高16位 |
| 0x0113 | NETMASK_LOW | R/W | 子网掩码低16位 |
| 0x0114 | GATEWAY_HIGH | R/W | 网关地址高16位 |
| 0x0115 | GATEWAY_LOW | R/W | 网关地址低16位 |
| 0x0116 | NETWORK_CONTROL | R/W | 网络控制寄存器 |

### 网络控制寄存器位定义 (0x0116)

| 位 | 名称 | 描述 |
|----|------|------|
| 0 | APPLY_CONFIG | 应用网络配置 (写1触发，自动清零) |
| 1 | RESTORE_DEFAULTS | 恢复默认配置 (写1触发，自动清零) |
| 8 | REBOOT_REQUIRED | 需要重启标志 (只读) |
| 12 | SOFTWARE_RESET | 软件重启 (写1触发，立即重启) |

## 使用方法

### 1. 读取当前网络配置

使用Modbus功能码 0x03 (读取保持寄存器) 读取寄存器 0x0110-0x0115：

```python
# 读取IP地址
result = client.read_holding_registers(0x0110, 2, unit=1)
ip_high, ip_low = result.registers
ip_address = f"{ip_high>>8}.{ip_high&0xFF}.{ip_low>>8}.{ip_low&0xFF}"

# 读取子网掩码
result = client.read_holding_registers(0x0112, 2, unit=1)
nm_high, nm_low = result.registers
netmask = f"{nm_high>>8}.{nm_high&0xFF}.{nm_low>>8}.{nm_low&0xFF}"

# 读取网关地址
result = client.read_holding_registers(0x0114, 2, unit=1)
gw_high, gw_low = result.registers
gateway = f"{gw_high>>8}.{gw_high&0xFF}.{gw_low>>8}.{gw_low&0xFF}"
```

### 2. 设置新的网络配置

#### 步骤1：写入网络参数

使用Modbus功能码 0x06 (写入单个寄存器) 或 0x10 (写入多个寄存器)：

```python
# 设置IP地址为 192.168.1.20
ip_parts = [192, 168, 1, 20]
ip_high = (ip_parts[0] << 8) | ip_parts[1]  # 0xC0A8
ip_low = (ip_parts[2] << 8) | ip_parts[3]   # 0x0114

client.write_register(0x0110, ip_high, unit=1)
client.write_register(0x0111, ip_low, unit=1)

# 设置子网掩码为 255.255.255.0
nm_parts = [255, 255, 255, 0]
nm_high = (nm_parts[0] << 8) | nm_parts[1]  # 0xFFFF
nm_low = (nm_parts[2] << 8) | nm_parts[3]   # 0xFF00

client.write_register(0x0112, nm_high, unit=1)
client.write_register(0x0113, nm_low, unit=1)

# 设置网关地址为 192.168.1.1
gw_parts = [192, 168, 1, 1]
gw_high = (gw_parts[0] << 8) | gw_parts[1]  # 0xC0A8
gw_low = (gw_parts[2] << 8) | gw_parts[3]   # 0x0101

client.write_register(0x0114, gw_high, unit=1)
client.write_register(0x0115, gw_low, unit=1)
```

#### 步骤2：应用配置

写入网络控制寄存器应用配置：

```python
# 应用配置
client.write_register(0x0116, 0x0001, unit=1)  # APPLY_CONFIG位

# 检查是否需要重启
result = client.read_holding_registers(0x0116, 1, unit=1)
control = result.registers[0]
if control & 0x0100:  # REBOOT_REQUIRED位
    print("需要重启设备应用新配置")
```

### 3. 恢复默认配置

```python
# 恢复默认配置
client.write_register(0x0116, 0x0002, unit=1)  # RESTORE_DEFAULTS位
```

### 4. 软件重启

```python
# 软件重启 - 立即生效，无需手动重启
client.write_register(0x0116, 0x1000, unit=1)  # SOFTWARE_RESET位

# 注意: 此命令发送后设备将立即重启
# 需要等待设备重启完成后重新连接
```

**重要说明**:
- 软件重启会立即生效，设备将重启并应用新的网络配置
- 发送重启命令后连接会断开，需要等待设备重启完成
- 如果修改了IP地址，需要使用新IP地址重新连接

## 默认网络配置

- **IP地址**: 192.168.1.10
- **子网掩码**: 255.255.255.0  
- **网关地址**: 192.168.1.1

## 重要注意事项

1. **重启要求**: 由于LwIP网络协议栈的限制，新的网络配置需要重启设备才能完全生效。可以使用软件重启功能(0x1000)无需手动重启。
2. **配置保存**: 所有网络配置都会自动保存到内部Flash存储器中，断电不丢失。
3. **参数验证**: 系统会自动验证IP地址和子网掩码的合法性。
4. **Flash扇区**: 网络配置使用Flash扇区11 (0x080E0000)，大小128KB。
5. **备份恢复**: 如果Flash中的配置损坏，系统会自动使用默认配置。

## 测试脚本

项目包含两个Python测试脚本：

### 1. 完整功能测试 (`test/test_network_config.py`)

```bash
# 安装依赖
pip install pymodbus

# 运行完整测试
python test/test_network_config.py
```

测试脚本会执行以下操作：
1. 读取当前网络配置
2. 设置新的IP地址
3. 验证配置是否正确保存
4. 恢复默认配置
5. 验证默认配置恢复

### 2. 简化寄存器测试 (`test/simple_ip_test.py`)

用于快速验证寄存器读写功能：

```bash
# 运行简化测试
python test/simple_ip_test.py
```

这个测试专门验证：
- 写入IP地址寄存器后立即读取是否正确
- 多次写入不同IP地址的一致性
- 寄存器值的正确性

**使用场景**: 当发现"IP地址设置后，读取还是原来的值"时，先运行此测试进行快速诊断。

### 3. 控制寄存器测试 (`test/test_control_register.py`)

专门测试0x0116网络控制寄存器的功能：

```bash
# 运行控制寄存器测试
python test/test_control_register.py
```

这个测试验证：
- APPLY_CONFIG位的正确处理 (写入1后应该变成0x0100)
- REBOOT_REQUIRED位的设置 (位8应该被设置)
- RESTORE_DEFAULTS功能的正确性
- 各控制位和状态位的详细分析

**使用场景**: 当发现"0x0116写入1后变成0"的问题时使用。

### 4. 软件重启测试 (`test/test_software_reset.py`)

专门测试软件重启功能：

```bash
# 运行软件重启测试
python test/test_software_reset.py
```

这个测试验证：
- 软件重启命令的发送 (写入0x1000)
- 设备重启过程的监控
- 重启后的重新连接验证
- 设备状态的完整性检查

**警告**: 此测试会实际重启设备，请谨慎使用!

## 故障排除

### 常见问题

1. **IP地址设置后，读取还是原来的值**
   - **原因**: 早期版本中寄存器更新逻辑有问题
   - **解决方案**: 已在当前版本修复，寄存器写入后不会被Flash配置覆盖
   - **验证**: 运行 `python test/simple_ip_test.py` 验证修复效果
   - **注意**: 只有执行APPLY_CONFIG操作后，配置才会保存到Flash

2. **0x0116写入1后变成0，不是预期的0x0100**
   - **原因**: 控制寄存器位清除逻辑错误，误清除了REBOOT_REQUIRED位
   - **解决方案**: 修复位操作逻辑，只清除触发位(0,1)，保留状态位(8)
   - **正确行为**: 写入0x0001 → 处理后变成0x0100 (设置REBOOT_REQUIRED位)
   - **验证**: 运行 `python test/test_control_register.py` 验证修复

3. **无法连接设备**
   - 检查网络连接
   - 确认设备IP地址
   - 检查防火墙设置

4. **配置保存失败**
   - 检查Flash操作权限
   - 确认参数合法性
   - 查看串口调试信息

5. **新配置不生效**
   - 确认已应用配置 (APPLY_CONFIG)
   - 检查是否需要重启设备
   - 验证参数格式正确

### 调试信息

通过串口 (UART1, 115200波特率) 可以查看网络配置相关的调试信息和错误报告。

## 版本信息

- **功能版本**: v1.1
- **固件版本寄存器**: 0x0102 = 0x0101 (增加了网络配置功能)
- **兼容性**: 向下兼容，不影响现有电阻控制功能
