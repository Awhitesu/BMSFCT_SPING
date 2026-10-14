0# 老化监控系统客户端 POST 接口文档

本文档详细说明了老化监控系统（后端服务默认端口 `8008`）中客户端/上位机需要调用的 **POST** 接口及其请求数据内容。
步骤 1: 准备测试 (prepare)                                          │
│  POST /api/channels/{id}/prepare                                     │
│  员工扫码绑定条码 → 通道状态: idle → preparing   
---

## 接口调用顺序与流程

老化测试的完整生命周期中，客户端/上位机应按以下顺序调用 POST 接口：

```
┌─────────────────────────────────────────────────────────────────────┐
│                      │
├─────────────────────────────────────────────────────────────────────┤
│  步骤 2: 开始测试 (start-test)                                       │
│  POST /api/channels/{id}/start-test                                  │
│  按下启动按钮 → 通道状态: preparing → testing                        │
├─────────────────────────────────────────────────────────────────────┤
│  步骤 3: 测试过程中持续调用 (循环)                                    │
│  ├─ POST /api/channels/{id}/progress    (测试项变更时或定时上报)      │
│  │   作用: 上报当前测试子项进度                                        │
│  │                                                                   │
│  └─ POST /api/system/heartbeat          (每隔 ≤10s 发送心跳)         │
│      作用: 上报老化温度箱实时温度，同时维持系统"已连接"状态             │
├─────────────────────────────────────────────────────────────────────┤
│  步骤 4: 完成测试 (finish-test)                                      │
│  POST /api/channels/{id}/finish-test                                 │
│  上报最终判定结果 → 通道状态: testing → passed / failed              │
├─────────────────────────────────────────────────────────────────────┤
│  步骤 5: 接收完整测试数据 (test-data)                                │
│  POST /api/test-data                                                 │
│  上报整轮测试的完整历史记录（含主机和从机所有测试项）                  │
├─────────────────────────────────────────────────────────────────────┤
│  步骤 6: 重置通道 (reset)                                            │
│  POST /api/channels/{id}/reset                                       │
│  设备下线 → 清除通道数据 → 通道状态: 恢复为 idle                     │
└─────────────────────────────────────────────────────────────────────┘
```

> **注意**: 步骤 3 中的 **进度接口** (`/progress`) 和 **心跳接口** (`/system/heartbeat`) 是**并行调用**的：
> - **进度**：在测试项变更或有新数据时发送，用于更新前端实时显示。
> - **心跳**：全局接口，与通道无关。老化设备软件启动后独立定时发送（建议每 5~10 秒），**每次心跳都必须携带当前老化温度箱的实时温度**，用于维持系统"已连接"状态并更新温度曲线。

---

## 1. 准备测试（绑定条码）

员工扫码绑定设备，通道状态由 `idle` 切换至 `preparing`。

* **接口地址**: `POST /api/channels/{id}/prepare`
* **URL 参数**: `{id}` - 通道号（整型数字，例如 `1`, `2`, `16` 等）
* **Content-Type**: `application/json`

### 请求体 (JSON)

```json
{
  "master_barcode": "MASTER_SN_20260523001",
  "slave_barcode_1": "SLAVE1_SN_20260523001",
  "slave_barcode_2": "SLAVE2_SN_20260523001",
  "slave_barcode_3": "SLAVE3_SN_20260523001"
}
```

### 字段详细说明

| 字段名称 | 数据类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `master_barcode` | String | 是 | 主板（Master）条码 SN |
| `slave_barcode_1` | String | 否 | 从板 1（Slave 1）条码 SN |
| `slave_barcode_2` | String | 否 | 从板 2（Slave 2）条码 SN |
| `slave_barcode_3` | String | 否 | 从板 3（Slave 3）条码 SN |

### 返回响应 (JSON)

* **状态码 200 OK**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "message": "测试准备就绪"
  }
}
```

---

## 2. 开始测试（启动）

按下启动按钮，通道状态切换至 `testing`。

* **接口地址**: `POST /api/channels/{id}/start-test`
* **URL 参数**: `{id}` - 通道号（整型数字，例如 `1`, `2`, `16` 等）
* **Content-Type**: `application/json`

### 请求体 (JSON)

```json
{
  "start": 1
}
```

### 字段详细说明

| 字段名称 | 数据类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `start` | Integer | 否 | 启动标识，固定值 `1` |

### 返回响应 (JSON)

* **状态码 200 OK**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "message": "测试已开始"
  }
}
```

---

## 3. 实时更新测试进度接口

本接口用于在老化测试过程中，定时或在测试项变更时上报当前通道的最新测试子项进度。

* **接口地址**: `POST /api/channels/{id}/progress`
* **URL 参数**: `{id}` - 通道号（整型数字，例如 `1`, `2`, `16` 等）
* **Content-Type**: `application/json`

### 请求体 (JSON) 结构说明

```json
{
  "barcode": "主机",
  "name": "电压读取测试",
  "testValue": "12.05",
  "unit": "V",
  "upperLimit": "12.60",
  "lowerLimit": "11.40",
  "result": "PASS",
  "index": "1"
}
```

### 字段详细说明

| 字段名称 | 数据类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `barcode` | String | 是 | 当前正在测试的测试项所属的板子类型：`主机` / `从机1` / `从机2` / `从机3` |
| `name` | String | 是 | 当前正在测试的子项名称（如：电压测试、CAN通信测试） |
| `testValue` | String | 是 | 测试测量值 |
| `unit` | String | 否 | 物理单位（如：`V`, `A`, `bool`, `℃`） |
| `upperLimit` | String | 否 | 测试上限值 |
| `lowerLimit` | String | 否 | 测试下限值 |
| `result` | String | 是 | 单项判定结果（`PASS` 或 `FAIL`） |
| `index` | String | 否 | 步骤序号（如：`1`, `2`） |

### 返回响应 (JSON)

* **状态码 200 OK**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "message": "测试进度更新成功"
  }
}
```

---

## 4. 设备心跳上报（全局）

老化设备上位机软件启动后，需要**持续、循环地定时发送**心跳信号。每次心跳必须携带当前**老化温度箱的实时温度**。后端收到心跳后立即判定老化设备**已连接**，前端顶部状态栏显示"已连接"，同时更新温度曲线。后台心跳监控每 5 秒巡检一次，若 **超过 15 秒** 未收到任何心跳，则自动判定设备**离线**，前端状态栏显示"连接中断"。

> **注意**：
> - 此心跳是**全局接口**，与具体通道无关。一台老化设备只需发送一个心跳即可维持系统在线状态。
> - **必须循环发送**，只发一次会在 15 秒后自动离线。
> - **请求体中的 `chamber_temperature` 是老化温度箱的实时温度，不是空数据**。

* **接口地址**: `POST /api/system/heartbeat`
* **Content-Type**: `application/json`

### 请求体 (JSON)

```json
{
  "chamber_temperature": 55.4
}
```

### 字段详细说明

| 字段名称 | 数据类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `chamber_temperature` | Float | 是 | **老化温度箱当前实时温度（单位：℃），必须大于 0** |

### 返回响应 (JSON)

* **状态码 200 OK**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "message": "心跳已接收",
    "online": true,
    "timestamp": "2026-05-23T14:05:12+08:00"
  }
}
```

### 调用方式

**不是一次性发送，而是每隔固定时间循环发送。**

设备端应使用一个定时器（Timer），在软件运行期间**持续每隔一段时间调用一次**该接口：

```
设备软件启动
    │
    ▼
┌────────────────────────────────────────────┐
│  每隔 5 秒发送一次                          │◄── 循环执行，直到软件关闭
│  POST /api/system/heartbeat                 │
│  Body: { "chamber_temperature": 当前温度值 } │
└────────────────────────────────────────────┘
```

> **建议调用频率**：每隔 **5 秒** 发送一次心跳请求。这样即使某一次因网络波动丢失，下一次（5秒后）仍能及时补上，不会被误判为离线。

---

## 5. 完成测试（结果判定）

测试判定结束，通道状态切换为 `passed` 或 `failed`。

* **接口地址**: `POST /api/channels/{id}/finish-test`
* **URL 参数**: `{id}` - 通道号（整型数字，例如 `1`, `2`, `16` 等）
* **Content-Type**: `application/json`

### 请求体 (JSON)

```json
{
  "result": true
}
```

### 字段详细说明

| 字段名称 | 数据类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `result` | Boolean | 是 | 老化判定结果：`true` = 合格（`passed`），`false` = 异常（`failed`） |

### 返回响应 (JSON)

* **状态码 200 OK**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "message": "测试已完成"
  }
}
```

---

## 6. 接收完整测试数据接口

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
      "index": "1",
      "testclass": "主机"
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
      "index": "1",
      "testclass": "从机1"
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
* `testclass`: 测试项所属板子类型（如：`"主机"`、`"从机1"`、`"从机2"`、`"从机3"`）

### 返回响应 (JSON)

* **状态码 200 OK**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "message": "测试数据接收成功",
    "barcode": "MASTER_SN_123456",
    "status": true
  }
}
```

---

## 7. 重置通道

设备下线，重置通道当前状态（清除条码、boards、测试进度等运行时数据，历史测试记录保留在数据库中），恢复为 `idle` 状态。

* **接口地址**: `POST /api/channels/{id}/reset`
* **URL 参数**: `{id}` - 通道号（整型数字，例如 `1`, `2`, `16` 等）
* **Content-Type**: `application/json`

### 请求体 (JSON)

空 Body，无需传递任何字段：

```json
{}
```

### 返回响应 (JSON)

* **状态码 200 OK**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "message": "通道已重置"
  }
}
```
