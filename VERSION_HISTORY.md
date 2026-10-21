# 版本历史与回滚索引

本页集中展示源码版本和已登记的安装包版本。**源码检查点不等于已打包EXE，也不代表整机实测验收。**

## 源码版本（最新在前）

| 源码版本 | 日期 | 本次更新 | 查看／回滚代码 |
|---|---|---|---|
| S2026.10.21.15 | 2026-10-21 | 硬件管理增加可选 RS-WS-ETH-6 温湿度传感器，支持厂家 P2 主动上传与 Modbus TCP 问询模式。; 主动上传后台处理登录、心跳和实时数据确认，校验帧长、校验和、节点与来源 IP；断线重连、数据过期和停止接收时使旧值失效。; 独立调试增加温湿度数值、采集时间、接收状态与日志。; 新增读取温度（℃）、读取湿度（%RH）子工步，支持数值上下限判定、范围等待与变量保存；失败清除指定变量旧值。; 变量选择列表自动收集设备工步配置的键名；修正电阻卡旧回归用例的过期编号期望。; 109 项测试及 259 个子用例通过。192.168.1.100:2404 实测接收 9 条实时数据，约每 4.96 秒一帧；经实际测试引擎验证两个读取子工步及变量输出。; 本次更新为源码，未生成新 EXE；实机通信验证不替代传感器精度校准。 | [8fded85](https://github.com/pingsong1419-boop/BMSFCT/commit/8fded85dd0edab6d5feed24708ba03ab91137653) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/8fded85dd0edab6d5feed24708ba03ab91137653.zip) |
| S2026.10.21.14 | 2026-10-21 | Merge PR #4: DBC sampling groups and simulator improvements | [8bd5607](https://github.com/pingsong1419-boop/BMSFCT/commit/8bd560734e6f41d8d902173d3f4bf428b4661e4b) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/8bd560734e6f41d8d902173d3f4bf428b4661e4b.zip) |
| S2026.10.21.13 | 2026-10-21 | docs: record PR #4 conflict resolution in version history | [727aa0e](https://github.com/pingsong1419-boop/BMSFCT/commit/727aa0e086e58cc045534ea0957f97a62d32fa3d) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/727aa0e086e58cc045534ea0957f97a62d32fa3d.zip) |
| S2026.10.21.12 | 2026-10-21 | Merge main into PR #4: resolve DBC conflicts and retain CH2 recipe | [25d7bb7](https://github.com/pingsong1419-boop/BMSFCT/commit/25d7bb709689412932e067ce22e60d047337f042) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/25d7bb709689412932e067ce22e60d047337f042.zip) |
| S2026.10.21.11 | 2026-10-21 | 新增模拟器通道配置快照与输出校验中断支持，统一结果小数位显示，修复DBC组名与EASY320通道寻址，补充回归测试 | [214af5c](https://github.com/pingsong1419-boop/BMSFCT/commit/214af5cd053f6c071586100aa9b04920629180d9) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/214af5cd053f6c071586100aa9b04920629180d9.zip) |
| S2026.10.21.10 | 2026-10-21 | 新增DBC采样组起点/终点配置与分组规划，补充界面支持与回归测试 | [edca246](https://github.com/pingsong1419-boop/BMSFCT/commit/edca2464bd1945f4d1c0fe87c6f477fdfecab393) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/edca2464bd1945f4d1c0fe87c6f477fdfecab393.zip) |
| S2026.10.21.9 | 2026-10-21 | 新增DBC采样组起点/终点配置与分组规划，补充界面支持与回归测试 | [350f088](https://github.com/pingsong1419-boop/BMSFCT/commit/350f088b672803a275db4352147e62b601070eab) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/350f088b672803a275db4352147e62b601070eab.zip) |
| S2026.10.21.8 | 2026-10-21 | 新增DBC采样组起点/终点配置与分组规划，补充界面支持与回归测试 | [f035739](https://github.com/pingsong1419-boop/BMSFCT/commit/f03573910b7fd0b10a38af5b8d10e55194baf016) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/f03573910b7fd0b10a38af5b8d10e55194baf016.zip) |
| S2026.10.21.7 | 2026-10-21 | 建立统一版本历史与回滚索引，打包后自动登记版本变更 | [306934c](https://github.com/pingsong1419-boop/BMSFCT/commit/306934cc1b4f538e5b4bd6be4b3d375af88c8ff6) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/306934cc1b4f538e5b4bd6be4b3d375af88c8ff6.zip) |
| S2026.10.21.6 | 2026-10-21 | 明确全部继电器按导入Excel点位执行、不限24点；增加100点和300点引擎完整路径回归，相关19项通过；运行代码未改动 | [ec468c9](https://github.com/pingsong1419-boop/BMSFCT/commit/ec468c95ad0b0511f33a96370f19260e17b702de) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/ec468c95ad0b0511f33a96370f19260e17b702de.zip) |
| S2026.10.21.5 | 2026-10-21 | 电阻卡按启用卡序号连续编号，支持指定/范围/所有/奇偶通道；设置页展示有效范围；工步顶部对齐与电流单选框修复；Easy320批量断开及回读校验。99项测试和81个子测试通过，相关硬件待验收 | [b16b768](https://github.com/pingsong1419-boop/BMSFCT/commit/b16b7682940b338ca86278ae04982d6985a26ffe) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/b16b7682940b338ca86278ae04982d6985a26ffe.zip) |
| S2026.10.21.4 | 2026-10-21 | CAN发送支持选择多个物理通道；DBC采样组与最新有效值读取；电池模拟器批量限流；工步布局与ZLG参数校验更新；详细变更见提交差异 | [9979652](https://github.com/pingsong1419-boop/BMSFCT/commit/997965257376c5860bb74541e7a0cb0738e5a8cb) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/997965257376c5860bb74541e7a0cb0738e5a8cb.zip) |
| S2026.10.21.3 | 2026-10-21 | 优化工步配置UI与APP刷写流程，新增上位机进程控制及相关测试 | [408ad61](https://github.com/pingsong1419-boop/BMSFCT/commit/408ad61fea2ae07bc2f05636155f29aa39b20e65) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/408ad61fea2ae07bc2f05636155f29aa39b20e65.zip) |
| S2026.10.21.2 | 2026-10-21 | UDS数据解析参数布局调整：取消固定窄宽度，重排端序、有符号、单位、比例、偏移和公式字段 | [734cb6f](https://github.com/pingsong1419-boop/BMSFCT/commit/734cb6f144aedf3eecda785738ac71d391b6a5ec) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/734cb6f144aedf3eecda785738ac71d391b6a5ec.zip) |
| S2026.10.21.1 | 2026-10-21 | 更新UDS工步界面，增加config.ini写入功能 | [cab51a7](https://github.com/pingsong1419-boop/BMSFCT/commit/cab51a77e62da2cbe1985685c69cdc5e9f54b65a) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/cab51a77e62da2cbe1985685c69cdc5e9f54b65a.zip) |
| S2026.10.19.2 | 2026-10-19 | Add relay combination step with UI form and tests | [78d87ef](https://github.com/pingsong1419-boop/BMSFCT/commit/78d87ef5dce4dafec6f4b397e1ec6ce522ba7636) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/78d87ef5dce4dafec6f4b397e1ec6ce522ba7636.zip) |
| S2026.10.19.1 | 2026-10-19 | Update: device control, UDS workbench, DBC parsing, MES precheck and test coverage | [4a3e202](https://github.com/pingsong1419-boop/BMSFCT/commit/4a3e20233df2015013ae9d9f14bb956ea4c443b2) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/4a3e20233df2015013ae9d9f14bb956ea4c443b2.zip) |
| S2026.10.14.1 | 2026-10-14 | Initial BMSFCT source snapshot: device control, test sequences and reporting | [d3a5cd4](https://github.com/pingsong1419-boop/BMSFCT/commit/d3a5cd4ab9c0d77491c4c599e6d59774a6407200) · [下载源码](https://github.com/pingsong1419-boop/BMSFCT/archive/d3a5cd4ab9c0d77491c4c599e6d59774a6407200.zip) |

## 安装包版本（最新在前）

以下只登记找到构建清单、历史发布记录或版本文件的版本；未找到记录的旧编号不编造更新内容。历史缺口仍需补充原安装包或构建记录。

| EXE版本 | 构建时间 | 更新内容 | 验证／限制 | 源码及回滚位置 | EXE SHA256（前12位） |
|---|---|---|---|---|---|
| V2026.10.20.6 | 2026-10-20T16:40:49.665919+08:00 | 仓库version.json登记的版本号；缺少逐版构建与更新清单，无法确认具体打包内容 | 待补全；不能仅凭版本号认定包含哪次Git提交 | 未记录对应提交；未找到原安装包和SHA256，需补充归档；不要据此直接回滚 | `未保留` |
| V2026.10.19.12 | 2026-10-19T21:03:56.636402+08:00 | UDS子页12px字号、精简工具按钮并排成单行；三字段写入成功输出6E，匹配DID后将动态ASCII保存至主板序列号；测试页变量判定目标显示实际键值；VF-Limo7第11/13项按写入6E与读回变量值判定 | 相关回归68项通过，打包源码及辅助文件核验通过；未记录精确源码提交 | 未记录对应提交；本地dist/BMSFCT_V2026.10.19.12完整目录；未上传GitHub安装包 | `c12b7bcd5fbb…` |
| V2026.10.19.11 | 2026-10-19（会话发布记录） | 周期报文跨勾选调试保留，普通NG不关闭前面启动的列表；监控50ms刷新、批量绘制、拥堵时优先显示最新帧及跳过数量 | 相关周期回归25项、监控相关40项通过；端到端监控延迟未实机量测；原构建清单待补 | 未记录对应提交；历史目录名BMSFCT_V2026.10.19.11；本次未找到原安装包，需补充归档 | `未保留` |
| V2026.10.19.10 | 2026-10-19（会话发布记录） | 测试页列宽比例1:3:2:2:2:1:1.5，切换选项卡保留测试结果；屏蔽工步不加载到测试序列；周期任务失效清理；UDS尊重工步明确的Classic/FD设置，修复配方FD覆盖Classic的问题 | UDS改动相关65项回归通过；同配方实机一轮6E F1 8C成功，另一轮收到NRC22；原构建清单待补 | 未记录对应提交；历史目录名BMSFCT_V2026.10.19.10；本次未找到原安装包，需补充归档 | `未保留` |
| V2026.10.19.9 | 2026-10-19（会话发布记录） | UDS三字段与标准2E传输共用、传输高级参数和在线快照；UDS多段组合解析及右键菜单显示修复；PCAN BUS-OFF保护及发送日志 | 历史回归记录114项通过；BUS-OFF根因当时未实机确认；原构建清单待补 | 未记录对应提交；历史目录名BMSFCT_V2026.10.19.9；本次未找到原安装包，需补充归档 | `未保留` |
| V2026.10.19.1 | 2026-10-19（历史报告登记） | DBC信号导入、后台解析、工步读取和监控；详细范围见doc/audits/DBC测试报告_20261019.md | 历史DBC测试报告可查；缺少原构建清单和源码对应关系 | 未记录对应提交；历史目录名BMSFCT_V2026.10.19.1；本次未找到原安装包，需补充归档 | `未保留` |

## 如何选择回滚版本

1. 按问题发生前的功能和验证记录选择目标行；S开头为源码版本，V开头为EXE版本。
2. 回滚源码：点击该行“下载源码”，解压到独立目录后重新构建。不要在正在使用的工位直接重置工作目录。
3. 回滚EXE：使用已归档的对应版本完整目录，核对build-info.json和完整SHA256（releases/history.json中保存完整值）。仅有源码提交不代表能够还原历史EXE。
4. 先备份并保留现场数据库、config和recipes；存在配置结构或通道映射变化时，先在测试工位核对兼容性。
5. 标为未记录／待补全的历史版本不能据此确认完整更新范围或精确回滚；GitHub未上传的安装包不能在本页直接下载。

## 后续登记规则

- 每次打包成功，tools/package_app.py自动更新本页及releases/history.json；失败的构建不登记成功版本。
- 打包时可重复传入--release-note填写每条改动；未填写时采用最近的Git提交说明，并标记未提交源码。
- 打包后将本页和history.json一起提交到GitHub。源码提交前运行python tools/version_history.py刷新源码检查点。
- 旧版本缺失的记录只补充已知事实，不推测EXE与Git提交的对应关系。
