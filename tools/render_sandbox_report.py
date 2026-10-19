"""Render a Chinese Markdown/standalone HTML report from sandbox evidence."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
from html import escape
import importlib.metadata
import json
from pathlib import Path
import shutil
import statistics
import zipfile

import markdown


def local(value):
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folder')
    parser.add_argument('--bootstrap-log')
    args = parser.parse_args()
    folder = Path(args.folder).resolve()
    summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    if len(summary['rounds']) != summary['requested'] or 'finished_utc' not in summary:
        raise RuntimeError('Campaign has not finished; refusing to issue a final report')
    rounds = summary['rounds']
    stress_file = folder / 'single_process_stress/result.json'
    stress = json.loads(stress_file.read_text(encoding='utf-8')) if stress_file.exists() else None
    stress_ok = (stress['exit_code'] == 0 and stress['cycles_completed'] == 300 and stress['pass_marker']) if stress else True
    good = sum(row['qualified'] for row in rounds)
    blocks = Counter(action for row in rounds for action in row['guard_blocks_outside_probe'])
    details, slow = [], defaultdict(list)
    for row in rounds:
        directory = folder / f"run_{row['round']:02d}"
        payload = json.loads((directory / 'result.json').read_text(encoding='utf-8')) if (directory / 'result.json').exists() else {}
        row['subtest_failed'] = sum(r['outcome'] == 'failed' for r in payload.get('subtests', []))
        console = (directory / 'console.log').read_text(encoding='utf-8', errors='replace')
        row['fatal_markers'] = [term for term in ('Fatal Python error', 'Windows fatal exception',
            'QThread: Destroyed while thread is still running') if term in console]
        for record in payload.get('reports', []):
            if record['phase'] == 'call': slow[record['nodeid']].append(record['seconds'])
        details.append(row)
    durations = [row['seconds'] for row in rounds]
    elapsed = (datetime.fromisoformat(summary['finished_utc']) - datetime.fromisoformat(summary['started_utc'])).total_seconds()
    passed = sum(row.get('passed', 0) for row in rounds)
    subtests = sum(row.get('subtests', 0) for row in rounds)
    failed = sum(row.get('failed', 0) + row['subtest_failed'] for row in rounds)
    skipped = sum(row.get('skipped', 0) for row in rounds)
    fatal = sum(bool(row['fatal_markers']) for row in rounds)
    hashes = json.loads((folder / 'source_manifest.json').read_text(encoding='utf-8'))
    project = Path(__file__).resolve().parents[1]
    application = {name: digest for name, digest in hashes.items()
        if name in {'main.py', 'launch_app.py'} or name.replace('\\', '/').startswith(
            ('core/', 'data/', 'devices/', 'ui/', 'resources/'))}
    changed = [name for name, digest in application.items()
        if not (project / name).is_file() or hashlib.sha256((project / name).read_bytes()).hexdigest() != digest]
    (folder / 'application_source_verification.json').write_text(json.dumps(
        dict(files=len(application), changed=changed, unchanged=not changed), ensure_ascii=False, indent=2), encoding='utf-8')
    protected_after = {name: hashlib.sha256((project / name).read_bytes()).hexdigest()
        if (project / name).is_file() else None for name in summary['protected_before']}
    protected_ok = protected_after == summary['protected_before']
    (folder / 'post_campaign_integrity.json').write_text(json.dumps(dict(
        before=summary['protected_before'], after=protected_after, unchanged=protected_ok), indent=2), encoding='utf-8')
    title = 'BMSFCT 沙盒循环回归测试报告'
    verdict = '通过' if (good == len(rounds) and not failed and not fatal
                         and summary['protected_unchanged'] and protected_ok and not changed and stress_ok) else '存在异常，需处理'
    table = ['| 轮次 | 主测试通过 | 子测试记录 | 失败（含子项） | 跳过 | 退出码 | 超时 | 进程耗时 | 日志 |',
             '|---|---:|---:|---:|---:|---:|---|---:|---|']
    for row in rounds:
        table.append(f"| {row['round']} | {row.get('passed','—')} | {row.get('subtests','—')} | "
            f"{row.get('failed',0)+row['subtest_failed']} | {row.get('skipped',0)} | {row['exit_code']} | "
            f"{'是' if row['timed_out'] else '否'} | {row['seconds']:.2f}s | [原始日志](run_{row['round']:02d}/console.log) |")
    slow_table = ['| 测试项 | 平均调用耗时 | 最大调用耗时 |', '|---|---:|---:|']
    for node, values in sorted(slow.items(), key=lambda item: statistics.mean(item[1]), reverse=True)[:8]:
        slow_table.append(f'| `{node}` | {statistics.mean(values):.3f}s | {max(values):.3f}s |')
    source_zip = folder / 'source_snapshot.zip'
    snapshot = Path(summary['sandbox']) / 'snapshot'
    with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, digest in hashes.items():
            path = snapshot / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError('Frozen source snapshot changed: ' + name)
            archive.write(path, name)
    dependencies = sorted({(dist.metadata.get('Name', ''), dist.version)
                           for dist in importlib.metadata.distributions()})
    (folder / 'dependency_versions.json').write_text(json.dumps(dependencies, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.bootstrap_log:
        shutil.copy2(args.bootstrap_log, folder / 'bootstrap_environment_error.log')
    first = rounds[0]
    stress_text = (f"另在一个持续运行的Python/Qt进程中完成 **{stress['cycles_completed']}/300轮** 生命周期循环，每轮执行PLC重复命令及异步准备取消两个场景，共{stress['cycles_completed']*2}次场景执行。耗时{stress['seconds']:.2f}秒，退出码{stress['exit_code']}，通过标记{'存在' if stress['pass_marker'] else '缺失'}。\n\n"
        "使用同样的副本和硬件拦截；这部分单列，未混入完整回归的主测试计数。[单进程压力日志](single_process_stress/console.log)。"
        if stress else '本次未执行附加单进程生命周期压力测试。')
    text = f'''# {title}

测试结论：**{verdict}**。正式 {len(rounds)} 轮中，{good} 轮满足进程正常退出、pytest成功、隔离自检完成且未超时的条件。

## 一、结果摘要

| 指标 | 实测结果 |
|---|---|
| 正式循环 | {good}/{len(rounds)} 轮通过 |
| 主测试执行通过总数 | {passed} 次 |
| 子测试记录总数 | {subtests} 次 |
| 失败 / 跳过 | {failed} / {skipped} |
| 日志中原生致命错误标记 | {fatal} 轮 |
| 单轮进程耗时 | 最短 {min(durations):.2f}s；平均 {statistics.mean(durations):.2f}s；最长 {max(durations):.2f}s |
| 正式测试总墙钟时间 | {elapsed:.2f}s（{elapsed/60:.2f}分钟，含复制与进程启动退出） |
| 生产配置及原始数据库哈希比较 | {'前后一致' if protected_ok else '存在变化，需要核查外部写入'} |
| 应用源码与冻结快照比较 | {len(application)} 个应用文件，{'一致' if not changed else '存在变化'} |

## 二、环境与测试对象

- 开始：{local(summary['started_utc'])}；结束：{local(summary['finished_utc'])}，北京时间。
- 系统：{summary['platform']}。
- Python：{first.get('python','未记录')}。
- PySide6：{first.get('qt','未记录')}；Qt平台为 `offscreen`，不在桌面弹出测试窗口。
- 对象：当前源码快照，共 {summary['source_files']} 个文件；未使用原始生产配置、原始数据库或生产配方。
- 依赖：每轮使用临时解释器目录，依赖从现有虚拟环境读取；版本清单见 [dependency_versions.json](dependency_versions.json)。这不是重新安装依赖的干净系统验证。
- 每轮独立复制源码、生成空系统配置、创建自己的数据库与临时目录，启动新的 Python/Qt 进程；测试顺序固定。

## 三、隔离措施及自检

这是**测试副本隔离加软件访问拦截，不是虚拟机或操作系统级安全沙盒**，不适用于运行不可信代码。

1. 不复制 `config/sys_config.json`、数据库和生产配方；保护原工程写入。
2. 允许 `127.0.0.1/::1` 本机仿真通信；非回环网络连接及解析被阻断。
3. 真实串口和CAN设备构造入口被阻断；VISA入口和特定设备DLL加载也设置拦截。
4. 每轮先主动验证网口、串口、CAN阻断，各3项；正式轮次共 {sum(row['guard_self_checks'] for row in rounds)} 次阻断自检。
5. 测试期间另记录 {sum(blocks.values())} 次访问阻断，按事件分类：{json.dumps(dict(blocks), ensure_ascii=False)}。这些是被阻止的尝试，不代表真实设备已经被访问，也不单独判为业务用例失败。
6. `PYTHONDONTWRITEBYTECODE=1`，不向原依赖目录写入字节码；原配置、备份和数据库的前后SHA-256见 [summary.json](summary.json)。

## 四、逐轮结果

{chr(10).join(table)}

计数说明：主测试次数与子测试次数分别统计，不能把它们称为代码覆盖率或断言数量。JSON中的分阶段报告还包含setup/teardown，表中主测试只统计call阶段。

### 附加单进程生命周期循环

{stress_text}

## 五、覆盖范围

| 业务链 | 本次覆盖方式 |
|---|---|
| 登录、账户、权限、自检 | 入口身份传递、账户异常、自检失败/取消回归 |
| 配方编辑、保存、加载、条码 | 新旧配方、另存、损坏文件、错误目标、空列表、扫码与关闭交错 |
| 测试引擎与工步 | 类型分派、无效输入、重试、取消、未知设备失败、不判断项目不能掩盖执行失败 |
| 仪器控制 | 模拟响应、设置/回读失败、明确OFF、取消后禁止ON、共享控制入口和锁、确认参数恢复 |
| CAN、UDS、EOL、CSC、DTC | 重连、缓存顺序、多帧、流控、错误响应、坏定义、原始异常值与取消 |
| 下载与刷写 | SG400/E2/APP工步、文件选择、回读完整性及比较的模拟测试 |
| 记录与统计 | SQLite异常、迟到提交、Excel文本及原子保存、条码去重 |
| Qt线程生命周期 | 页面销毁、闭包释放、原生线程结束、异步停止退出及子进程探针 |

完整测试名称和每项耗时保存在各轮 `result.json` 与日志；源文件对应参考工程的《软件框架与调用流程》文档。

## 六、耗时较长的测试项

{chr(10).join(slow_table)}

这些耗时是本机隔离回归环境中的测试调用耗时，包含模拟延时、UI构建和测试开销，不能用于宣称真实烧录或仪器控制速度。

## 七、准备阶段及异常说明

- 正式测试前进行了一轮完整隔离预检，744项主测试、244个子测试通过；不计入上述正式10轮。
- 搭建隔离解释器时曾发生一次中文路径 `.pth` 编码错误，解释器没有进入pytest。修复为ASCII转义路径后预检通过；这是测试框架准备故障，不算应用用例失败。原始记录见 [bootstrap_environment_error.log](bootstrap_environment_error.log)。
- 本报告保留所有正式轮次，失败不会被成功复跑覆盖。若出现非零进程退出、超时或原生崩溃，必须单列分析，不能仅凭pytest结束前的通过计数判定整轮成功。

## 八、结论与限制

本次结论只适用于这份源码快照、当前依赖和已有测试集。{'正式循环中没有复现此前的验证进程异常退出。' if good == len(rounds) else '正式循环存在异常，需结合对应轮次日志继续定位。'}

**不能据此认定真实仪器控制成功率100%、Qt永不崩溃或软件无BUG。** 本次没有现场设备、PLC生产联动、真实MES服务、实际下载回读和真实输出电压验收；也未统计行/分支覆盖率、内存泄漏率，未做数小时连续生产及系统重启测试。循环顺序固定，不等于随机故障注入。

未打包、未部署、未上传GitHub。下一步实机验收须逐设备按连接、设置、回读、开启、关闭、超时、取消、重连矩阵记录。

## 九、可追溯附件

- [正式循环原始汇总](summary.json)
- [源码SHA-256清单](source_manifest.json)
- [应用源码一致性核对](application_source_verification.json)
- [全部测试结束后的原配置/数据库一致性核对](post_campaign_integrity.json)
- [已核对哈希的源码快照](source_snapshot.zip)（不含生产配置或数据库）
- [依赖版本](dependency_versions.json)
- 各轮 `console.log`、`result.json`、`guard.jsonl`。

生成时间：{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}。
'''
    (folder / '测试报告.md').write_text(text, encoding='utf-8')
    body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>
<style>body{{font:15px/1.75 "Microsoft YaHei",sans-serif;color:#243247;background:#eef2f6;margin:0}}main{{max-width:1120px;margin:28px auto;padding:36px;background:white;border-radius:10px}}h1{{font-size:28px;color:#12365a}}h2{{margin-top:32px;border-bottom:2px solid #dde7f0;padding-bottom:8px;font-size:21px}}table{{border-collapse:collapse;width:100%;font-size:13px;margin:18px 0;overflow-wrap:anywhere}}th,td{{border:1px solid #d7e0e9;padding:9px;text-align:left}}th{{background:#e9f0f7}}tr:nth-child(even){{background:#f8fafc}}code{{font-size:12px;overflow-wrap:anywhere}}a{{color:#165c9b}}@media print{{body{{background:white}}main{{margin:0;padding:0}}h2{{break-after:avoid}}tr{{break-inside:avoid}}}}</style><main>{body}</main></html>'''
    (folder / '测试报告.html').write_text(html, encoding='utf-8')
    with zipfile.ZipFile(folder.with_suffix('.zip'), 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in folder.rglob('*'):
            if path.is_file(): archive.write(path, str(path.relative_to(folder)))
    print(json.dumps(dict(verdict=verdict, rounds=good, requested=len(rounds), passed=passed,
        subtests=subtests, failed=failed, skipped=skipped, fatal_rounds=fatal,
        mean_seconds=statistics.mean(durations), protected_unchanged=protected_ok,
        stress_cycles=stress['cycles_completed'] if stress else 0), ensure_ascii=False))


if __name__ == '__main__':
    main()
