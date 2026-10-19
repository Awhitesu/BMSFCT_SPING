"""Independent archive and third-party MES delivery directories."""
import os
from pathlib import Path

FIELDS=('report_root_dir','mes_report_dir','report_equipment_no','report_station_name','report_position1','report_position2')

def default_report_root():
    return str(Path.home()/'Documents'/'BMSFCT'/'测试报告')

def settings_from(config):
    result={key:str(config.get(key,'') or '').strip() for key in FIELDS}
    result['report_root_dir']=result['report_root_dir'] or default_report_root()
    result['report_equipment_no']=result['report_equipment_no'] or str(config.get('mes_equipment_no','') or '')
    return result

def validate_settings(config):
    result=settings_from(config)
    for key,label in [('report_root_dir','测试报告目录'),('mes_report_dir','MES保存目录')]:
        value=result[key]
        if key=='mes_report_dir' and not value:continue
        if not os.path.isabs(value):raise ValueError(f'{label}必须为绝对路径，可使用本地盘或网络共享目录。')
        if any(ord(c)<32 or c in '<>"|?*' for c in value):raise ValueError(f'{label}含无效字符。')
    return result
