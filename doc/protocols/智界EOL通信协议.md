# 3.5H EOL 通信协议

## 1. 基本协议

EOL 通信使用固定 CAN ID、固定 DLC 和 8 字节数据帧。

| 方向 | CAN ID | DLC | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 上位机发送 | `0x7F0` | `8` | `0x10` | 功能码 | 子命令 | `0x00` | 参数0 | 参数1 | 参数2 | 参数3 |
| BMS 回复 | `0x7F8` | `8` | `0x11` | 功能码 | 子命令 | ResponseCode | 数据0 | 数据1 | 数据2 | 数据3 |

### 字节含义

| 字段 | 说明 |
| --- | --- |
| Byte0 | 帧类型。发送固定为 `0x10`，回复固定为 `0x11`。 |
| Byte1 | 功能码，例如 `0x03` 表示绝缘，`0x04` 表示 GPIO。 |
| Byte2 | 子命令，同一功能码下区分具体操作。 |
| Byte3 | 发送帧固定填 `0x00`；回复帧为响应码。 |
| Byte4-Byte7 | 发送帧为可能携带的参数；回复帧为返回数据。 |

### ResponseCode

| 值 | 含义 |
| --- | --- |
| `0x40` | 肯定响应，命令执行成功。 |
| `0x80` | 否定响应，命令执行失败。 |

### 约定

- DLC 固定为 `8`。
- 表格中的 `/` 表示该字节无业务含义，但实际发送和回复中的空字节均按 `0x00` 填充。
- 所有帧均为 8 字节，不足的参数或数据字节补 `0x00`。
- 多字节数值按高字节在前、低字节在后解析。
- 返回数据位于 Byte4-Byte7，需要结合功能码和子命令解释。

## 2. 功能码总览

| 功能码 | 功能分组 |
| --- | --- |
| `0x03` | 绝缘 |
| `0x04` | GPIO |
| `0x05` | PWM |
| `0x06` | ADC |
| `0x07` | CSC |
| `0x08` | CRASH |
| `0x09` | RTC |
| `0x10` | NTC |
| `0x0A` | EEPROM |
| `0x0B` | 电流 |
| `0xFF` | 唤醒源/传感器/高边负载反馈 |

## 3. `0x03` 绝缘

### 3.1 控制绝缘桥臂

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x03` | `0x01` | `0x00` | `0x01` | 状态 | `/` | `/` | 控制绝缘桥臂 |
| 回复 | `0x7F8` | `0x11` | `0x03` | `0x01` | ResponseCode | `/` | `/` | `/` | `/` | 执行结果 |

| 状态 | 含义 |
| --- | --- |
| `0` | P/N 均断开 |
| `1` | P 闭合，N 断开 |
| `2` | P 断开，N 闭合 |

### 3.2 读取绝缘值

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x03` | `0x03` | `0x00` | `/` | `/` | `/` | `/` | 读取绝缘值 |
| 回复 | `0x7F8` | `0x11` | `0x03` | `0x03` | ResponseCode | bit23:bit16 | bit15:bit8 | bit7:bit0 | 符号 | 绝缘值 |

解析规则：

```text
raw = (Byte4 << 16) | (Byte5 << 8) | Byte6
sign = -1 if Byte7 == 1 else 1
value = sign * raw * 0.001
```

## 4. `0x04` GPIO

### 4.1 读取 GPIO 状态

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x04` | `0x01` | `0x00` | GPIO 索引 | `/` | `/` | `/` | 读取 GPIO 状态 |
| 回复 | `0x7F8` | `0x11` | `0x04` | `0x01` | ResponseCode | GPIO 索引 | 电平状态 | `/` | `/` | GPIO 状态 |

### 4.2 写入 GPIO 状态

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x04` | `0x05` | `0x00` | GPIO 索引 | 电平状态 | `/` | `/` | 写入 GPIO 状态 |
| 回复 | `0x7F8` | `0x11` | `0x04` | `0x05` | ResponseCode | GPIO 索引 | `/` | `/` | `/` | 执行结果 |

### 4.3 GPIO 索引表

| GPIO 索引 | GPIO Mapping |
| --- | --- |
| `0x01` | DIO_CHANNEL_HSD_O_00_EN |
| `0x02` | DIO_CHANNEL_HSD_O_01_EN |
| `0x03` | DIO_CHANNEL_HSD_O_02_EN |
| `0x04` | DIO_CHANNEL_HSD_O_03_EN |
| `0x05` | DIO_CHANNEL_HSD_O_04_EN |
| `0x06` | DIO_CHANNEL_HSD_O_05_EN |
| `0x07` | DIO_CHANNEL_HSD_O_06_EN |
| `0x08` | DIO_CHANNEL_HSD_O_07_EN |
| `0x09` | DIO_CHANNEL_LSD_O_00_EN |
| `0x0A` | DIO_CHANNEL_LSD_O_01_EN |
| `0x0B` | DIO_CHANNEL_LSD_O_02_EN |
| `0x0C` | DIO_CHANNEL_LSD_O_03_EN |
| `0x0D` | DIO_CHANNEL_LSD_O_04_EN |
| `0x0E` | DIO_CHANNEL_LSD_O_05_EN |
| `0x10` | CC1_2015+_S2 |
| `0x11` | CC2_SW3 |
| `0x12` | LINK |
| `0x13` | FAS |
| `0x14` | SC_EN1 |

## 5. `0x05` PWM

### 5.1 读取 PWM 占空比

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x05` | `0x01` | `0x00` | PWM 通道 | `/` | `/` | `/` | 读取 PWM 占空比 |
| 回复 | `0x7F8` | `0x11` | `0x05` | `0x01` | ResponseCode | 占空比 | `/` | `/` | `/` | 占空比 |

### 5.2 读取 PWM 频率

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x05` | `0x02` | `0x00` | PWM 通道 | `/` | `/` | `/` | 读取 PWM 频率 |
| 回复 | `0x7F8` | `0x11` | `0x05` | `0x02` | ResponseCode | bit31:bit24 | bit23:bit16 | bit15:bit8 | bit7:bit0 | PWM 频率 |

## 6. `0x06` ADC

### 6.1 读取 ADC 原始值

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x06` | `0x01` | `0x00` | ADC 索引 | `/` | `/` | `/` | 读取 ADC 原始值 |
| 回复 | `0x7F8` | `0x11` | `0x06` | `0x01` | ResponseCode | ADC 索引 | bit15:bit8 | bit7:bit0 | `/` | ADC 原始值 |

解析规则：

```text
value = (Byte5 << 8) | Byte6
```

### 6.2 读取 ADC 转换值

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x06` | `0x02` | `0x00` | ADC 索引 | `/` | `/` | `/` | 读取 ADC 转换值 |
| 回复 | `0x7F8` | `0x11` | `0x06` | `0x02` | ResponseCode | ADC 索引 | bit15:bit8 | bit7:bit0 | `/` | ADC 转换值，factor: `0.001` |

解析规则：

```text
raw = (Byte5 << 8) | Byte6
value = raw * 0.001
```

### 6.3 ADC 索引表

| ADC 索引 | 通道 |
| --- | --- |
| `0` | KL30_IN1_V_A2D |
| `1` | WKD_EXT1_ADC |
| `2` | WKD_EXT2_ADC |
| `3` | WKD_INT1_INT2_A2D |
| `4` | WKD_INT3_INT4_A2D |
| `5` | WKD_EXT3_ADC |
| `6` | KL30_IN2_V_A2D |
| `7` | WKD_EXT6_ADC |
| `8` | HWREV_A2D |
| `9` | HSD_O_00_USNS |
| `10` | HSD_O_01_USNS |
| `11` | HSD_O_02_USNS |
| `12` | HSD_O_03_USNS |
| `13` | WKD_INT6_ADC |
| `14` | GB2015_CC2_PE |
| `15` | HSD_O_06_USNS |
| `16` | HSD_O_07_USNS |
| `17` | HSD_I_CS1_4 |
| `18` | LSD_V_AD1 |
| `19` | LSD_V_AD2 |
| `20` | NTCF_MCU |
| `21` | HSD_I_CS7_8 |
| `22` | SIG1_A_ADC |
| `23` | HALL_IN1_ADC |
| `24` | SIG3_A_ADC |
| `25` | LSD_V_AD3 |
| `26` | SBC_VS1 |
| `27` | HSD_O_04_USNS |
| `28` | LSD_V_AD4 |
| `29` | HSD_O_05_USNS |
| `30` | NTCF_I_00 |
| `31` | NTCF_I_01 |
| `32` | INPUT2_USNS |
| `33` | INPUT3_USNS |
| `34` | HALL_5V_ADC |
| `35` | WKD_INT7_ADC |
| `36` | NTCF_I_02 |
| `37` | NTCF_I_03 |
| `38` | NTCF_I_04 |
| `39` | HSD_I_CS5_6 |
| `40` | NTCF_I_05 |
| `41` | CHRG_GB2015_CC1 |
| `42` | CHRG_GB_CC2 |
| `43` | INPUT1_USNS |
| `44` | OUTPUT3_USNS |
| `45` | WKD_EXT4_ADC |
| `46` | Pulse1_ADC |
| `47` | OUTPUT2_USNS |
| `48` | OUTPUT1_USNS |

## 7. `0x07` CSC

### 7.1 设置节点数目

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x07` | `0x01` | `0x00` | 节点数目 | `/` | `/` | `/` | 支持 1~12 |
| 回复 | `0x7F8` | `0x11` | `0x07` | `0x01` | ResponseCode | `/` | `/` | `/` | `/` | 执行结果 |

### 7.2 读取高压

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x07` | `0x02` | `0x00` | 高压索引 | `/` | `/` | `/` | 读取高压 |
| 回复 | `0x7F8` | `0x11` | `0x07` | `0x02` | ResponseCode | bit31:bit24 | bit23:bit16 | bit15:bit8 | bit7:bit0 | 高压值 |

解析规则：

```text
raw = (Byte4 << 24) | (Byte5 << 16) | (Byte6 << 8) | Byte7
value = raw * 0.001
```

高压索引：`0x02` = HV1，`0x03` = HV2/HV3，`0x0B` = link1，`0x0C` = link2。

### 7.3 均衡控制

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x07` | `0x05` | `0x00` | `/` | 均衡状态 | `/` | `/` | `0` 关闭均衡，非 `0` 开启均衡 |
| 回复 | `0x7F8` | `0x11` | `0x07` | `0x05` | ResponseCode | `/` | `/` | `/` | `/` | 执行结果 |

### 7.4 读取单体电压

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x07` | `0x0E` | `0x00` | 电芯索引 bit15:bit8 | 电芯索引 bit7:bit0 | `/` | `/` | 读取单体电压 |
| 回复 | `0x7F8` | `0x11` | `0x07` | `0x0E` | ResponseCode | bit31:bit24 | bit23:bit16 | bit15:bit8 | bit7:bit0 | 单体电压，factor: `0.001` |

### 7.5 读取 Stack 电压

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x07` | `0x0F` | `0x00` | Stack 索引 bit15:bit8 | Stack 索引 bit7:bit0 | `/` | `/` | 读取 Stack 电压 |
| 回复 | `0x7F8` | `0x11` | `0x07` | `0x0F` | ResponseCode | bit31:bit24 | bit23:bit16 | bit15:bit8 | bit7:bit0 | Stack 电压，factor: `0.001` |

### 7.6 读取快充阻抗

| 方向 | CAN ID | Byte0 | Byte1 | Byte2 | Byte3 | Byte4 | Byte5 | Byte6 | Byte7 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 发送 | `0x7F0` | `0x10` | `0x07` | `0x10` | `0x00` | `/` | `/` | `/` | `/` | 读取快充阻抗 |
| 回复 | `0x7F8` | `0x11` | `0x07` | `0x10` | ResponseCode | bit31:bit24 | bit23:bit16 | bit15:bit8 | bit7:bit0 | 快充阻抗 |

## 8. `0x08` CRASH

| 子命令 | 操作 | 发送 Byte4 | 发送 Byte5 | 发送 Byte6 | 发送 Byte7 | 回复 Byte4-Byte7 | 解析 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `0x01` | 读取 PWM 占空比 | `/` | `/` | `/` | `/` | Byte4 = 占空比 | 占空比 = Byte4 |
| `0x02` | 读取 PWM 频率 | `/` | `/` | `/` | `/` | bit31:bit0 | U32 |
| `0x03` | 读取脉冲阻抗 | 索引：`0` sig1，`1` sig3 | `/` | `/` | `/` | bit31:bit0 | U32 |
| `0x04` | 读取脉冲宽度 | `/` | `/` | `/` | `/` | bit31:bit0 | U32 |

通用帧格式：发送 `0x7F0 10 08 子命令 00 Byte4 Byte5 Byte6 Byte7`；回复 `0x7F8 11 08 子命令 ResponseCode Byte4 Byte5 Byte6 Byte7`。

## 9. `0x09` RTC

| 子命令 | 操作 | 发送 Byte4 | 发送 Byte5 | 发送 Byte6 | 发送 Byte7 | 回复 Byte4-Byte7 | 解析 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `0x04` | 读取 RTC 时间 | `/` | `/` | `/` | `/` | bit31:bit0 | 原始 4 字节时间值 |
| `0x05` | 设置 RTC 唤醒时间 | `0x02` | bit23:bit16 | bit15:bit8 | bit7:bit0 | `/` | 执行结果 |
| `0x07` | 设置 RTC 时间 | bit31:bit24 | bit23:bit16 | bit15:bit8 | bit7:bit0 | `/` | 执行结果 |

通用帧格式：发送 `0x7F0 10 09 子命令 00 Byte4 Byte5 Byte6 Byte7`；回复 `0x7F8 11 09 子命令 ResponseCode Byte4 Byte5 Byte6 Byte7`。

## 10. `0x10` NTC

| 子命令 | 操作 | 发送 Byte4 | 发送 Byte5 | 发送 Byte6 | 发送 Byte7 | 回复 Byte4-Byte7 | 解析 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `0x01` | 读取单体温感 | 温感索引 | `/` | `/` | `/` | bit31:bit0 | 温度值，offset: `-50` |
| `0x02` | 读取 PCB 温感 | 温感索引 | `/` | `/` | `/` | bit31:bit0 | 温度值，offset: `-50` |
| `0x04` | 读取主机温感 | 温感索引 | `/` | `/` | `/` | bit31:bit0 | 温度值，offset: `-50` |
| `0x05` | 读取主机 PCB 温感 | 温感索引 | `/` | `/` | `/` | bit31:bit0 | 温度值，offset: `-50` |

通用帧格式：发送 `0x7F0 10 10 子命令 00 Byte4 Byte5 Byte6 Byte7`；回复 `0x7F8 11 10 子命令 ResponseCode Byte4 Byte5 Byte6 Byte7`。

## 11. `0x0A` EEPROM

| 子命令 | 操作 | 发送 Byte4 | 发送 Byte5 | 发送 Byte6 | 发送 Byte7 | 回复 Byte4-Byte7 | 解析 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `0x01` | 设置 EEPROM 操作地址 | bit31:bit24 | bit23:bit16 | bit15:bit8 | bit7:bit0 | `/` | 执行结果 |
| `0x03` | 读取 EEPROM 数据 | `/` | `/` | `/` | `/` | 地址0 byte | 地址1 byte | 地址2 byte | 地址3 byte |
| `0x05` | 写入 EEPROM 数据 | 地址0 byte | 地址1 byte | 地址2 byte | 地址3 byte | `/` | 执行结果 |

说明：EEPROM 每次读取/写入 4 字节，操作完成后地址自动加 4。

通用帧格式：发送 `0x7F0 10 0A 子命令 00 Byte4 Byte5 Byte6 Byte7`；回复 `0x7F8 11 0A 子命令 ResponseCode Byte4 Byte5 Byte6 Byte7`。

## 12. `0x0B` 电流

| 子命令 | 操作 | 发送 Byte4 | 发送 Byte5 | 发送 Byte6 | 发送 Byte7 | 回复 Byte4-Byte7 | 解析 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `0x01` | 读取霍尔电流 | `/` | `/` | `/` | `/` | bit31:bit0 | 电流值 |
| `0x03` | 读取霍尔电流 | `/` | `/` | `/` | `/` | bit31:bit0 | 电流值 |

解析规则：

```text
raw = (Byte4 << 24) | (Byte5 << 16) | (Byte6 << 8) | Byte7
value = raw * 0.001 - 800
```

## 13. `0xFF` 唤醒源/传感器/高边负载反馈

| 子命令 | 操作 | 发送 Byte4 | 发送 Byte5 | 发送 Byte6 | 发送 Byte7 | 回复 Byte4-Byte7 | 解析 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `0x06` | 读取第一唤醒源 | `/` | `/` | `/` | `/` | Byte4 = 第一唤醒源 | 唤醒源掩码 |
| `0x0E` | 读取压力传感器 | `/` | `/` | `/` | `/` | bit31:bit0 | U32 |
| `0x11` | 读取高边负载回采电压 | `/` | `/` | `/` | `/` | Byte4=bit15:bit8，Byte5=bit7:bit0 | U16 |

通用帧格式：发送 `0x7F0 10 FF 子命令 00 Byte4 Byte5 Byte6 Byte7`；回复 `0x7F8 11 FF 子命令 ResponseCode Byte4 Byte5 Byte6 Byte7`。

### 13.1 唤醒源掩码

| 掩码 | 含义 |
| --- | --- |
| `0x1` | WAKE_MASK_KL15 |
| `0x2` | WAKE_MASK_CAN |
| `0x4` | WAKE_MASK_EXT3 |
| `0x8` | WAKE_MASK_AUX |
| `0x10` | WAKE_MASK_RTC |
| `0x20` | WAKE_MASK_TPL |
| `0x40` | WAKE_MASK_BPS_RSV |
| `0x80` | WAKE_MASK_EXT4 |
| `0x100` | WAKE_MASK_EXT6 |
| `0x200` | WAKE_MASK_CAN2 |
| `0x400` | WAKE_MASK_CAN3 |
