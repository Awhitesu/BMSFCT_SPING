# UDS DID 字典 CSV 导入说明

> 适用功能：工步编辑器（StepDialog）UDS 交互区 → **📂 导入CSV字典** 按钮
> 实现代码：`core/uds_dictionary.py:295`（`import_from_csv`）
> 字典存储位置：`config/uds_did_dictionary.json`（按名称合并，同名覆盖）

## 一、功能说明

将企业 DID 协议表批量导入本地 UDS 字典库。导入后：

1. 条目出现在 StepDialog 的**模板下拉框**中，选中即自动填充请求 PDU、期望响应、数据提取规则（偏移/长度/端序/比例等）；
2. 测试执行时，引擎按规则从 UDS 响应中提取数值，写入配方变量池（`var_name`），供后续判定/公式工步引用；
3. 数值换算公式：**实际值 = 原始值 × scale + offset_val**。

## 二、文件格式要求

| 项目 | 要求 |
|---|---|
| 文件格式 | `.csv` 逗号分隔 |
| 编码 | UTF-8（推荐 Excel「另存为 → CSV UTF-8 (逗号分隔)」，带 BOM 兼容） |
| 表头 | 第一行，中英文均可、不区分大小写 |
| 必填列 | 仅 `name / 名称`，缺失的行自动跳过 |
| 其余列 | 可整列省略，缺省走默认值 |

## 三、字段对照表

| 英文表头 | 中文表头 | 含义 | 可选值 / 格式 | 默认值 |
|---|---|---|---|---|
| name | 名称 | 模板名称（**必填**，唯一键） | 任意文本 | — |
| did | 标识符 | UDS 数据标识符 | 如 `01 01`、`F1 90` | 空 |
| request | 请求 | 完整请求 PDU | 如 `22 01 01` | `22 {did}` |
| expected | 期望响应 | 肯定响应前缀 | 如 `62 01 01` | `62 {did}` |
| mode | 模式 | 数据提取模式 | `ASCII` / `CALC` / `FORMULA` | CALC |
| offset | 偏移 | 数据在响应中的起始字节 | 整数 | 3 |
| length | 长度 | 数据字节数 | 整数 | 2 |
| endian | 端序 | 多字节字节序 | `big`（大端）/ `little`（小端） | big |
| signed | 有符号 | 是否按有符号数解析 | `1` / `true` / `yes` / `有符号`，其他为无符号 | 无符号 |
| scale | 比例 | 换算系数 | 浮点数 | 1.0 |
| offset_val | 偏移量 | 换算偏移量 | 浮点数 | 0.0 |
| unit | 单位 | 物理单位 | 如 `V`、`A`、`℃`、`%` | 空 |
| var_name | 变量名 | 提取后写入的变量名（供判定/公式引用） | 大写下划线命名，如 `BMS_PACK_VOLT` | 空 |

### 提取模式说明

| 模式 | 用途 | 关键字段 |
|---|---|---|
| ASCII | 文本类数据（VIN、软件版本号等），自动去除 NUL/填充字节 | offset、length |
| CALC | 数值类数据，按 `原始值 × scale + offset_val` 换算 | offset、length、endian、signed、scale、offset_val、unit |
| FORMULA | 自定义公式（如 `(val*0.05)+12`），在工步界面配置表达式 | 同 CALC |

## 四、示例

```csv
name,did,mode,offset,length,endian,scale,offset_val,unit,var_name
电池总压,01 01,CALC,3,2,big,0.1,0,V,BMS_PACK_VOLT
电池总电流,01 02,CALC,3,2,big,0.1,-400,A,BMS_PACK_CURR
SOC,01 03,CALC,3,1,big,0.4,0,%,BMS_SOC
单体最高电压,01 05,CALC,3,2,big,0.001,0,V,BMS_CELL_MAX_V
单体最低温度,01 08,CALC,3,1,big,1,-40,℃,BMS_CELL_MIN_T
正极绝缘阻抗,01 09,CALC,3,2,big,1,0,kΩ,BMS_INS_POS
软件版本,F1 89,ASCII,3,8,big,1,0,,ECU_SW_VER
硬件版本,F1 93,ASCII,3,8,big,1,0,,ECU_HW_VER
VIN码,F1 90,ASCII,3,17,big,1,0,,ECU_VIN
```

> 同目录附带可直接编辑的模板文件：`uds_did_template.csv`

## 五、导入操作步骤

1. 打开工步编辑器（StepDialog），选择 **UDS 交互列表 / CAN 交互** 工步；
2. 点击 **📂 导入CSV字典**，选择 CSV 文件；
3. 提示「成功从 CSV 导入 N 条 DID 模板！」即完成；
4. 在模板下拉框中选择条目，自动填充全部参数；
5. 字典持久化保存在 `config/uds_did_dictionary.json`，下次启动自动加载。

## 六、注意事项

- 同名条目会**覆盖更新**，可利用此特性批量修正字典；
- 导入只增不删，删除条目需在字典管理界面操作；
- `offset` 从响应 PDU 第 0 字节（即肯定响应服务号 0x62）开始计算，DID 占 2 字节，故数据通常从第 3 字节起；
- 若导入条数为 0，请检查：文件编码是否为 UTF-8、表头是否包含 `name` 或 `名称` 列；
- 可用 `export_to_csv`（字典管理器的导出功能）导出当前字典作为格式参考。
