# BMSFCT

**[版本历史与回滚索引（全部版本集中查看）](VERSION_HISTORY.md)**：查看每个源码检查点和已登记EXE版本的改动、验证、提交位置及回滚方式。

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

`launch_app.py` 是辅助启动入口。当前工作区的 `start_app.cmd` 和中文启动、打包批处理脚本在本次整理前已被删除；使用上面的 Python 命令启动。

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
| `doc/guides/` | 功能使用和部署指南 |
| `doc/design/` | 项目需求、架构、UI设计 |
| `doc/protocols/`、`doc/板卡协议/` | 通讯协议与板卡寄存器 |
| `doc/templates/`、`doc/DBC示例/` | 导入模板、工步参考和DBC仿真示例 |
| `doc/hardware/`、`doc/samples/` | 仪器手册和测试报告样本 |
| `doc/audits/`、`doc/history/` | 历史检查、修复和开发记录 |
| `doc/flowcharts/`、`doc/preview/` | 历史流程图与界面预览 |
| `doc/organization/` | 本次分类、清理、备份清单及验证结果 |
| `tools/` | 配套检查与导出工具 |
| `vendor/` | PCAN、ZLG、SG400厂商包与SeedKey DLL |
| `firmware/` | 电阻板、继电器板的STM32固件源码 |
| `examples/` | 独立厂商通讯示例 |
| `packaging/` | 主程序和机台互联工具的PyInstaller配置 |

硬件SDK、DLL及示例保留各自的供应商说明和许可证。使用对应硬件前请确认架构和驱动安装情况。

源码使用 `config/故障码.txt`，EXE 部署仍使用 EXE 同级故障码表。ZLG SDK 默认路径为 `vendor/zlgcan(20260806)/zlgcan_x64`。若从旧机台复制配置，应在硬件管理中检查其中保存的旧相对路径。Vinfast SeedKey DLL 已归入 `vendor/seedkey/`，使用该 DLL 时选择新位置。

## 打包与维护说明

在依赖完整的项目 Python 环境中，从项目根目录执行：

```powershell
.\.venv\Scripts\python.exe tools/package_app.py --check
.\.venv\Scripts\python.exe tools/package_app.py
```

详见 [打包说明](doc/guides/一键打包说明.md)。运行 `python tools/verify_project_layout.py` 可检查迁移记录、源码语法和打包资源路径。

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
