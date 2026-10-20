# 老化监控系统客户端 POST 接口文档

本文档详细说明了老化监控系统（后端服务默认端口 `8008`）中客户端/上位机需要调用的 **POST** 接口及其请求数据内容。

---

## 1. 实时更新测试进度接口

本接口用于在老化测试过程中，定时或在测试项变更时上报当前通道的最新测试子项进度、绑定的条码和高温箱体实时温度。

* **接口地址**: `POST /api/channels/{id}/progress`
* **URL 参数**: `{id}` - 通道号（整型数字，例如 `1`, `2`, `16` 等）
* **Content-Type**: `application/json`

### 请求体 (JSON) 结构说明

```json
{
  "reported_remaining_time": 45,
  "master_barcode": "MASTER_SN_123456",
  "slave_barcode_1": "SLAVE1_SN_123456",
  "slave_barcode_2": "SLAVE2_SN_123456",
  "slave_barcode_3": "SLAVE3_SN_123456",
  "barcode": "MASTER_SN_123456",
  "name": "电压读取测试",
  "testValue": "12.05",
  "unit": "V",
  "upperLimit": "12.60",
  "lowerLimit": "11.40",
  "result": "PASS",
  "index": "1",
  "chamber_temperature": 55.4
}
```

### 字段详细说明

| 字段名称 | 数据类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `reported_remaining_time` | Integer | 否 | 该通道的老化测试剩余时间（单位：分钟） |
| `master_barcode` | String | 是 | 主板（Master）条码 SN |
| `slave_barcode_1` | String | 否 | 从板 1（Slave 1）条码 SN（如无则留空或不传） |
| `slave_barcode_2` | String | 否 | 从板 2（Slave 2）条码 SN（如无则留空或不传） |
| `slave_barcode_3` | String | 否 | 从板 3（Slave 3）条码 SN（如无则留空或不传） |
| `barcode` | String | 是 | 当前正在测试的板子条码（必须是上述 4 个条码中的一个） |
| `name` | String | 是 | 当前正在测试的子项名称（如：电压测试、CAN通信测试） |
| `testValue` | String | 是 | 测试测量值 |
| `unit` | String | 否 | 物理单位（如：`V`, `A`, `bool`, `℃`） |
| `upperLimit` | String | 否 | 测试上限值 |
| `lowerLimit` | String | 否 | 测试下限值 |
| `result` | String | 是 | 单项判定结果（`PASS` 或 `FAIL`） |
| `index` | String | 否 | 步骤序号（如：`1`, `2`） |
| `chamber_temperature` | Float | 是 | **烘箱高温箱体当前实时温度（单位：℃）** |

### 返回响应 (JSON)

* **状态码 200 OK**:
```json
{
  "code": 200,
  "message": "success",
  "data": null
}
```

---

## 2. 接收完整测试数据接口

当老化测试完全结束时，调用此接口上报当前通道的整轮测试的最终记录（包含主机和三个从机所有的历史测试项目数据）。

* **接口地址**: `POST /api/test-data`
* **Content-Type**: `application/json`

### 请求体 (JSON) 结构说明

```json
{
  "channel_id": "1",
  "master_barcode": "MASTER_SN_123456",
  "slave_barcode_1": "SLAVE1_SN_123456",
  "slave_barcode_2": "SLAVE2_SN_123456",
  "slave_barcode_3": "SLAVE3_SN_123456",
  "start_time": "2026-05-23 08:30:00",
  "end_time": "2026-05-23 10:30:00",
  "product_model": "ZHIJIE-EV-V1",
  "status": true,
  "master_test_data": [
    {
      "name": "主板静态电流",
      "testValue": "15.2",
      "unit": "mA",
      "upperLimit": "20.0",
      "lowerLimit": "10.0",
      "result": "PASS",
      "index": "1"
    }
  ],
  "slave_1_test_data": [
    {
      "name": "从板1通信测试",
      "testValue": "1",
      "unit": "bool",
      "upperLimit": "1",
      "lowerLimit": "1",
      "result": "PASS",
      "index": "1"
    }
  ],
  "slave_2_test_data": [],
  "slave_3_test_data": []
}
```

### 字段详细说明

| 字段名称 | 数据类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `channel_id` | String | 是 | 通道编号（如：`"1"`, `"16"`） |
| `master_barcode` | String | 是 | 主板（Master）条码 SN |
| `slave_barcode_1` | String | 否 | 从板 1 条码 SN |
| `slave_barcode_2` | String | 否 | 从板 2 条码 SN |
| `slave_barcode_3` | String | 否 | 从板 3 条码 SN |
| `start_time` | String | 是 | 测试启动时间，格式: `YYYY-MM-DD HH:mm:ss` |
| `end_time` | String | 是 | 测试结束时间，格式: `YYYY-MM-DD HH:mm:ss` |
| `product_model` | String | 否 | 产品型号名称（如：`"ZHIJIE-EV-V1"`） |
| `status` | Boolean | 是 | 最终的老化判定结果：`true` 代表合格，`false` 代表不合格 |
| `master_test_data` | Array | 是 | 主板测试数据项的数组。若无数据，传入空数组 `[]` |
| `slave_1_test_data` | Array | 是 | 从板 1 测试数据项的数组。若无数据，传入空数组 `[]` |
| `slave_2_test_data` | Array | 是 | 从板 2 测试数据项的数组。若无数据，传入空数组 `[]` |
| `slave_3_test_data` | Array | 是 | 从板 3 测试数据项的数组。若无数据，传入空数组 `[]` |

#### 数组项（TestItem）内部字段：
* `name`: 测试项名称（如：`"静态电流测试"`）
* `testValue`: 实测值（如：`"15.2"`）
* `unit`: 单位（如：`"mA"`）
* `upperLimit`: 上限值（如：`"20.0"`）
* `lowerLimit`: 下限值（如：`"10.0"`）
* `result`: 结果判定（`"PASS"` 或 `"FAIL"`）
* `index`: 子项序号（如：`"1"`）

### 返回响应 (JSON)

* **状态码 200 OK**:
```json
{
  "code": 200,
  "message": "测试数据接收成功",
  "data": null
}
```
