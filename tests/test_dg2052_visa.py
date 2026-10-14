import sys
import unittest
from unittest.mock import Mock,patch
from devices.rigol_dg2052 import DG2052


class VisaConnectionTests(unittest.TestCase):
    def backend(self):
        instrument=Mock();instrument.query.return_value='RIGOL,DG2052,SERIAL,VERSION\n'
        manager=Mock();manager.open_resource.return_value=instrument
        pyvisa=Mock();pyvisa.ResourceManager.return_value=manager
        return pyvisa,manager,instrument

    def test_vxi11_resource_and_query(self):
        pyvisa,manager,instrument=self.backend()
        with patch.dict(sys.modules,{'pyvisa':pyvisa}):
            driver=DG2052('192.168.1.108');driver.connect_mode='VISA'
            self.assertTrue(driver.connect())
            manager.open_resource.assert_called_once_with('TCPIP0::192.168.1.108::inst0::INSTR',open_timeout=5000)
            instrument.query.assert_called_once_with('*IDN?')
            self.assertEqual(driver.command(':OUTP1 OFF'),'已发送')
            instrument.write.assert_called_once_with(':OUTP1 OFF')
            driver.disconnect();instrument.close.assert_called_once()

    def test_native_backend_failure_falls_back_to_py(self):
        pyvisa,manager,instrument=self.backend()
        pyvisa.ResourceManager.side_effect=[OSError('no VISA library'),manager]
        with patch.dict(sys.modules,{'pyvisa':pyvisa}):
            driver=DG2052('192.168.1.108');driver.connect_mode='VISA'
            self.assertTrue(driver.connect());pyvisa.ResourceManager.assert_called_with('@py')

    def test_wrong_model_not_connected(self):
        pyvisa,manager,instrument=self.backend();instrument.query.return_value='OTHER,MODEL'
        with patch.dict(sys.modules,{'pyvisa':pyvisa}):
            driver=DG2052('192.168.1.108');driver.connect_mode='VISA'
            self.assertFalse(driver.connect());self.assertIn('OTHER',driver.last_error)

    def test_visa_timeout_invalidates_connection(self):
        pyvisa,manager,instrument=self.backend()
        with patch.dict(sys.modules,{'pyvisa':pyvisa}):
            driver=DG2052('192.168.1.108');driver.connect_mode='VISA';driver.connect()
            instrument.query.side_effect=TimeoutError('timeout')
            with self.assertRaises(TimeoutError):driver.query('*IDN?')
            self.assertFalse(driver.is_connected)
