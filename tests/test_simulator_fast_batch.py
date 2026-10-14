import unittest
import threading
from unittest.mock import Mock,patch
from devices.ngi_83624 import NGI83624
from devices.simulator_batch import run_simulator_batch


class FastBatchTests(unittest.TestCase):
    def test_close_and_verify_sent_in_one_payload(self):
        sim=NGI83624('unused');sim._safe_send=Mock(return_value=True)
        sim.sock=Mock();sim.sock.recv.return_value=b'0\n0\n'
        self.assertTrue(sim.output_controls(False,[1,6]))
        sim._safe_send.assert_called_once_with(b'OUTP:ONOFF 0(@1,6)\nOUTP1:ONOFF?\nOUTP6:ONOFF?\n',logger=None)

    def test_unclosed_channel_still_fails(self):
        sim=NGI83624('unused');sim._safe_send=Mock(return_value=True)
        sim.sock=Mock();sim.sock.recv.return_value=b'1\n';sim.output_control=Mock(return_value=False)
        with patch('devices.ngi_83624.time.monotonic',side_effect=[0,2]):
            self.assertFalse(sim.output_controls(False,[6]))
        sim.output_control.assert_not_called()
        self.assertIn('CH6',sim.last_error)
    def test_batch_write_and_pipelined_readback(self):
        sim=NGI83624('unused',max_channels=24);sim._safe_send=Mock(return_value=True)
        sim.sock=Mock();sim.sock.recv.return_value=b'2.5\n'*24
        sim.set_voltage=Mock()
        self.assertTrue(sim.set_voltages(2.5))
        self.assertEqual(sim._safe_send.call_count,2)
        sim._safe_send.assert_any_call(b'SOUR:VOLT 2.5(@'+','.join(map(str,range(1,25))).encode()+b')\n',logger=None)
        sim.set_voltage.assert_not_called()
        self.assertEqual(sim.sock.recv.call_count,1)

    def test_only_failed_channel_retried(self):
        sim=NGI83624('unused');sim._safe_send=Mock(return_value=True)
        sim._query_channels=Mock(return_value=['2.5','0','2.5']);sim.set_voltage=Mock(return_value=True)
        self.assertTrue(sim.set_voltages(2.5,[4,6,8]))
        sim.set_voltage.assert_called_once_with(6,2.5,None)

    def test_incomplete_response_fails_and_disconnects(self):
        sim=NGI83624('unused');sim._safe_send=Mock(return_value=True)
        sim.sock=Mock();sim.sock.recv.side_effect=[b'2.5\n',b''];sim.disconnect=Mock()
        self.assertFalse(sim.set_voltages(2.5,[1,2]))
        sim.disconnect.assert_called_once()

    def test_devices_are_parallel_and_local_channels_preserved(self):
        one=Mock();two=Mock();barrier=threading.Barrier(2)
        def action(sim,channels):
            barrier.wait(timeout=2)
            return True
        result=run_simulator_batch([(1,one,1),(2,one,2),(78,two,6)],action)
        self.assertTrue(result['ok']);self.assertEqual([r['channels'] for r in result['results']],[[1,2],[78]])

    def test_no_devices_is_not_success(self):
        self.assertFalse(run_simulator_batch([],Mock())['ok'])

    def test_snapshot_batches_measurement_setting_and_output(self):
        sim=NGI83624('unused');sim._query_channels=Mock(side_effect=[['2.5','0'],['2.5','2.5'],['1','0']])
        self.assertEqual(sim.read_voltage_snapshot([1,6]),[(2.5,2.5,True),(0,2.5,False)])
        self.assertEqual(sim._query_channels.call_count,3)
