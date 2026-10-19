import threading
from types import SimpleNamespace
from unittest.mock import Mock

from devices.manager import DeviceManager
from ui.tabs.overview_tab import OverviewTab


def manager_for(config):
    manager = DeviceManager.__new__(DeviceManager)
    manager.db_manager = SimpleNamespace(load_sys_config=lambda: config)
    manager.enabled_devices = {'pcan': True}
    manager.rn_boards = {}
    manager._device_step_guard = threading.RLock()
    manager.simulators = []
    for name in ('pcan', 'opteeq', 'e2_rfp', 'easy320', 'ca550', 'hv_source',
                 'dut_power', 'it6333b', 'tbs2102b', 'dg2052', 'dmm34461'):
        setattr(manager, name, None)
    manager.pcan = SimpleNamespace(connect=Mock(return_value=True))
    return manager


def test_bad_board_configuration_does_not_skip_other_devices():
    cfg = {'rn_boards': {'relay1': {'enabled': True, 'ip': ''},
                         'hv1': {'enabled': True, 'ip': '192.0.2.10'}}}
    manager = manager_for(cfg)
    good = SimpleNamespace(setting={'key': 'hv1'}, connect=Mock(return_value=True))
    manager.rn_boards['hv1'] = good
    original = manager.get_rn_board

    def resolve(key, setting=None):
        if key == 'hv1':
            return good
        return original(key, setting)

    manager.get_rn_board = resolve
    logs = []
    assert manager.init_all_devices(logger=logs.append) is False
    manager.pcan.connect.assert_called_once()
    good.connect.assert_called_once()
    assert any('relay1' in line for line in logs)


def test_cancelled_initialization_does_not_reconfigure_existing_boards():
    cfg = {'rn_boards': {'relay1': {'enabled': True, 'ip': '192.0.2.10'}}}
    manager = manager_for(cfg)
    manager.get_rn_board = Mock(side_effect=AssertionError('must not touch board'))
    assert manager.init_all_devices(cancelled=lambda: True) is False
    manager.get_rn_board.assert_not_called()
    manager.pcan.connect.assert_not_called()


def test_initialization_uses_one_configuration_snapshot():
    cfg = {'rn_boards': {'relay1': {'enabled': True, 'ip': '192.0.2.10'}}}
    manager = manager_for(cfg)
    board = SimpleNamespace(connect=Mock(return_value=True))
    manager.get_rn_board = Mock(return_value=board)
    assert manager.init_all_devices() is True
    key, setting = manager.get_rn_board.call_args.args
    assert key == 'relay1'
    assert setting['ip'] == '192.0.2.10'
    board.connect.assert_called_once()


def preparation():
    cancel = threading.Event()
    can = SimpleNamespace(disconnect=Mock(return_value=True), configure=Mock(),
        connect=Mock(return_value=True), CAN_TYPE_CLASSIC=0, CAN_TYPE_FD=1,
        CAN_TYPE_FD_BRS=2)
    page = SimpleNamespace(_prepare_cancel=cancel,
                           engine=SimpleNamespace(device_manager=SimpleNamespace(pcan=can)))
    return page, can, cancel


def test_cancel_during_can_disconnect_does_not_reconnect():
    page, can, cancel = preparation()
    can.disconnect.side_effect = lambda: cancel.set()
    assert OverviewTab._apply_recipe_hardware(page, {'can_config': {'mode': 'CAN-FD'}})[0] is False
    can.configure.assert_not_called()
    can.connect.assert_not_called()


def test_failed_can_disconnect_does_not_reconfigure():
    page, can, _ = preparation()
    can.disconnect.return_value = False
    assert OverviewTab._apply_recipe_hardware(page, {'can_config': {'mode': 'CAN-FD'}})[0] is False
    can.configure.assert_not_called()
    can.connect.assert_not_called()


def test_unknown_can_mode_does_not_silently_fall_back():
    page, can, _ = preparation()
    assert OverviewTab._apply_recipe_hardware(page, {'can_config': {'mode': 'invalid'}})[0] is False
    can.disconnect.assert_not_called()


def test_cancel_during_can_configuration_does_not_connect():
    page, can, cancel = preparation()
    can.configure.side_effect = lambda **kwargs: cancel.set()
    assert OverviewTab._apply_recipe_hardware(page, {'can_config': {'mode': 'CAN-FD'}})[0] is False
    can.connect.assert_not_called()
