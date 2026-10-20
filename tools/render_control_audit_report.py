"""Build a local, searchable report from completed per-control audits."""
import html
import json
from pathlib import Path


def main():
    root=Path(__file__).resolve().parents[1]
    folders=[root/'logs'/f'controls_layout_20261018_{scale}' for scale in ('1','1.25','1.5','2')]
    summaries=[json.loads((folder/'summary.json').read_text(encoding='utf-8')) for folder in folders]
    for summary in summaries:
        assert summary['findings']==0,summary
    output=root/'doc'/'audits'/'控件布局逐项验收_20261018.html'
    inventory=[]
    for folder,summary in zip(folders,summaries):
        for line in (folder/'controls.jsonl').read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            inventory.append([summary['scale'],row['path'],row['widget'],row['cls'],
                              str(row['rect']),row['text'],row.get('hit_region','显示/禁用检查'),','.join(row['issues'])])
    sources=json.loads((folders[0]/'source_coverage.json').read_text(encoding='utf-8'))
    sites=json.loads((folders[0]/'popup_sites.json').read_text(encoding='utf-8'))
    assert all(row['runtime_observed'] for row in sources)
    total=sum(summary['observations'] for summary in summaries)
    states=sum(summary['states'] for summary in summaries)
    tables=''.join(f'<tr><td>{html.escape(s["scale"])}</td><td>{s["device_pixel_ratio"]}</td><td>{s["states"]}</td><td>{s["observations"]}</td><td>{s["findings"]}</td></tr>' for s in summaries)
    source_rows=''.join(f'<tr><td>{html.escape(s["name"])}</td><td>{html.escape(s["file"])}:{s["line"]}</td><td>已构造并检查</td></tr>' for s in sources)
    data=json.dumps(inventory,ensure_ascii=False).replace('</','<\/')
    content='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>控件布局逐项验收</title>
<style>body{font:15px system-ui,sans-serif;max-width:1500px;margin:32px auto;padding:0 20px;color:#172033;background:#f4f6f8}h1{font-size:28px}section{background:white;padding:20px;margin:18px 0;border:1px solid #dbe1e8;border-radius:8px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #dde3eb;padding:8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#edf2f8;position:sticky;top:0}input{padding:10px;width:60%;margin-bottom:12px}button,select{padding:9px}code{background:#eef2f7}.scroll{max-height:650px;overflow:auto}.note{color:#516078}img{max-width:100%}</style>
<h1>控件布局逐项验收 · 2026-10-18</h1>
<section><p>完成 SOURCECOUNT 个自定义界面类、SITECOUNT 个临时弹窗调用点模板、STATECOUNT 个页面/状态场景，共 TOTALCOUNT 次控件检查。最终自动检测发现项为 0。</p>
<p>检查：文字尺寸、兄弟控件重叠、滚动后可达区域、控件命中区域、禁用状态、表格单元格、分页和动态配置。独立运行验证四种设备像素比。</p>
<p class="note">这些数字是状态与控件的检查次数，并非不同控件的唯一数量。布局验证使用替身、临时数据库和离线状态；没有点击真实输出按钮或验证仪器通信。临时提示框按源码文本模板加动态占位值重建；文件选择器验证 Qt 非原生版本，未冒充 Windows 原生版本的逐像素验收。</p>
<table><tr><th>缩放</th><th>实际 DPR</th><th>场景</th><th>控件检查</th><th>发现项</th></tr>SUMMARYTABLE</table></section>
<section><h2>逐项控件清单</h2><input id="q" placeholder="搜索设备、控件、状态或文字"><select id="scale"><option value="">全部缩放</option><option>1</option><option>1.25</option><option>1.5</option><option>2</option></select><p id="count"></p><div class="scroll"><table><thead><tr><th>缩放</th><th>页面/状态</th><th>控件</th><th>类型</th><th>坐标/尺寸</th><th>文字</th><th>可达性</th><th>发现项</th></tr></thead><tbody id="rows"></tbody></table></div><p><button id="prev">上一页</button> <span id="page"></span> <button id="next">下一页</button> 每页200项</p></section>
<section><h2>源码界面类覆盖</h2><table><tr><th>类</th><th>文件/行</th><th>检查</th></tr>SOURCETABLE</table></section>
<script id="data" type="application/json">DATAJSON</script><script>
const data=JSON.parse(document.getElementById('data').textContent);let page=0,filtered=data;
function render(){const q=document.getElementById('q').value.toLowerCase(),s=document.getElementById('scale').value;filtered=data.filter(r=>(!s||r[0]===s)&&(!q||r.join(' ').toLowerCase().includes(q)));let max=Math.max(0,Math.ceil(filtered.length/200)-1);page=Math.min(page,max);const body=document.getElementById('rows');body.replaceChildren();for(const row of filtered.slice(page*200,(page+1)*200)){const tr=document.createElement('tr');for(const text of row){const td=document.createElement('td');td.textContent=text;tr.appendChild(td)}body.appendChild(tr)}document.getElementById('count').textContent='匹配 '+filtered.length+' 项';document.getElementById('page').textContent=(page+1)+' / '+(max+1)}
document.getElementById('q').oninput=()=>{page=0;render()};document.getElementById('scale').onchange=()=>{page=0;render()};document.getElementById('prev').onclick=()=>{page=Math.max(0,page-1);render()};document.getElementById('next').onclick=()=>{page++;render()};render();</script></html>'''
    for key,value in dict(SOURCECOUNT=str(len(sources)),SITECOUNT=str(len(sites)),STATECOUNT=str(states),TOTALCOUNT=str(total),SUMMARYTABLE=tables,SOURCETABLE=source_rows,DATAJSON=data).items():
        content=content.replace(key,value)
    output.write_text(content,encoding='utf-8')
    (root/'logs/controls_layout_20261018_summary.json').write_text(json.dumps(dict(summaries=summaries,
        source_classes=len(sources),popup_sites=len(sites),total_observations=total,total_states=states),ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT',output.name,'OBSERVATIONS',total,'STATES',states,'CLASSES',len(sources),'POPUP_SITES',len(sites))


if __name__=='__main__':main()
