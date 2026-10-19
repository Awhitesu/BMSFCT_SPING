import time
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication, QMessageBox
from ui.tabs.overview_tab import OverviewTab
from core.engine import TestEngine as Engine

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


def test_unchanged_pcan_is_reused_but_changed_timing_reconnects():
    driver=Mock();driver.channel='PCAN_USBBUS1';driver.nominal_bitrate=500000;driver.data_bitrate=2000000;driver.fd_enabled=True
    driver.bus=object();driver.is_connected=True;driver.disconnect.return_value=True;driver.connect.return_value=True
    driver.configure.side_effect=lambda **values:[setattr(driver,k,v) for k,v in values.items()]
    page=SimpleNamespace(_prepare_cancel=threading.Event(),engine=SimpleNamespace(device_manager=SimpleNamespace(pcan=driver)))
    recipe={'can_config':dict(channel='PCAN_USBBUS1',mode='CAN-FD',nominal_bitrate=500000,data_bitrate=2000000)}
    assert OverviewTab._apply_recipe_hardware(page,recipe)[0]
    assert OverviewTab._apply_recipe_hardware(page,recipe)[0]
    assert driver.connect.call_count==1
    recipe['can_config']['nominal_bitrate']=250000
    assert OverviewTab._apply_recipe_hardware(page,recipe)[0]
    assert driver.connect.call_count==2 and driver.nominal_bitrate==250000
    driver.is_connected=False
    assert OverviewTab._apply_recipe_hardware(page,recipe)[0]
    assert driver.connect.call_count==3
