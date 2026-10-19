"""Reject the Qt binding race fixed upstream as PYSIDE-3246."""
import re


def validate_qt_versions(pyside, shiboken, qt):
    def version(text):
        match=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)',text)
        if not match:raise RuntimeError('无法确认Qt运行库版本：'+text)
        return tuple(map(int,match.groups()))
    versions=[version(text) for text in (pyside,shiboken,qt)]
    if len(set(versions))!=1:
        raise RuntimeError(f'Qt运行库版本不一致：PySide6={pyside}，Shiboken={shiboken}，Qt={qt}')
    if versions[0]<(6,10,2):
        raise RuntimeError(f'当前PySide6 {pyside}不符合本项目运行要求。'
            '6.10.1存在已确认的多线程原生崩溃缺陷PYSIDE-3246，请使用项目虚拟环境中的6.10.3。')
    return True
