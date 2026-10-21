"""One GitHub-rendered page for source checkpoints and packaged releases."""
import json
import subprocess
from pathlib import Path

REPOSITORY='https://github.com/pingsong1419-boop/BMSFCT'


def git(root,*args):
    if not (Path(root)/'.git').exists():return ''
    try:result=subprocess.run(['git',*args],cwd=root,capture_output=True,encoding='utf-8',errors='replace')
    except OSError:return ''
    return result.stdout.strip() if result.returncode==0 else ''


def source_info(root):
    commit=git(root,'rev-parse','HEAD')
    paths=('core','devices','ui','resources','main.py','requirements.txt','packaging','tools')
    dirty=bool(git(root,'status','--porcelain','--',*paths))
    previous=next((row.get('source_commit') for row in reversed(load(root)) if row.get('source_commit')),None)
    subjects=git(root,'log','--format=%s',f'{previous}..HEAD').splitlines() if previous else []
    return dict(source_commit=commit or None,source_dirty=dirty,
                source_subject=git(root,'log','-1','--format=%s'),source_changes=subjects)


def load(root):
    path=Path(root)/'releases/history.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def cell(value):
    return str(value or '未记录').replace('|','／').replace('\n','；').replace('\r','')


def render(root):
    root=Path(root)
    lines=['# 版本历史与回滚索引','',
        '本页集中展示源码版本和已登记的安装包版本。**源码检查点不等于已打包EXE，也不代表整机实测验收。**','',
        '## 源码版本（最新在前）','',
        '| 源码版本 | 日期 | 本次更新 | 查看／回滚代码 |',
        '|---|---|---|---|']
    history=git(root,'log','--reverse','--format=%H%x09%cs%x09%s').splitlines()
    notes_path=root/'releases/source_notes.json'
    notes=json.loads(notes_path.read_text(encoding='utf-8')) if notes_path.exists() else {}
    counts={};sources=[]
    for row in history:
        parts=row.split('\t',2)
        if len(parts)!=3:continue
        commit,date,subject=parts;counts[date]=counts.get(date,0)+1
        sources.append((f'S{date.replace("-", ".")}.{counts[date]}',date,subject,commit))
    for version,date,subject,commit in reversed(sources):
        subject=next((text for prefix,text in notes.items() if commit.startswith(prefix)),subject)
        lines.append(f'| {version} | {date} | {cell(subject)} | [{commit[:7]}]({REPOSITORY}/commit/{commit}) · [下载源码]({REPOSITORY}/archive/{commit}.zip) |')
    lines+=['','## 安装包版本（最新在前）','',
        '以下只登记找到构建清单、历史发布记录或版本文件的版本；未找到记录的旧编号不编造更新内容。历史缺口仍需补充原安装包或构建记录。','',
        '| EXE版本 | 构建时间 | 更新内容 | 验证／限制 | 源码及回滚位置 | EXE SHA256（前12位） |',
        '|---|---|---|---|---|---|']
    entries=sorted(load(root),key=lambda r:tuple(int(n) for n in r['app_version'].split('.')),reverse=True)
    for row in entries:
        commit=row.get('source_commit')
        source=f'[{commit[:7]}]({REPOSITORY}/commit/{commit})' if commit else '未记录对应提交'
        if row.get('source_dirty'):source+='；含未提交改动，不能仅按此提交精确还原EXE'
        source+='；'+row.get('rollback','使用已归档的同版本完整安装目录；若无安装包归档，只能按源码重新构建')
        checksum=(row['sha256'][:12]+'…') if row.get('sha256') else '未保留'
        lines.append(f'| V{row["app_version"]} | {cell(row.get("built_at"))} | {cell("；".join(row.get("notes",[])))} | {cell(row.get("validation"))} | {source} | `{checksum}` |')
    lines+=['','## 如何选择回滚版本','',
        '1. 按问题发生前的功能和验证记录选择目标行；S开头为源码版本，V开头为EXE版本。',
        '2. 回滚源码：点击该行“下载源码”，解压到独立目录后重新构建。不要在正在使用的工位直接重置工作目录。',
        '3. 回滚EXE：使用已归档的对应版本完整目录，核对build-info.json和完整SHA256（releases/history.json中保存完整值）。仅有源码提交不代表能够还原历史EXE。',
        '4. 先备份并保留现场数据库、config和recipes；存在配置结构或通道映射变化时，先在测试工位核对兼容性。',
        '5. 标为未记录／待补全的历史版本不能据此确认完整更新范围或精确回滚；GitHub未上传的安装包不能在本页直接下载。','',
        '## 后续登记规则','',
        '- 每次打包成功，tools/package_app.py自动更新本页及releases/history.json；失败的构建不登记成功版本。',
        '- 打包时可重复传入--release-note填写每条改动；未填写时采用最近的Git提交说明，并标记未提交源码。',
        '- 打包后将本页和history.json一起提交到GitHub。源码提交前运行python tools/version_history.py刷新源码检查点。',
        '- 旧版本缺失的记录只补充已知事实，不推测EXE与Git提交的对应关系。','']
    (root/'VERSION_HISTORY.md').write_text('\n'.join(lines),encoding='utf-8')


def record_build(root,metadata,notes=None):
    root=Path(root);entries=load(root)
    row={k:metadata[k] for k in ('app_version','built_at','sha256','source_commit','source_dirty') if k in metadata}
    row['notes']=list(notes or metadata.get('source_changes') or [metadata.get('source_subject') or '未提供人工更新说明'])
    if metadata.get('source_dirty'):row['notes'].append('构建包含未提交源码；具体代码以本次构建快照为准')
    row['validation']='打包及资源检查通过；不代表硬件验收'
    entries=[old for old in entries if old['app_version']!=row['app_version']]+[row]
    path=root/'releases/history.json';path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(entries,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)
    render(root)


if __name__=='__main__':
    render(Path(__file__).resolve().parents[1])
