"""Shared online UDS connection checks and immutable editor state."""
from PySide6.QtWidgets import (QWidget, QLineEdit, QComboBox, QCheckBox, QSpinBox,
                              QDoubleSpinBox, QPushButton, QTreeWidget, QTableWidget, QGroupBox)


def connected_recipe_driver(page):
    binding = page.recipe_can_binding or {}
    if binding.get('enabled') is False:
        raise ValueError('配方未绑定CAN卡')
    driver = getattr(page.device_manager, 'pcan', None)
    if not driver:
        raise ValueError('请先应用配方并连接CAN卡')
    if callable(getattr(type(driver), 'check_bus_health', None)):
        driver.check_bus_health()
    if not driver.is_connected:
        raise ValueError(driver.last_error or '请先应用配方并连接CAN卡')
    channel = getattr(driver, 'channel', '')
    if binding.get('channel') and isinstance(channel, str):
        if channel.startswith('ZLG_CAN') != binding['channel'].startswith('ZLG_CAN'):
            raise ValueError('已连接CAN卡与配方不一致，请先应用配方配置')
    for key in ('nominal_bitrate', 'data_bitrate'):
        actual = getattr(driver, key, None)
        if key in binding and type(actual) is int and actual != int(binding[key]):
            raise ValueError('CAN波特率与配方不一致，请先应用配方配置：'+key)
    modes = {'经典 CAN': 0, 'CAN-FD': 1, 'CAN-FD + BRS': 2}
    expected = modes.get(binding.get('mode'))
    actual = getattr(driver, 'default_can_type', None)
    if expected is not None and type(actual) is int and actual != expected:
        raise ValueError('CAN报文模式与配方不一致，请先应用配方配置')
    for name in ('uds_workbench', 'uds_field_write_form'):
        editor = getattr(page, name, None)
        if editor is not None and editor.task.isRunning():
            raise ValueError('另一个UDS在线操作正在运行，请先停止或等待完成')
    node = page
    while node is not None:
        engine = getattr(node, 'test_engine', None)
        if getattr(node, '_run_busy', False) is True or (engine is not None and isinstance(getattr(engine, 'workers', None), dict) and engine.workers):
            raise ValueError('测试正在运行，不能同时执行在线UDS写入')
        parent = getattr(node, 'parentWidget', None)
        node = parent() if callable(parent) else None
    return driver


def freeze_controls(page, root, stop):
    types = (QLineEdit, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox,
             QPushButton, QTreeWidget, QTableWidget, QGroupBox)
    widgets = [widget for widget in root.findChildren(QWidget)
               if isinstance(widget, types) and widget is not stop
               and not (isinstance(widget, QGroupBox) and widget.isAncestorOf(stop))]
    for name in ('category_combo', 'sub_category_combo', 'device_combo', 'action_combo',
                 'can_physical_channel', 'btn_ok'):
        widget = getattr(page, name, None)
        if widget is not None and widget not in widgets:
            widgets.append(widget)
    snapshot = [(widget, widget.isEnabled()) for widget in widgets]
    for widget, _ in snapshot:
        widget.setEnabled(False)
    stop.setEnabled(True)
    return snapshot


def restore_controls(snapshot, stop):
    for widget, enabled in snapshot:
        widget.setEnabled(enabled)
    stop.setEnabled(False)
