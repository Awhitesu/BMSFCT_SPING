import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from devices.manager import DeviceManager
from ui.tabs.pcan_fd_tab import PCANFDTab


app = QApplication.instance() or QApplication([])


def manager_stub():
    manager = DeviceManager.__new__(DeviceManager)
    manager.rn_boards = {}
    manager.simulators = []
    manager.simulator_channels = lambda: []
    for name in ('dmm34461', 'tbs2102b', 'dg2052', 'it6333b', 'hv_source',
                 'dut_power', 'easy320', 'ca550', 'pcan', 'opteeq', 'e2_rfp'):
        setattr(manager, name, None)
    return manager


def test_missing_output_confirmation_fails_shutdown():
    manager = manager_stub()
    manager.hv_source = SimpleNamespace(is_connected=True, output_control=Mock(return_value=None))
    manager.it6333b = SimpleNamespace(is_connected=True, output_control=Mock(return_value=True))
    assert not manager.safe_shutdown_outputs()
    assert manager.last_shutdown_errors
    assert manager.it6333b.output_control.call_count == 3


def test_disconnect_failure_does_not_skip_remaining_devices_or_report_success():
    manager = manager_stub()
    manager.hv_source = SimpleNamespace(disconnect=Mock(side_effect=OSError('close failed')))
    manager.dut_power = SimpleNamespace(disconnect=Mock(return_value=None))
    manager.pcan = SimpleNamespace(disconnect=Mock(return_value=False), last_error='adapter busy')
    assert manager.disconnect_all() is False
    manager.dut_power.disconnect.assert_called_once()
    manager.pcan.disconnect.assert_called_once()
    assert len(manager.last_disconnect_errors) == 2
    assert 'adapter busy' in '; '.join(manager.last_disconnect_errors)
    manager.hv_source.disconnect.side_effect = None
    manager.pcan.disconnect.return_value = True
    assert manager.disconnect_all() is True
    assert manager.last_disconnect_errors == []


def test_zlg_configuration_is_background_and_repeated_clicks_do_not_stack():
    db = Mock()
    db.load_sys_config.return_value = {}
    manager = DeviceManager(db)
    driver = manager.pcan
    driver.channel, driver.is_connected = 'ZLG_CAN1', True
    entered, release = threading.Event(), threading.Event()
    worker_threads = []
    def configure(*args):
        worker_threads.append(threading.get_ident())
        entered.set()
        release.wait(3)
    driver.bus = SimpleNamespace(configure_channels=Mock(side_effect=configure))
    page = PCANFDTab(manager)
    page.timer.stop()
    page.channel.setCurrentText('ZLG_CAN1')
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(10)
    try:
        with patch('ui.tabs.pcan_fd_tab.QMessageBox.information') as info:
            started = time.monotonic()
            page.apply_zlg_parameters()
            assert time.monotonic() - started < .15
            assert entered.wait(2)
            page.apply_zlg_parameters()
            # Windows can coalesce timers under a loaded full-suite run. Check
            # that events continue while configure is blocked, rather than
            # assuming three 10 ms ticks arrive within a fixed 120 ms window.
            deadline = time.monotonic() + 1
            while len(ticks) < 3 and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(.002)
            assert len(ticks) >= 3
            assert worker_threads == [worker_threads[0]]
            assert worker_threads[0] != threading.get_ident()
            assert not page.zlg_apply.isEnabled()
            release.set()
            deadline = time.monotonic() + 2
            while page.operation.isRunning() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.002)
            assert page.zlg_apply.isEnabled()
            info.assert_called_once()
    finally:
        release.set()
        timer.stop()
        page.shutdown()
        page.close()
        driver.is_connected = False
        driver.bus = None
