"""配方子工步语法知识库（从 core/engine.py 实际解析逻辑提炼）。

这份规则会被注入 AI 配方助手的 system prompt，也是 validate_recipe 本地校验的依据。
引擎如何解析工步，这里就如何描述；引擎改了，这里必须同步改。
"""

# 与 core/engine.py:2512-2527 的关键字路由保持一致，顺序即优先级
ROUTING_RULES = [
    # (SubStepType 名, 路由条件说明)
    ("APP_FLASH_CONFIG", "type 含「APP刷写配置」，或 device 含「APP刷写」且 action 含「查找」"),
    ("APP_FLASH", "type 或 device 含「APP刷写」"),
    ("MES_CHECK", "type 或 device 含「MES」，或 action 含「moveInCheck」"),
    ("EOL_PROTOCOL", "type 含「3.5HEOL」或「EOL协议」，或 params 含「EOL:」"),
    ("CALCULATE", "type/action 含「公式计算」，或 params 含「FORMULA:」"),
    ("READ_VAR", "type/action 含「读取变量」，或 device 含「变量操作」，或 params 含「VAR:」"),
    ("READ_INSTRUMENT", "type 含「读取」"),
    ("CAN_SEND", "type 含「CAN发送」，或 action 含「发送指令」"),
    ("CAN_RECEIVE", "type/action 含「CAN接收」或「接收指定帧ID」"),
    ("CAN_INTERACT", "type 含「CAN交互」或「报文交互」"),
    ("WAIT", "type 含「等待」"),
    ("SET_INSTRUMENT", "兜底：未命中以上任何关键字（含继电器控制/校准仪设置/连接检测等）"),
]

STEP_GRAMMAR_DOC = """## 子工步语法规则（引擎实际解析逻辑，必须严格遵守）

### 通用约定
- params 永远是【字符串】，多段用 " / " 连接，键值段格式 KEY:VALUE（KEY 大写）。
- fail_strategy 只能是：失败停止 / 忽略继续 / 重试3次。
- type 字段只是提示，引擎按关键字重新推导真实类型（见下方各路由）；未命中关键字的 type 一律落入「设置仪表」。
- sync_exec=true 表示多通道同步执行（屏障集齐后只执行一次）；测试项 exec_mode="顺序执行" 时不要给子工步加 sync_exec。
- is_judgment=true 表示该子工步的回读值作为测试项判定依据，一个需要判定的测试项应有且仅有一个 true。
- 测试项判定：standard_type="不判断" 恒 PASS；"字符串" 要求实测值与 min 精确相等；"数值" 取实测值中的数字与 min/max 比较。

### 1. 设置仪表 SET_INSTRUMENT（兜底类型，按 device 关键字分派）
- 被测物供电/IT6722A（device 含 it6722/dut power/被测物供电/main）：
  语法 "{电压}V / {电流}A / 开启输出" 或 "{电压}V / {电流}A / 关闭输出"；纯数字 params 表示只设电压。
  例："12.0V / 1.0A / 开启输出"
- 高压源 NGI（device 含 hv_source/hv source/ngi/高压源）：语法同上；专有 action「清除保护」（无 params）。
- 电池模拟器（device 含 simulator/电池模拟器）：
  批量："{电压}V / {电流}mA / ON|OFF / Range:HIGH|LOW / CH:ALL 或 1,2,3"；
  单通道："{电压}V / {电流}mA / 开启输出"（可省略 CH，默认本通道）。
- 继电器（device 含 easy320 → Easy320；含 aging board/老化 → 老化板）：
  action=闭合勾选通道/断开勾选通道，params="6,7"（通道号逗号分隔）；action 含「全部」= 全部断开。
- CA550 校准仪（device 含 ca550）：
  "Type:V|mA|OHM|RTD|TC / Val:{数值} / Range:Auto|100mV|1V|10V|30V|20mA / Output:开启输出|关闭输出"；
  简写 "关闭"=归零关输出。
- 力捷丰 SG400（device 含 sg400/opteeq/力捷丰）：
  action=连接检测/查询状态（无 params）；action=执行烧录工程时 params：
  "PROJECT:{工程名.opj} / TIMEOUT:{秒,默认300} / VERIFY_DIFF:1 / VERIFY_SCOPE:首|全 / ARCHIVE_REPORT:1"
  注意 PROJECT 值里不能含 "/"。
  VERIFY_DIFF=1 时烧录 Pass 后做 Flash 物理回读逐字节防呆比对，回读大小按 .opd 固件实际大小整包比对（不人为截断；
  VERIFY_SCOPE:首=快速档上限64KB，全=芯片全部物理扇区深度比对，缺省=按固件实际大小整包比对），不匹配判 DataDiffFail。
- 瑞萨 E2（device 含 e2_rfp/renesas/瑞萨）：
  "FILE:{路径.rpj} / DEVICE_FAMILY:RA|RX|RL78|RH850|RE / TIMEOUT:{秒} / VERIFY_DIFF:1"

### 2. 读取仪表 READ_INSTRUMENT（type 含「读取」）
- 语法："读取电压|读取电流 [/ CH:{n}] [/ UNIT:{单位}]"。
- UNIT 仅 IT6722 系设备支持：电压 V|mV，电流 A|mA|μA。
- device 决定读哪台设备：simulator→电池模拟器，it6722/dut/被测物供电→DUT 电源，hv/ngi→高压源，ca550→校准仪。
- 例："读取电流 / UNIT:mA"

### 3. CAN 类
- CAN发送（type 含「CAN发送」/action 含「发送指令」）：
  "ID:0x7E0 / DATA:10 03 / TYPE:0|1|2 / DLC:8 / CH:1"（DATA 为空格分隔 hex 字节）。
- CAN接收（含「CAN接收」/「接收指定帧ID」）：
  "ID:0x7E0 / TIMEOUT:{毫秒}"，收到即 OK。
- CAN交互/问答（type 含「CAN交互」）：
  "ID:0x7E0 / DATA:... / WAIT_ID:0x7E8 / TIMEOUT:{毫秒}"，返回响应 hex 或 TIMEOUT。
- UDS 交互列表（device 含「UDS」）：
  "TX_ID:0x7E0 / RX_ID:0x7E8 / TIMEOUT:{毫秒} / LIST64:{脚本base64url}"；
  脚本每行 "请求字节 [=> 期望字节] [| 提取规则]"，??/** 通配，# 注释；
  提取规则：ASCII(offset=3,len=17,var=ECU_VIN)、CALC(offset=3,len=2,endian=big,scale=0.1,unit=V,var=X)、FORMULA(offset=3,len=2,expr="(val*0.05)+12",unit=kPa,var=X)。
- 注意：CAN/EOL/UDS 的 TIMEOUT 单位是【毫秒】。

### 4. 3.5HEOL协议 EOL_PROTOCOL（type 含「3.5HEOL」或 params 含「EOL:」）
- 模板："EOL:{操作名} / PARAM1:x / PARAM2:x / TIMEOUT:{毫秒} / CH:n / TYPE:0 / DLC:8 / TX_ID:0x7F0 / RX_ID:0x7F8"
- 常用操作名：0x03 绝缘控制读取、0x03 绝缘控制写入(STATE)、绝缘测试、0x04 GPIO控制读取/写入(INDEX/LEVEL)、
  0x05 PWM读取、0x06 ADC读取(ADC)、0x07 CSC控制读取/写入、0x07 CSC批量读取、0x08 CRASH读取、
  0x09 RTC控制读取/写入(DATA 4字节hex)、0x10 NTC读取(INDEX)、0x0A EEPROM控制读取/写入、EEPROM测试、
  0x0B 霍尔电流读取(HALL)、DTC诊断、DTC故障清除。
- 例："EOL:0x06 ADC读取 / ADC:0 / MODE:VALUE / TIMEOUT:500 / CH:0 / TYPE:0 / DLC:8 / TX_ID:0x7F0 / RX_ID:0x7F8"

### 5. 变量与公式
- 读取变量（device 含「变量操作」或 params 含「VAR:」）："VAR:{变量名}"；找不到变量判 FAIL。
- 公式计算（含「公式计算」或「FORMULA:」）："FORMULA:{表达式} [/ VAR:{写入变量名}]"；
  表达式可用 STEP1..STEPn（当前测试项第 n 个子工步结果）、已存变量、abs/round/min/max。

### 6. 等待 WAIT（type 含「等待」）
- params 为毫秒数，如 "2000ms"。结果恒 OK。

### 7. MES过站校验 MES_CHECK（type/device 含「MES」）
- 无 params，参数全部取自系统配置。

### 8. APP刷写（device/type 含「APP刷写」）
- 查找刷写文件（type=APP刷写配置，action=查找刷写文件）：
  "HOST:{exe路径} / INI:config.ini / SECTION:App / KEY:addr / APPNAME:" → 返回固件文件名。
- 启动刷写（type=APP刷写，action=启动刷写）：
  "HOST:{exe路径} / TIMEOUT:{秒,默认300}" —— 注意这里的 TIMEOUT 单位是【秒】。
- 例："HOST:D:\\BMSTest\\BMS_General.exe / TIMEOUT:300"

### 9. 内部工步（不要在配方里手写）
- 同步屏障、申请/释放顺序锁由引擎自动注入，手写的会被过滤或造成异常。

### UI 可创建的 device + action 组合（优先使用这些原文）
- IT6722A 被测物供电电源 (IT6722A DUT Power)：设置参数 / 回读数据
- NGI 高压源 (HV Source)：设置参数 / 回读数据 / 清除保护
- 电池模拟器 (NGI 83624A Simulator)：快捷批量配置 / 回读数据
- Easy320 继电器 (Easy320)：闭合勾选通道 / 断开勾选通道 / 全部断开
- CA550 校准仪 (CA550)：参数设置 / 数据回读
- 力捷丰 SG400 下载器 (OpteeQ SG400)：连接检测 / 查询状态 / 执行烧录工程
- 瑞萨 E2 仿真器 (rfp-cli)：连接检测 / 查询状态 / 执行烧录工程
- APP刷写：查找刷写文件 / 启动刷写
- CAN 交互：发送指令 / 交互/问答 / 接收指定帧ID
- UDS 交互列表：顺序发送并校验
- 3.5HEOL协议：0x03~0xFF 各操作 / 绝缘测试 / EEPROM测试 / DTC诊断 / DTC故障清除
- MES过站校验：moveInCheck过站校验
- 等待 (Wait)：固定延时
- 变量操作 (Variable)：读取变量
- 公式计算 (Calculate)：公式计算
"""
