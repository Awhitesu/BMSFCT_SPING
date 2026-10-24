import os
import threading
import time
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer, QThread
from PySide6.QtWidgets import QApplication
from ui.device_task import DeviceTask
from devices.manager import DeviceManager
from ui.tabs.hv_tab import HVSourceTab
from ui.tabs.simulator_tab import SimulatorTab
from ui.tabs.mainboard_power_tab import MainboardPowerTab
from ui.tabs.pcan_fd_tab import PCANFDTab
from ui.tabs.multimeter_tab import MultimeterTab
from ui.tabs.it6333b_tab import IT6333BTab
from ui.tabs.dg2052_tab import DG2052Tab
from ui.tabs.tbs2102b_tab import TBS2102BTab
from ui.tabs.easy320_tab_standalone import Easy320StandaloneTab
from ui.tabs.hardware_config_tab import HardwareConfigTab
from ui.tabs.ca550_tab_standalone import CA550SerialWorker


class ResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.005)

    def test_worker_reuses_thread_and_recovers_from_errors(self):
        task = DeviceTask()
        results = []
        ids = []
        def action():
            ids.append(threading.get_ident())
            return 1
        def completed(ok, value):
            self.assertEqual(QThread.currentThread(), self.app.thread())
            results.append((ok, value))
        try:
            task.run(action, completed)
            self.assertFalse(task.run(action, completed))
            self.pump(.1)
            task.run(action, completed)
            self.pump(.1)
            self.assertEqual(ids[0], ids[1])
            def fail(): raise TimeoutError('test timeout')
            task.run(fail, completed)
            self.pump(.1)
            self.assertEqual(results[-1], (False, 'test timeout'))
            self.assertFalse(task.isRunning())
        finally:
            task.shutdown()

    def test_slow_device_actions_do_not_block_ui_or_stack_tasks(self):
        db = Mock()
        db.load_sys_config.return_value = {'sim1_enabled': True, 'sim1_channels': 1}
        manager = DeviceManager(db)
        cases = [
            (HVSourceTab, 'hv_source', 'set_voltage', lambda p: p.set_hv_volt()),
            (SimulatorTab, 'simulators', 'set_voltages', lambda p: p.set_sim_volt()),
            (MainboardPowerTab, 'dut_power', 'output_control', lambda p: p.control_output(False)),
            (PCANFDTab, 'pcan', 'connect', lambda p: p.connect_device()),
        ]
        for cls, attribute, method, trigger in cases:
            with self.subTest(page=cls.__name__):
                gate = threading.Event()
                entered = threading.Event()
                device = getattr(manager, attribute)
                if isinstance(device, list): device = device[0]
                device.is_connected = True
                if cls is SimulatorTab:
                    device.read_output_state = Mock(return_value=False)
                worker_ids = []
                def blocked(*args, **kwargs):
                    worker_ids.append(threading.get_ident())
                    entered.set()
                    gate.wait(2)
                    return True
                setattr(device, method, Mock(side_effect=blocked))
                page = cls(manager)
                if cls is SimulatorTab:
                    page._select_all_channels(True)
                ticks = []
                timer = QTimer()
                timer.timeout.connect(lambda: ticks.append(1))
                timer.start(10)
                try:
                    started = time.monotonic()
                    trigger(page)
                    self.assertLess(time.monotonic() - started, .15)
                    self.pump(.15)
                    self.assertTrue(entered.is_set())
                    trigger(page)
                    self.pump(.1)
                    self.assertGreater(len(ticks), 8)
                    self.assertEqual(len(worker_ids), 1)
                    self.assertNotEqual(worker_ids[0], threading.get_ident())
                    gate.set()
                    self.pump(.15)
                    self.assertFalse(page.operation.isRunning())
                finally:
                    gate.set()
                    timer.stop()
                    page.shutdown()
                    page.close()
        manager.disconnect_all()

    def test_other_instrument_pages_keep_ui_responsive(self):
        db=Mock()
        db.load_sys_config.return_value={}
        manager=DeviceManager(db)
        factories=[
            (lambda:MultimeterTab(manager),lambda p,f:p.run('test',f)),
            (lambda:IT6333BTab(manager),lambda p,f:p.run('test',lambda d:f())),
            (lambda:DG2052Tab(manager),lambda p,f:p.run('test',lambda d:f())),
            (lambda:TBS2102BTab(manager),lambda p,f:p.run(lambda d:f())),
            (lambda:Easy320StandaloneTab(manager.easy320,manager),lambda p,f:p.run_task(f,lambda *_:None)),
        ]
        for factory,trigger in factories:
            page=factory()
            gate=threading.Event()
            calls=[]
            def blocked():
                calls.append(threading.get_ident());gate.wait(2);return True
            ticks=[]
            timer=QTimer();timer.timeout.connect(lambda:ticks.append(1));timer.start(10)
            try:
                trigger(page,blocked)
                self.pump(.1)
                self.assertGreater(len(ticks),4)
                self.assertNotEqual(calls[0],threading.get_ident())
            finally:
                gate.set();self.pump(.1);timer.stop();page.shutdown();page.close()
        manager.disconnect_all()

    def test_interface_scan_keeps_ui_responsive(self):
        db=Mock();db.load_sys_config.return_value={}
        page=HardwareConfigTab(db)
        gate=threading.Event()
        def scan():gate.wait(2);return [('COM99','test')]
        page.get_available_serial_ports=scan
        page.get_available_usb_channels=lambda:['PCAN_USBBUS1']
        try:
            page.refresh_interface_lists()
            self.pump(.1)
            self.assertFalse(page.btn_refresh_ports.isEnabled())
            gate.set();self.pump(.1)
            self.assertTrue(page.btn_refresh_ports.isEnabled())
            self.assertEqual(page._serial_ports,[('COM99','test')])
        finally:
            gate.set();page.shutdown();page.close()

    def test_ca550_queue_is_bounded(self):
        worker=CA550SerialWorker(Mock())
        for _ in range(40):worker.add_task('get_idn')
        self.assertEqual(worker.task_queue.qsize(),32)

    def test_ca550_error_does_not_stop_worker(self):
        driver=Mock()
        driver.get_idn.side_effect=[TimeoutError('timeout'),'CA550']
        worker=CA550SerialWorker(driver)
        values=[]
        worker.response_received.connect(lambda action,value,args:values.append(value))
        worker.start()
        try:
            worker.add_task('get_idn');worker.add_task('get_idn')
            self.pump(.35)
            self.assertEqual(len(values),2)
            self.assertIn('timeout',values[0])
            self.assertEqual(values[1],'CA550')
            self.assertFalse(worker.busy)
            self.assertEqual(worker.task_queue.unfinished_tasks,0)
        finally:
            worker.stop()


if __name__ == '__main__':
    unittest.main()
