# BMSFCT

BMS功能测试、APP刷写和机台设备调试软件，使用Python与PySide6开发。

Qt运行库固定为PySide6/Shiboken 6.10.3。6.10.1存在官方PYSIDE-3246多线程崩溃缺陷，请使用项目虚拟环境启动；VS Code的调试配置也使用该环境。更新已有环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 功能

- 配方与测试序列编辑、参数配置、数值/字符串判定和执行日志。
- PLC启动/停止交互、MES过站校验及SQLite/Excel测试记录。
- NGI电池模拟器组合控制、ITECH电源、高压源、CA550和EASY320调试。
- PCAN/ZLG CAN及CAN FD、UDS诊断。
- SG400、瑞萨E2和APP下载、回读校验及烧录追溯。
- 34461A万用表、DG2052信号发生器、TBS2102B示波器。
- 绝缘、电阻、Relay、HV、HSD和DMM板卡调试。
- EASY320 Excel点位导入、八进制DO编号及电阻卡CH1–36组合控制。

## 运行

建议使用Windows、Python 3.11、64位环境。在项目根目录执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

也可以双击`start_app.cmd`启动。该入口绕过Windows虚拟环境启动器的光标反馈，同时使用本项目虚拟环境。

首次运行会创建本地数据库。账户初始化逻辑位于`data/db_manager.py`。在硬件管理中心及各类板卡配置页填写本机台的地址、串口、启用状态和通道映射，再进行设备连接。

首次克隆后，将 `config/sys_config.example.json` 复制为 `config/sys_config.json`，再填写本机配置。示例中的设备默认关闭。实际系统配置不纳入 Git；AI 密钥只保存在本机。现场数据库、运行日志、测试报告、虚拟环境、下载程序和压缩包不作为源码版本上传。

## 目录

| 目录 | 内容 |
|---|---|
| `core/` | 测试执行、工步、判定、报表与追溯 |
| `devices/` | 设备驱动及组合设备控制 |
| `ui/` | 图形界面、序列编辑和设备调试 |
| `data/` | 数据库管理 |
| `config/`、`recipes/` | 配置与配方 |
| `tests/` | 驱动、执行逻辑及界面测试 |
| `doc/` | 协议、功能说明、Excel模板与审查报告 |
| `tools/` | 配套检查与导出工具 |
| `GIT/`、`RES_MODBUS_TCP_2.0/` | 工作区内的板卡固件源码 |

硬件SDK、DLL及示例保留各自的供应商说明和许可证。使用对应硬件前请确认架构和驱动安装情况。

## 测试

单独运行需要验证的测试模块，例如：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_device_sequence_steps.py'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_easy320*.py'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_resistor_bank*.py'
```

实机测试脚本位于`tools/`，部分脚本会切换设备输出；应在明确对应机台配置和当前操作目的后运行。自动化软件测试不代替整套机台联测。

本项目以 GitHub 的 `main` 分支为主仓库：`https://github.com/pingsong1419-boop/BMSFCT`。首次提交为源码快照，没有导入原 Codeup 历史。
