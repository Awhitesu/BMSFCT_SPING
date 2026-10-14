import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from ui.tabs.hv_tab import HVSourceTab


class HVPollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def pump(self,duration=.1):
        end=time.monotonic()+duration
        while time.monotonic()<end:
            self.app.processEvents();time.sleep(.005)

    def setUp(self):
        self.driver=Mock(ip='192.168.1.190',port=7000,is_connected=True)
        self.driver.measure_voltage.return_value=12.0
        self.driver.measure_current.return_value=.1
        self.page=HVSourceTab(Mock(hv_source=self.driver))
        self.page.show();self.page.timer.stop();self.page.connection_timer.stop()

    def tearDown(self):
        self.page.shutdown();self.page.close()

    def test_repeated_poll_does_not_change_buttons_or_unchanged_values(self):
        self.page.update_readings();self.pump()
        with patch.object(self.page.btn_off,'setEnabled',wraps=self.page.btn_off.setEnabled) as enabled, \
             patch.object(self.page.lbl_status,'setStyleSheet',wraps=self.page.lbl_status.setStyleSheet) as style, \
             patch.object(self.page.lbl_volt,'setText',wraps=self.page.lbl_volt.setText) as voltage:
            for _ in range(3):
                self.page.update_readings();self.pump()
                self.page.refresh_connection_state()
            enabled.assert_not_called();style.assert_not_called();voltage.assert_not_called()

    def test_control_clicked_during_poll_is_executed_once_after_read(self):
        gate=threading.Event()
        self.driver.measure_voltage.side_effect=lambda:(gate.wait(2) and 12.0)
        try:
            self.page.update_readings();self.pump(.03)
            self.assertTrue(self.page.btn_off.isEnabled())
            self.page.control_output(False)
            self.page.control_output(False)
            self.driver.output_control.assert_not_called()
            gate.set();self.pump(.2)
            self.driver.output_control.assert_called_once_with(False)
            self.assertTrue(self.page.btn_off.isEnabled())
            self.page.hide()
            self.assertFalse(self.page.timer.isActive())
        finally:gate.set()


if __name__=='__main__':unittest.main()
