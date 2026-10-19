import unittest
from unittest.mock import Mock,patch
from devices.ngi_83624 import NGI83624
from devices.manager import DeviceManager


class SimulatorVerificationTests(unittest.TestCase):
    def test_current_units_and_readback(self):
        sim=NGI83624('unused');sim._safe_send=Mock(return_value=True);sim._query=Mock(return_value='125')
        self.assertTrue(sim.set_current_limit(6,.125))
        sim._safe_send.assert_called_once_with(b'SOUR6:OUTCURR 125\n',logger=None)
        sim._query.assert_called_once_with('SOUR6:OUTCURR?',None)

    def test_ranges_use_documented_codes(self):
        for mode,code in [('HIGH',0),('LOW',2),('AUTO',3)]:
            sim=NGI83624('unused');sim._safe_send=Mock(return_value=True);sim._query=Mock(return_value=str(code))
            self.assertTrue(sim.set_range(6,mode))
            sim._safe_send.assert_called_once_with(f'SOUR6:RANG {code}\n'.encode(),logger=None)
        sim._safe_send.reset_mock()
        self.assertFalse(sim.set_range(6,'invalid'));sim._safe_send.assert_not_called()

    def test_parameter_mismatch_not_success(self):
        sim=NGI83624('unused');sim._safe_send=Mock(return_value=True);sim._query=Mock(return_value='0')
        self.assertFalse(sim.set_current_limit(6,.125))
        self.assertEqual(sim._query.call_count,3)

    def test_read_current_settings_uses_amperes(self):
        sim=NGI83624('unused');sim._query=Mock(side_effect=['125','3'])
        self.assertEqual(sim.read_source_settings(6),{'current':.125,'range':'AUTO'})
    def test_fragmented_response_is_complete(self):
        sim=NGI83624('unused'); sim.sock=Mock()
        sim.sock.recv.side_effect=[b'2.',b'500',b'0\r\n']
        self.assertEqual(sim._read_line(),'2.5000')

    @patch('devices.ngi_83624.time.sleep')
    def test_set_voltage_retries_failed_readback(self,sleep):
        sim=NGI83624('unused'); sim._safe_send=Mock(return_value=True)
        sim.read_set_voltage=Mock(side_effect=[0.0,2.5])
        self.assertTrue(sim.set_voltage(6,2.5))
        self.assertEqual(sim._safe_send.call_count,2)
        sim._safe_send.assert_called_with(b'SOUR6:VOLT 2.5\n',logger=None)

    @patch('devices.ngi_83624.time.sleep')
    def test_persistent_zero_is_not_success(self,sleep):
        sim=NGI83624('unused'); sim._safe_send=Mock(return_value=True)
        sim.read_set_voltage=Mock(return_value=0.0)
        self.assertFalse(sim.set_voltage(6,2.5))
        self.assertEqual(sim.read_set_voltage.call_count,3)
        self.assertIn('CH6',sim.last_error)

    def test_eof_flush_does_not_loop(self):
        sim=NGI83624('unused'); sim.sock=Mock(); sim.sock.recv.return_value=b''
        with self.assertRaises(ConnectionError): sim._clear_buffer()
        self.assertEqual(sim.sock.recv.call_count,1)

    def test_sendall_used(self):
        sim=NGI83624('unused'); sim.sock=Mock(); sim.is_connected=True
        sim._clear_buffer=Mock()
        self.assertTrue(sim._safe_send(b'SOUR6:VOLT 2.5\n'))
        sim.sock.sendall.assert_called_once(); sim.sock.send.assert_not_called()

    def test_disconnected_devices_not_skipped(self):
        db=Mock(); db.load_sys_config.return_value={'sim1_channels':2,'sim2_enabled':True,'sim2_channels':2}
        manager=DeviceManager(db)
        for sim in manager.simulators:
            sim.is_connected=False
            sim.read_output_state=Mock(return_value=False)
            sim.set_voltage=Mock(return_value=True); sim.output_control=Mock(return_value=True)
            sim.set_voltages=Mock(return_value=True);sim.output_controls=Mock(return_value=True)
        manager.simulators[1].set_voltages.return_value=False
        manager.simulators[1].output_controls.return_value=False
        self.assertFalse(manager.broadcast_voltage(2.5))
        self.assertFalse(manager.broadcast_output(True))
        self.assertEqual(manager.simulators[1].set_voltages.call_count,1)
        manager.simulators[1].output_controls.assert_any_call(True,[1,2],None)
        manager.simulators[1].output_controls.assert_any_call(False,[1,2],None)

    @patch('devices.ngi_83624.time.sleep')
    def test_invalid_output_reply_is_not_confirmed_off(self,sleep):
        sim=NGI83624('unused'); sim._ensure_connected=Mock(return_value=True)
        sim._safe_send=Mock(return_value=True); sim._query=Mock(return_value='')
        with patch('devices.ngi_83624.time.monotonic',side_effect=[0,2]):
            self.assertFalse(sim.output_control(6,False))

    @patch('devices.ngi_83624.time.sleep')
    def test_enable_waits_for_lagging_state_without_rewrite_or_reconnect(self,sleep):
        sim=NGI83624('unused'); sim.is_connected=True
        sim._safe_send=Mock(return_value=True)
        sim._query=Mock(side_effect=['0','0','1'])
        sim._verify_output_voltage=Mock(return_value=True)
        sim.connect=Mock()
        self.assertTrue(sim.output_control(6,True))
        sim._safe_send.assert_called_once_with(b'OUTP6:ONOFF 1\n',logger=None)
        self.assertEqual(sim._query.call_count,3)
        sim.connect.assert_not_called()
        self.assertTrue(sim.is_connected)
        sim._verify_output_voltage.assert_called_once_with([6],None)

    @patch('devices.ngi_83624.time.sleep')
    def test_batch_waits_only_for_unconfirmed_channels(self,sleep):
        sim=NGI83624('unused'); sim.is_connected=True
        sim._query_channels=Mock(return_value=['1','0','1'])
        sim._query=Mock(side_effect=['0','1'])
        sim._verify_output_voltage=Mock(return_value=True)
        sim.output_control=Mock()
        self.assertTrue(sim.output_controls(True,[1,2,3]))
        sim._query_channels.assert_called_once_with(
            'OUTP{ch}:ONOFF?',[1,2,3],None,prefix='OUTP:ONOFF 1(@1,2,3)\n')
        self.assertEqual(sim._query.call_count,2)
        sim._query.assert_called_with('OUTP2:ONOFF?',None)
        sim.output_control.assert_not_called()
        sim._verify_output_voltage.assert_called_once_with([1,2,3],None)

    @patch('devices.ngi_83624.time.sleep')
    def test_transition_timeout_reports_failure_without_disconnect(self,sleep):
        sim=NGI83624('unused'); sim.is_connected=True
        sim._safe_send=Mock(return_value=True); sim._query=Mock(return_value='0')
        with patch('devices.ngi_83624.time.monotonic',side_effect=[0,2]):
            self.assertFalse(sim.output_control(6,True))
        self.assertIn('CH6',sim.last_error)
        self.assertTrue(sim.is_connected)

    @patch('devices.ngi_83624.time.sleep')
    def test_confirmed_disable_has_no_fixed_delay(self,sleep):
        sim=NGI83624('unused'); sim._safe_send=Mock(return_value=True)
        sim._query=Mock(return_value='0')
        self.assertTrue(sim.output_control(6,False))
        sleep.assert_not_called()
