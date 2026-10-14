# 以太网排查与修复指南 (YT8512C PHY)

## 修复内容总结

本次针对YT8512C PHY芯片的以太网问题进行了以下关键修复：

### 🔧 1. 修复扩展寄存器访问方法 (Critical Fix)

**问题**: 原代码错误地使用MMD访问方法（Reg 0x0D/0x0E）访问YT8512C扩展寄存器。

**修复**: 根据YT8512C数据表，正确使用 **Reg 0x1E/0x1F 间接访问方法**：
- **Reg 0x1E (Debug Addr Offset)**: 写入扩展寄存器地址
- **Reg 0x1F (Debug Data)**: 读/写数据

**修改文件**: `src/lan8742.c`
- `LAN8742_ReadExtReg()` - 修改为正确的访问方法
- `LAN8742_WriteExtReg()` - 修改为正确的访问方法

### 🔧 2. 添加RMII接口配置 (Critical Fix)

**问题**: 代码缺少YT8512C的RMII模式配置，芯片可能默认工作在MII模式。

**修复**: 在`LAN8742_ConfigureYT8512C()`中添加：
1. **配置EXT_PAD_CTRL (0x4001) 寄存器**:
   - Bit[1] = 1: 启用RMII模式
   - Bit[0] = 0: 时钟输入模式（外部25MHz晶振）
   - Bits[5:2] = 0xA: 配置中高驱动强度以提高信号完整性

2. **配置LED指示**:
   - LED0 (0x40C0): Link/Activity模式
   - LED1 (0x40C3): Speed模式（100M时亮，10M时灭）

**修改文件**: `src/lan8742.c:821-900`

### 🔧 3. 添加SYSCFG以太网配置 (Critical for STM32F4)

**问题**: STM32F4的以太网外设需要通过SYSCFG寄存器明确选择RMII模式。

**修复**: 在`HAL_ETH_MspInit()`中添加：
```c
__HAL_RCC_SYSCFG_CLK_ENABLE();
SYSCFG->PMC |= SYSCFG_PMC_MII_RMII_SEL;  // 选择RMII模式
```

**修改文件**: `src/ethernetif.c:533-538`

### 🔧 4. 改进PHY地址检测算法

**问题**: 原检测方法使用PHYSCSR寄存器的地址字段，可能不适用于所有PHY芯片。

**修复**: 改为通过读取PHY ID寄存器（Reg 0x02）检测有效PHY：
- 遍历所有PHY地址 (0-31)
- 读取PHY_ID1寄存器
- 检查是否为有效值（非0x0000或0xFFFF）

**修改文件**: `src/lan8742.c:101-117`

### 🔧 5. 优化初始化流程和调试输出

**新增**: 详细的串口调试输出，帮助诊断问题：
- PHY地址检测过程
- PHY软复位状态
- PHY类型识别（YT8512C/LAN8742/未知）
- YT8512C配置步骤
- 自协商启动确认

**修改文件**: `src/lan8742.c` - 添加 `#include <stdio.h>` 和多个 `printf()` 调试输出

---

## 硬件配置验证清单

### RMII接口引脚连接（根据代码）

| 功能 | STM32引脚 | YT8512C引脚 | 说明 |
|------|-----------|-------------|------|
| REF_CLK | PA1 | RMII_CLK_OUT | 50MHz参考时钟（YT8512C输出） |
| MDIO | PA2 | MDIO | 管理数据 |
| MDC | PC1 | MDC | 管理时钟 |
| CRS_DV | PA7 | RMII_CRS_DV | 载波侦听/数据有效 |
| RXD0 | PC4 | RMII_RXD0 | 接收数据位0 |
| RXD1 | PC5 | RMII_RXD1 | 接收数据位1 |
| TX_EN | PB11 | RMII_TX_EN | 发送使能 |
| TXD0 | PB12 | RMII_TXD0 | 发送数据位0 |
| TXD1 | PB13 | RMII_TXD1 | 发送数据位1 |
| RESET | PD8 | PHY_RST# | PHY硬件复位（低电平有效） |

### 关键硬件要求

1. **时钟源**: YT8512C需要外部25MHz晶振
2. **上拉电阻**: MDIO信号线需要4.7kΩ-10kΩ上拉电阻
3. **复位时序**: PD8复位引脚需要至少50ms的低电平脉冲
4. **电源**: YT8512C需要稳定的3.3V或1.8V供电（根据型号）

---

## 测试步骤

### 第1步: 烧录固件并观察串口输出

使用以下命令编译并烧录固件：

```bash
pio run --target upload
```

连接串口（COM3, 115200 baud），观察初始化输出：

**预期输出示例**:
```
正在检测PHY地址...
检测到PHY芯片，地址=0x00, PHY_ID1=0x0000
执行PHY软复位...
PHY软复位完成
等待PHY初始化稳定 (2秒)...
禁用PHY掉电模式...
检测PHY芯片型号...
检测到YT8512C PHY芯片
配置YT8512C专用寄存器...
YT8512C配置完成
启动PHY自协商...
PHY自协商已启动
PHY初始化完成
```

### 第2步: 检查网络链路状态

观察串口输出中的链路状态消息：

**链路连接成功**:
```
网络链路恢复，速度=100Mbps, 双工=全双工
以太网接口重启成功
```

**链路未连接**:
```
等待网络链路建立...
警告: 网络链路未建立，但继续初始化
```

### 第3步: Ping测试

从PC端ping设备IP地址（默认192.168.1.10）：

```bash
ping 192.168.1.10
```

**预期结果**: 应收到正常的ping响应（延迟通常<1ms）

### 第4步: Modbus TCP连接测试

使用Modbus TCP客户端工具（如Modbus Poll）连接设备：
- **IP地址**: 192.168.1.10
- **端口**: 502
- **功能码**: 0x03（读保持寄存器）
- **起始地址**: 0x0000
- **数量**: 2（读取CH1电阻值）

**预期结果**: 成功读取寄存器数据

---

## 常见问题排查

### 问题1: 串口输出"错误: 未检测到有效的PHY芯片"

**可能原因**:
1. MDC/MDIO线路接触不良或断开
2. YT8512C芯片未上电
3. PHY地址配置错误（硬件strapping引脚）

**排查步骤**:
1. 用万用表测量YT8512C供电电压（3.3V或1.8V）
2. 检查MDC/MDIO引脚的焊接
3. 检查PHY_RST#引脚是否正常（应该在初始化后为高电平）

### 问题2: PHY初始化完成但链路未建立

**可能原因**:
1. 网线未插入或网线故障
2. RMII时钟未正确配置
3. TX/RX数据线连接错误

**排查步骤**:
1. 更换网线测试
2. 检查PHY LED指示（如果有）：
   - LED0应该在有链路时闪烁（Activity）
   - LED1应该指示速度（100M时亮）
3. 用示波器检查PA1引脚的50MHz参考时钟
4. 检查TX_EN、TXD0、TXD1信号在发送数据时是否有波形

### 问题3: 链路建立但无法ping通

**可能原因**:
1. IP地址冲突
2. LwIP协议栈配置问题
3. ARP解析失败

**排查步骤**:
1. 检查PC和设备是否在同一子网（默认192.168.1.x）
2. 尝试使用arp命令查看ARP表：`arp -a`
3. 检查防火墙是否阻止ICMP包
4. 观察串口是否有"网络链路断开"的消息

### 问题4: 自协商失败或速度异常（10M半双工）

**可能原因**:
1. PHY自协商配置寄存器（ANAR）设置不正确
2. 交换机端口配置为强制速度模式
3. 信号完整性问题（阻抗不匹配、走线过长）

**排查步骤**:
1. 检查串口输出的自协商结果
2. 尝试连接到不同的交换机端口
3. 检查PCB走线长度和阻抗匹配（RMII要求50Ω差分阻抗）

---

## 高级调试选项

### 选项1: 启用详细的PHY寄存器转储

在`LAN8742_Init()`函数中添加寄存器读取代码：

```c
// 添加到初始化完成后
if(status == LAN8742_STATUS_OK) {
    uint32_t reg_value;
    printf("=== PHY寄存器转储 ===\r\n");

    // 基本寄存器
    pObj->IO.ReadReg(pObj->DevAddr, 0x00, &reg_value);
    printf("BCR (0x00): 0x%04lX\r\n", reg_value);

    pObj->IO.ReadReg(pObj->DevAddr, 0x01, &reg_value);
    printf("BSR (0x01): 0x%04lX\r\n", reg_value);

    pObj->IO.ReadReg(pObj->DevAddr, 0x11, &reg_value);
    printf("PHYSCSR (0x11): 0x%04lX\r\n", reg_value);

    // YT8512C扩展寄存器
    LAN8742_ReadExtReg(pObj, 0x4001, &reg_value);
    printf("EXT_PAD_CTRL (0x4001): 0x%04lX\r\n", reg_value);

    printf("=====================\r\n");
}
```

### 选项2: 使用内部回环测试

添加PHY内部回环测试以排除外部线路问题：

```c
// 使能内部回环
LAN8742_EnableLoopbackMode(&LAN8742);

// 发送测试包并检查是否能接收
// ...

// 禁用回环
LAN8742_DisableLoopbackMode(&LAN8742);
```

---

## 参考资料

- **YT8512C数据表**: 您提供的寄存器定义表
- **STM32F407参考手册**: RM0090 (第41章 以太网MAC)
- **STM32F4 ETH HAL驱动**: `stm32f4xx_hal_eth.c`
- **LwIP协议栈文档**: https://www.nongnu.org/lwip/

---

## 修改文件列表

1. **src/lan8742.c** (主要修改)
   - 修复扩展寄存器访问方法
   - 改进PHY地址检测
   - 添加YT8512C RMII配置
   - 添加详细调试输出

2. **src/ethernetif.c**
   - 添加SYSCFG RMII模式选择

3. **include/lan8742.h** (无修改，已有正确定义)

---

**版本**: 2025-01-13
**修复人**: Claude Code
**状态**: ✅ 编译成功，待测试验证
