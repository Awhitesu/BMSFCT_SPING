"""Compare frozen function bytecode/constants with source, ignoring build paths."""
import argparse
import hashlib
import json
from pathlib import Path
import types
from PyInstaller.archive.readers import CArchiveReader

MODULES=['core.dbc_signals','ui.widgets.dbc_forms','core.engine','ui.dialogs.step_dialog',
         'ui.tabs.config_tab','ui.tabs.overview_tab','ui.main_window','devices.manager',
         'core.report_settings','core.excel_recorder','ui.widgets.serial_scanner',
         'devices.infoscan_scanner','ui.dialogs.ai_barcode_dialog']


def signature(code):
    return (code.co_code,tuple(signature(c) if isinstance(c,types.CodeType) else c for c in code.co_consts),
            code.co_names,code.co_varnames,code.co_freevars,code.co_cellvars,code.co_flags,
            code.co_argcount,code.co_posonlyargcount,code.co_kwonlyargcount)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--exe',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();exe=Path(args.exe).resolve();root=Path(__file__).resolve().parents[1]
    archive=CArchiveReader(str(exe)).open_embedded_archive('PYZ.pyz')
    rows=[]
    for name in MODULES:
        path=root/(name.replace('.','/')+'.py');text=path.read_text(encoding='utf-8')
        if signature(compile(text,str(path),'exec',optimize=0))!=signature(archive.extract(name)):
            raise AssertionError('Frozen source mismatch: '+name)
        rows.append(dict(module=name,source_sha256=hashlib.sha256(text.encode()).hexdigest(),matched=True))
    manifest=json.loads((exe.parent/'build-info.json').read_text(encoding='utf-8'))
    if hashlib.sha256(exe.read_bytes()).hexdigest()!=manifest['sha256']:raise AssertionError('Executable hash differs from release manifest')
    result=dict(version=manifest['app_version'],matched_modules=len(rows),modules=rows)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Verified',len(rows),'source modules and executable hash for',manifest['app_version'])
