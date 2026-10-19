import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from PySide6.QtCore import QCoreApplication
from core.engine import ChannelWorker,SubStep,SubStepType
from core.device_steps import encode,DEVICES

app=QCoreApplication.instance() or QCoreApplication([])


class DeviceStepDispatchTests(unittest.TestCase):
    def setUp(self):
        driver=Mock();driver._lock=threading.RLock();driver.is_connected=True
        driver.last_error='';driver.measure.return_value=(2.8,'V')
        self.driver=driver
        self.manager=SimpleNamespace(dmm34461=driver,
            is_enabled=lambda _:True,device_step_lock=lambda _:threading.RLock())
        self.worker=ChannelWorker(1,[],device_manager=self.manager)
        self.worker.is_running = True

    def tearDown(self):
        self.worker.is_running = False

    def step(self):
        return SubStep(SubStepType.READ_INSTRUMENT,{'device':DEVICES['dmm34461'][1],
            'action':'单次测量','params':encode('dmm34461','measure',{'variable':'DUT_V'}),
            'is_judgment':True})

    def test_value_unit_variable_and_judgment_are_forwarded(self):
        ok,result=self.worker._execute_sub_step_logic(self.step())
        self.assertTrue(ok);self.assertEqual(result,2.8);self.assertEqual(result.unit,'V')
        self.assertEqual(self.worker.variables['DUT_V'],2.8)
        self.assertEqual(self.worker.current_step_results[-1],(result,True))

    def test_driver_exception_fails_with_reason(self):
        self.driver.measure.side_effect=TimeoutError('DMM timeout')
        ok,result=self.worker._execute_sub_step_logic(self.step())
        self.assertFalse(ok);self.assertIn('DMM timeout',result)
        self.assertNotIn('DUT_V',self.worker.variables)

    def test_stopped_worker_does_not_execute_device_step(self):
        self.worker.is_running = False
        ok, result = self.worker._execute_sub_step_logic(self.step())
        self.assertFalse(ok)
        self.assertIn('测试已停止', result)
        self.driver.measure.assert_not_called()
        self.assertNotIn('DUT_V', self.worker.variables)

    def test_can_uses_shared_pcan_without_obsolete_board_manager(self):
        can=Mock();can.is_connected=True;can.send_can_message.return_value=True
        self.manager.pcan=can
        step=SubStep(SubStepType.CAN_SEND,{'device':'CAN 交互','action':'发送指令',
            'params':'ID:0x123 / TYPE:0 / CH:2 / DLC:1 / DATA:01'})
        ok,_=self.worker._execute_sub_step_logic(step)
        self.assertTrue(ok)
        can.send_can_message.assert_called_once_with(2,0x123,0,1,b'\x01')


if __name__=='__main__':unittest.main()
