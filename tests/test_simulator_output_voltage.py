import unittest
from unittest.mock import Mock,patch
from devices.ngi_83624 import NGI83624


class OutputVoltageTests(unittest.TestCase):
    @patch('devices.ngi_83624.time.sleep')
    def test_single_on_waits_for_actual_voltage(self,sleep):
        sim=NGI83624('unused')
        sim._safe_send=Mock(return_value=True)
        sim._query=Mock(return_value='1')
        sim._query_channels=Mock(side_effect=[['2.8'],['0'],['1'],['2.8'],['1']])
        self.assertTrue(sim.output_control(6,True))
        sim._safe_send.assert_called_once()
        sleep.assert_called_once()

    def test_on_without_voltage_is_failure(self):
        sim=NGI83624('unused')
        sim._safe_send=Mock(return_value=True)
        sim._query=Mock(return_value='1')
        sim._query_channels=Mock(side_effect=[['2.8'],['0'],['1']])
        with patch('devices.ngi_83624.time.monotonic',side_effect=[0,0,4]):
            self.assertFalse(sim.output_control(6,True))
        self.assertIn('CH6',sim.last_error)
        self.assertIn('2.8',sim.last_error)

    @patch('devices.ngi_83624.time.sleep')
    def test_batch_uses_individual_targets_and_polls_pending_only(self,sleep):
        sim=NGI83624('unused')
        sim._query_channels=Mock(side_effect=[
            ['1','1'],['2.8','3.3'],['2.8','0'],['1','1'],['3.31'],['1']])
        self.assertTrue(sim.output_controls(True,[1,2]))
        sim._query_channels.assert_any_call('MEAS{ch}:VOLT?',[2],None)
        sleep.assert_called_once()

    def test_voltage_without_on_state_is_failure(self):
        sim=NGI83624('unused')
        sim._query_channels=Mock(side_effect=[['2.8'],['2.8'],['0']])
        with patch('devices.ngi_83624.time.monotonic',side_effect=[0,4]):
            self.assertFalse(sim._verify_output_voltage([1]))

    def test_invalid_measurement_is_failure(self):
        sim=NGI83624('unused')
        sim._query_channels=Mock(side_effect=[['2.8'],['nan'],['1']])
        with patch('devices.ngi_83624.time.monotonic',side_effect=[0,4]):
            self.assertFalse(sim._verify_output_voltage([1]))

    def test_disabled_output_does_not_require_voltage_to_decay(self):
        sim=NGI83624('unused')
        sim._query_channels=Mock(return_value=['0','0'])
        sim._verify_output_voltage=Mock()
        self.assertTrue(sim.output_controls(False,[1,2]))
        sim._verify_output_voltage.assert_not_called()


if __name__=='__main__':
    unittest.main()
