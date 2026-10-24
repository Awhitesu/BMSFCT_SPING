import copy
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.dbc_signals import DBCMonitor
from core.engine import TestEngine as Engine
from ui.tabs.overview_tab import OverviewTab
from test_dbc_signals import asset


app = QApplication.instance() or QApplication([])


def page_state(enabled=False):
    driver = Mock()
    driver.is_connected = False
    driver.connect.return_value = False
    driver.disconnect.return_value = True
    driver.last_error = 'The driver id not loaded'
    manager = SimpleNamespace(pcan=driver, dbc_monitor=DBCMonitor(),
                              is_enabled=lambda key: enabled if key == 'pcan' else False)
    page = SimpleNamespace(_prepare_cancel=threading.Event(), db_manager=None,
                           preparation_log=Mock(), engine=SimpleNamespace(device_manager=manager))
    page._apply_recipe_hardware = lambda snapshot: OverviewTab._apply_recipe_hardware(page, snapshot)
    return page, manager, driver


@pytest.mark.parametrize('channel', ['PCAN_USBBUS1', 'ZLG_CAN2'])
@pytest.mark.parametrize('with_dbc', [False, True])
def test_disabled_can_skips_recipe_connection_and_dbc(channel, with_dbc, asset):
    page, manager, driver = page_state()
    recipe = dict(name='disabled-can', can_config=dict(channel=channel), items=[])
    if with_dbc:
        recipe['dbc'] = asset
    saved = copy.deepcopy(recipe)
    ok, message = OverviewTab._apply_recipe_hardware(page, recipe)
    assert ok and '未启用 CAN' in message
    assert recipe == saved
    assert manager.dbc_monitor.asset is None
    assert driver.method_calls == []


def test_disabling_can_stops_existing_dbc_and_reenable_applies_recipe(asset):
    page, manager, driver = page_state()
    driver.is_connected = True
    manager.dbc_monitor.configure(asset, driver, context_key='same-recipe')
    callback = driver.add_message_listener.call_args.args[0]
    driver.reset_mock()
    page._applied_recipe_can = ('old-config', ())
    recipe = dict(name='same-recipe', can_config=dict(channel='PCAN_USBBUS1'), dbc=asset)
    try:
        assert OverviewTab._apply_recipe_hardware(page, recipe)[0]
        assert manager.dbc_monitor.asset is None
        driver.remove_message_listener.assert_called_once_with(callback)
        driver.connect.assert_not_called()
        driver.disconnect.assert_not_called()
        assert page._applied_recipe_can is None

        manager.is_enabled = lambda key: True
        driver.connect.return_value = True
        assert OverviewTab._apply_recipe_hardware(page, recipe)[0]
        driver.configure.assert_called_once()
        driver.connect.assert_called_once()
        assert manager.dbc_monitor.asset == asset
    finally:
        manager.dbc_monitor.stop()


def test_enabled_can_still_reports_connection_failure():
    page, _, driver = page_state(enabled=True)
    ok, message = OverviewTab._apply_recipe_hardware(page, dict(can_config=dict(channel='PCAN_USBBUS1')))
    assert not ok and 'CAN卡连接失败' in message and driver.last_error in message
    driver.connect.assert_called_once()


@pytest.mark.parametrize('device,kind', [('CAN 交互', 'CAN发送'), ('UDS', 'CAN交互'),
    ('3.5H EOL', '3.5HEOL协议'), ('APP刷写', 'APP刷写'), ('CANDBC 交互', 'CANDBC交互')])
def test_disabled_can_blocks_active_can_steps_before_test_preparation(device, kind):
    page, _, driver = page_state()
    recipe = dict(_debug_run=True, can_config=dict(channel='PCAN_USBBUS1'),
                  items=[dict(name='needs-can', sub_steps=[dict(device=device, type=kind)])])
    with pytest.raises(ValueError, match='硬件管理中心未启用 CAN'):
        OverviewTab._prepare_run(page, recipe, '')
    assert driver.method_calls == []


@pytest.mark.parametrize('skip', ['item', 'step', 'item-comment', 'step-comment', 'power-only'])
def test_disabled_can_allows_tests_without_active_can_steps(skip):
    page, _, driver = page_state()
    step = dict(name='can-step', device='CAN 交互', type='CAN发送')
    item = dict(name='can-item', sub_steps=[step])
    if skip == 'item': item['skip_runtime'] = True
    elif skip == 'step': step['skip_runtime'] = True
    elif skip == 'item-comment': item['name'] = '#can-item'
    elif skip == 'step-comment': step['name'] = '#can-step'
    else: step.update(device='IT6722A', type='设置仪表')
    recipe = dict(_debug_run=True, can_config=dict(channel='PCAN_USBBUS1'), items=[item])
    assert OverviewTab._prepare_run(page, recipe, '')[0]
    assert driver.method_calls == []


def test_selecting_recipe_with_disabled_can_clears_error_without_popup(monkeypatch):
    _, manager, driver = page_state()
    widget = OverviewTab(Engine(manager))
    widget.stop_monitor()
    widget._recipe_can_error = 'old CAN failure'
    warning = Mock()
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    try:
        assert widget.apply_selected_recipe_can(dict(can_config=dict(channel='PCAN_USBBUS1')))
        deadline = time.monotonic() + 3
        while widget._prepare_task.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        app.processEvents()
        assert not widget._prepare_task.isRunning() and not widget.preparing
        assert widget._recipe_can_error == ''
        assert widget.lbl_big_status.text() == 'READY'
        warning.assert_not_called()
        assert driver.method_calls == []
    finally:
        widget.begin_shutdown()
        for name in ('_prepare_task', '_plc_task', '_stats_task', '_recipe_match_task'):
            getattr(widget, name).shutdown()
        manager.dbc_monitor.stop()
        widget.close()
