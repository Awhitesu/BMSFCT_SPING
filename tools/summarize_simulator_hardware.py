"""Summarize recorded device operations; never connects to hardware."""
import argparse
import json
import math
from pathlib import Path
import re
import statistics


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder');parser.add_argument('--report',required=True)
    args=parser.parse_args();folder=Path(args.folder)
    units=sorted(json.loads((folder/'summary.json').read_text(encoding='utf-8')),key=lambda r:r['ip'])
    operations=[];retry_ops=[];queries=0
    for path in sorted(folder.glob('*_scpi.jsonl')):
        segment=[]
        for line in path.read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            if row['kind']=='TX':segment.extend(row['data'].splitlines());queries+=row['data'].count('?')
            if row['kind']=='OPERATION':
                operations.append(dict(row,ip=path.stem.removesuffix('_scpi')))
                prefix={'single_voltage_to_measured':'VOLT','set_current_limit':'OUTCURR','set_range':'RANG'}.get(row['op'])
                if prefix and row.get('channel'):
                    count=sum(bool(re.match(fr'SOUR{row["channel"]}:{prefix}\s',command)) for command in segment)
                    if count>1:retry_ops.append(dict(ip=path.stem,op=row['op'],channel=row['channel'],writes=count))
                segment=[]
    labels={'connect':'连接并握手','set_range':'单通道量程设置及确认','set_current_limit':'单通道限流设置及确认',
        'read_current_limit':'单通道限流与量程回读','single_voltage_to_measured':'单通道设压至实测达标',
        'read_voltage':'单通道电压回读','read_current_open_circuit':'单通道空载电流回读',
        'batch_set_voltage':'24通道批量设压及设定值确认','all_output_on_measured':'24通道开启并确认实测电压',
        'all_output_off':'24通道关闭并确认OFF','read_all_voltage_measured':'24通道电压/设定值/输出状态快照'}
    report=['# 三台电池模拟器空载控制稳定性及响应时间报告','',
        '测试对象：192.168.1.110、192.168.1.111、192.168.1.112，端口7000，每台24通道，共72通道。',
        '设备握手均为 `NGI,N83624,0,H2.00S1.42`。调用当前源码的正式 `NGI83624` 驱动，三台独立线程同时测试；不是仿真总线。','',
        '## 条件与成功判据','',
        '- 用户确认空载，并授权电压1.0/2.5/4.2V，开关输出及量程测试。HIGH/AUTO限流10/50/100mA；根据用户补充的小量程1mA，LOW使用0.1/0.5/1mA。',
        '- 设置量程及限流均要求设定值回读一致；开启还要求仪器实测电压在目标±0.05V内，不能只以ON为成功。',
        '- 空载电流筛查阈值为绝对值小于1mA，这是本次测试阈值，不是厂家精度指标。',
        '- 关闭成功以输出状态OFF确认，耗时不代表端子放电到0V的时间。',
        '- 所有时间为电脑端调用到确认结果的耗时，包含网络、驱动等待和查询，不等于示波器测得的电气上升沿。','',
        '## 稳定性结果','',
        '| IP | 完成循环 | 功能与循环检查数 | 失败 | 用时(s) | 最终24通道全部OFF |',
        '|---|---:|---:|---:|---:|---|']
    for u in units:
        report.append(f'| {u["ip"]} | {u["completed_cycles"]}/{u["requested_cycles"]} | {u["operations"]} | {u["failures"]} | {u["elapsed_s"]} | {u["cleanup"].get("all_outputs_confirmed_off",False)} |')
    total=len(operations);failures=sum(not r['ok'] for r in operations)
    completed=sum(u['completed_cycles'] for u in units)
    report+=['',f'本轮总计 {total} 项检查，失败 {failures} 项。计划每台100次全通道开关及电压循环，实际通过整机快照确认{completed}台次；成功快照包含{completed*24}个通道电压点，另有逐通道测试及开启电压确认。',
        f'日志统计到 {queries} 条查询（包含基线和恢复）。逐通道设置中检测到 {len(retry_ops)} 项发生重复写入确认。','',
        '## 响应时间（毫秒，三台合并）','',
        '| 操作 | 样本数 | 平均 | P50 | P95 | 最大 |',
        '|---|---:|---:|---:|---:|---:|']
    aggregate={}
    for name,label in labels.items():
        values=sorted(r['ms'] for r in operations if r['op']==name and r['ok'])
        if not values:continue
        metrics=dict(samples=len(values),mean_ms=round(statistics.mean(values),3),p50_ms=round(statistics.median(values),3),
            p95_ms=values[math.ceil(len(values)*.95)-1],max_ms=max(values))
        aggregate[name]=metrics
        report.append(f'| {label} | {len(values)} | {metrics["mean_ms"]} | {metrics["p50_ms"]} | {metrics["p95_ms"]} | {metrics["max_ms"]} |')
    voltage_errors=[abs(r['result']-r['target_V']) for r in operations if r['op']=='single_voltage_to_measured' and r['ok']]
    current_values=[r['result'] for r in operations if r['op']=='read_current_open_circuit' and r['ok']]
    report+=['',f'通过的逐通道设压样本最大仪器自回读偏差：{max(voltage_errors):.6f}V（不包含下述失败快照）。空载电流自回读范围：{min(current_values):.6f}～{max(current_values):.6f}mA。','',
        '## 发现与边界','',
        '- 初始轮次把10/50/100mA也用于LOW，超出了用户随后确认的小量程范围，回读不符。这些超量程尝试保留在run1/run2日志中，不计入本轮有效稳定性通过率，不能据此认定驱动故障。',
        '- HIGH/AUTO补充轮次另完成各100次循环，无新增有效范围内失败；本报告主表仅统计修正LOW测试点后的run3。',
        '- 正式驱动的单通道set_voltage包含固定50ms等待，当前单通道响应时间包含它。批量设置采用另一条路径，不能直接按单通道时间乘24估算。',
        '- 此为空载短时验收；未接电子负载、外部万用表或示波器，因此未验证实际限流点、带载精度、电流校准精度或微秒级电气响应，也不能保证长期零故障。',
        '- 测试后恢复原电压设定、限流和量程，并逐通道确认输出关闭。','',
        '原始证据目录：`'+str(folder)+'`。每台包含SCPI收发日志、操作耗时及最终清理结果。','',
        '厂家资料入口：[NGI N83624](https://www.ngi-tech.com/Unidirectional-current/n83624-24-channels-battery-simulator-6v15vch)。厂家给出的动态响应指标与本报告的电脑端SCPI控制耗时属于不同测试条件。']
    report+=['','## 未通过的原始证据','']
    for row in operations:
        if not row['ok']:report.append(f'- {row["ip"]}，循环{row.get("cycle","--")}（从0开始），{row["op"]}：{row["error"]}')
    output=Path(args.report);output.parent.mkdir(parents=True,exist_ok=True);output.write_text('\n'.join(report)+'\n',encoding='utf-8')
    (folder/'aggregate_metrics.json').write_text(json.dumps(dict(checks=total,failures=failures,metrics=aggregate,repeated_writes=retry_ops),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(checks=total,failures=failures,report=str(output),metrics=aggregate),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
