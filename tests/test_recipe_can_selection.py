import time
import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from ui.tabs.overview_tab import OverviewTab
from core.engine import TestEngine as Engine
from core.dbc_signals import DBCMonitor
from test_dbc_signals import asset

app = QApplication.instance() or QApplication([])


def pump(predicate):
    end=time.monotonic()+3
    while not predicate() and time.monotonic()<end:
        app.processEvents();time.sleep(.002)
    assert predicate()


def test_selection_applies_in_background_and_reports_failure():
    page=OverviewTab(Engine())
    page.set_recipe_selection_role('admin', True)
    page.plc_signal_timer.stop()
    entered,release=threading.Event(),threading.Event()
    def apply(recipe):
        entered.set(); release.wait(2); return False,'offline'
    page._apply_recipe_hardware=apply
    try:
        with patch.object(QMessageBox,'warning') as warning:
            assert page.apply_selected_recipe_can({'can_config':{'channel':'PCAN_USBBUS1'}})
            assert entered.wait(1)
            assert page.preparing and not page.combo_recipe.isEnabled()
            assert not page.apply_selected_recipe_can({})
            release.set();pump(lambda:not page._prepare_task.isRunning())
            assert page._recipe_can_error == 'offline'
            assert page.combo_recipe.isEnabled()
            warning.assert_called_once()
    finally:
        release.set();page.begin_shutdown();pump(lambda:not page._prepare_task.isRunning());page.close()


@pytest.mark.parametrize('channel', ['PCAN_USBBUS1', 'ZLG_CAN2'])
def test_unchanged_can_is_reused_but_changed_timing_reconnects(channel):
    driver=Mock();driver.channel=channel;driver.nominal_bitrate=500000;driver.data_bitrate=2000000;driver.fd_enabled=True
    driver.bus=object();driver.is_connected=True;driver.disconnect.return_value=True;driver.connect.return_value=True
    driver.configure.side_effect=lambda **values:[setattr(driver,k,v) for k,v in values.items()]
    page=SimpleNamespace(_prepare_cancel=threading.Event(),engine=SimpleNamespace(device_manager=SimpleNamespace(pcan=driver)))
    recipe={'can_config':dict(channel=channel,mode='CAN-FD',nominal_bitrate=500000,data_bitrate=2000000)}
    assert OverviewTab._apply_recipe_hardware(page,recipe)[0]
    assert OverviewTab._apply_recipe_hardware(page,recipe)[0]
    assert driver.connect.call_count==1
    recipe['can_config']['nominal_bitrate']=250000
    assert OverviewTab._apply_recipe_hardware(page,recipe)[0]
    assert driver.connect.call_count==2 and driver.nominal_bitrate==250000
    driver.is_connected=False
    assert OverviewTab._apply_recipe_hardware(page,recipe)[0]
    assert driver.connect.call_count==3


def zlg_recipe_state():
    driver = Mock()
    driver.channel = 'ZLG_CAN2'
    driver.nominal_bitrate, driver.data_bitrate, driver.fd_enabled = 500000, 2000000, True
    driver.lock = threading.RLock()
    driver.is_connected = True
    driver.disconnect.return_value = driver.connect.return_value = True
    driver.configure.side_effect = lambda **values: [setattr(driver, key, value) for key, value in values.items()]
    page = SimpleNamespace(_prepare_cancel=threading.Event(), db_manager=None, preparation_log=Mock(),
                           engine=SimpleNamespace(device_manager=SimpleNamespace(pcan=driver)))
    page._apply_recipe_hardware = lambda recipe: OverviewTab._apply_recipe_hardware(page, recipe)
    recipe = dict(_debug_run=True, can_config=dict(channel='ZLG_CAN2', mode='CAN-FD',
        nominal_bitrate=500000, data_bitrate=2000000,
        zlg=dict(ip='192.0.2.10', port=8000, send_channel=2,
                 channels=[dict(enabled=True, nominal=500000, data=2000000, termination=False)
                           for _ in range(4)])))
    return page, driver, recipe


def test_zlg_selected_recipe_connection_is_reused_for_repeated_test_preparation():
    page, driver, recipe = zlg_recipe_state()
    with patch.object(OverviewTab, '_apply_recipe_dbc') as apply_dbc:
        assert page._apply_recipe_hardware(recipe)[0]
        assert OverviewTab._prepare_run(page, recipe, '')[0]
        assert OverviewTab._prepare_run(page, recipe, '')[0]
        assert apply_dbc.call_count == 3
    driver.disconnect.assert_called_once()
    driver.configure.assert_called_once()
    driver.connect.assert_called_once()
    driver.bus.configure_channels.assert_called_once_with(recipe['can_config']['zlg']['channels'], False)


@pytest.mark.parametrize(('field', 'value'), [
    ('ip', '192.0.2.11'), ('port', 8001), ('channel', 'ZLG_CAN3'),
    ('enabled', False), ('nominal', 250000), ('data', 1000000), ('termination', True),
])
def test_zlg_recipe_parameter_changes_reconnect_and_apply_all_channels(field, value):
    page, driver, recipe = zlg_recipe_state()
    assert page._apply_recipe_hardware(recipe)[0]
    changed = deepcopy(recipe)
    config = changed['can_config']
    if field == 'channel':
        config[field] = value
        config['zlg']['send_channel'] = int(value.removeprefix('ZLG_CAN'))
    elif field in ('ip', 'port'):
        config['zlg'][field] = value
    else:
        config['zlg']['channels'][2][field] = value
    assert page._apply_recipe_hardware(changed)[0]
    assert driver.connect.call_count == driver.disconnect.call_count == 2
    assert driver.bus.configure_channels.call_count == 2
    driver.bus.configure_channels.assert_called_with(config['zlg']['channels'], False)
    assert driver.zlg_ip == config['zlg']['ip'] and driver.zlg_port == config['zlg']['port']
    assert driver.channel == config['channel']


@pytest.mark.parametrize('state_change', ['disconnected', 'replaced_bus'])
def test_zlg_connection_changes_reconnect_with_unchanged_recipe(state_change):
    page, driver, recipe = zlg_recipe_state()
    assert page._apply_recipe_hardware(recipe)[0]
    if state_change == 'disconnected':
        driver.is_connected = False
    else:
        driver.bus = Mock()
    driver.bus.configure_channels.reset_mock()
    assert page._apply_recipe_hardware(recipe)[0]
    assert driver.connect.call_count == 2
    driver.bus.configure_channels.assert_called_once_with(recipe['can_config']['zlg']['channels'], False)


def test_zlg_reuse_preserves_dbc_subscription_and_switches_recipe_context(asset):
    page, driver, recipe = zlg_recipe_state()
    monitor = DBCMonitor()
    page.engine.device_manager.dbc_monitor = monitor
    recipe.update(name='ZLG-first', dbc=asset)
    try:
        assert page._apply_recipe_hardware(recipe)[0]
        assert OverviewTab._prepare_run(page, recipe, '')[0]
        driver.add_message_listener.assert_called_once()
        driver.remove_message_listener.assert_not_called()
        assert monitor.asset == asset and monitor.context_key == 'ZLG-first'

        changed = deepcopy(recipe)
        changed['name'] = 'ZLG-second'
        assert page._apply_recipe_hardware(changed)[0]
        assert monitor.asset == asset and monitor.context_key == 'ZLG-second'
        assert driver.add_message_listener.call_count == 2
        driver.remove_message_listener.assert_called_once()
        driver.connect.assert_called_once()
        driver.bus.configure_channels.assert_called_once()
    finally:
        monitor.stop()
