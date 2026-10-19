# UDS 2E 三字段写入与 PCAN BUS-OFF 检查

日期：2026-10-19。现场已确认使用 PCAN；当时的 Classic/FD/BRS 模式、波特率和故障前报文尚未取得。本次未向实际设备发送报文。

## 对照结论

三字段工步只负责生成 `2E + DID + ASCII(固定字段 + YYMMDD + 条码片段)`，随后进入标准 UDS 的相同执行分支、UDSClient 和 ISOTPTransport。没有另外实现一套发送协议。

对同一份最终 PDU，在 Classic、FD、FD+BRS 下分别执行普通 2E 和三字段工步，比较 CAN ID、FD/BRS 标志、DLC 及每帧数据。短内容及进入多帧传输的长内容均完全一致。仿真中的 6E+DID 肯定响应通过校验，成功后才保存“主板序列号”变量。

这不能证明现场总线不会 BUS-OFF，也不能说明现场故障根因已经确认。

## 已修复

- PCAN 原生状态码的 BUS-OFF 位检查，兼容 ctypes 状态值。
- BUS-OFF 或接收线程故障后锁定发送并唤醒响应等待；普通发送、工步重试和测试准备不再自动重连。只有主动、成功重新连接才清除锁定。
- 故障信息保留通道、波特率及最后尝试发送帧，避免后续失败覆盖原始上下文。
- Classic 连接拒绝 FD 报文，避免后端静默丢弃格式标志。
- FD 帧先按合法 DLC 长度补齐，再使用配置的 padding；原先新增 DLC 的补齐字节会由驱动补成 00，和设置的 padding 可能不同。
- UDS 逐帧记录 TX/RX、帧模式、DLC、流控 BS/STmin；停止时中断流控等待及后续帧发送。

## 仿真和回归

分组运行，全部正常结束，共 118 项通过：

| 测试文件 | 通过数 |
| --- | ---: |
| test_uds_busoff_protection.py、test_can_uds_reliability.py | 48 |
| test_can_periodic.py、test_uds_sequence_workbench.py | 41 |
| test_uds_field_write.py、test_uds_engine_integration.py | 22 |
| test_recipe_can_selection.py、test_zlg_recipe_config.py | 7 |

覆盖原生 BUS-OFF 状态、首帧后故障、等待线程唤醒、禁止自动重连、主动重连恢复、FC 缺失时禁止 CF、取消后禁止 CF、合法 FD DLC 补齐，以及普通 2E 与三字段写入相同 PDU 的逐帧对比。

## 现场仍需核对

需要记录同一配方、相同最终 PDU 下的标准 2E 与三字段工步。重点保留模式、仲裁/数据波特率、最后 TX、ECU FC、BUS-OFF 前的 PCAN 状态和错误帧。由这些证据判断故障开始于请求首帧、后续 CF、ECU 响应还是后台报文。

BUS-OFF 对应 PCAN 控制器的总线故障状态。应用层组包正确不等于物理总线参数及信号状态正确。

参考：[PEAK PCAN-Basic 状态帧说明](https://www.peak-system.com/documentation/API/PCAN-Basic.Net/html/080b4916-e4c0-4ebf-aec7-956dc31eb5ab.htm)。
