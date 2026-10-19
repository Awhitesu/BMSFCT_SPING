# -*- coding: utf-8 -*-
"""软件界面名称（窗口标题/主标题）的统一读取，默认值集中在此。"""

DEFAULT_WINDOW_TITLE = "BMS软件注入&APP刷写系统"
DEFAULT_HEADER_TITLE = "APP刷写系统"


def load_titles(db_manager):
    """从系统配置读取界面名称；缺省或读取失败时回落到默认名称。"""
    cfg = {}
    try:
        if db_manager:
            cfg = db_manager.load_sys_config() or {}
    except Exception:
        cfg = {}
    window_title = str(cfg.get('ui_window_title') or '').strip() or DEFAULT_WINDOW_TITLE
    header_title = str(cfg.get('ui_header_title') or '').strip() or DEFAULT_HEADER_TITLE
    return window_title, header_title
