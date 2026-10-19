import math
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch
from devices.ngi_83624 import NGI83624


@contextmanager
def simulated_readback(targets, rounds):
    sim = NGI83624('unused')
    sim._safe_send = Mock(return_value=True)
    sim._query = Mock(return_value='1')
    clock = [0.0]
    round_index = [-1]
    def query(template, channels, logger=None, **kwargs):
        if template == 'SOUR{ch}:VOLT?':
            return [str(targets[ch]) for ch in channels]
        if template == 'MEAS{ch}:VOLT?':
            round_index[0] += 1
        values, states = rounds[min(max(0, round_index[0]), len(rounds)-1)]
        return [str((values if template == 'MEAS{ch}:VOLT?' else states)[ch]) for ch in channels]
    sim._query_channels = Mock(side_effect=query)
    with patch('devices.ngi_83624.time.monotonic', side_effect=lambda:clock[0]), patch(
            'devices.ngi_83624.time.sleep', side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
        yield sim, clock


class OutputVoltageTests(unittest.TestCase):
    def test_single_on_waits_for_actual_voltage_and_stability(self):
        with simulated_readback({6:2.8}, [({6:0},{6:1}),({6:2.8},{6:1})]) as (sim, clock):
            self.assertTrue(sim.output_control(6,True))
            self.assertGreaterEqual(clock[0],1.0)
            self.assertGreaterEqual(sim.last_output_confirmation['stable_s'],.3)
            sim._safe_send.assert_called_once()

    def test_on_without_voltage_is_failure(self):
        with simulated_readback({6:2.8}, [({6:0},{6:1})]) as (sim, clock):
            self.assertFalse(sim.output_control(6,True))
            self.assertAlmostEqual(clock[0],3.0)
            self.assertIn('CH6',sim.last_error)
            self.assertIn('2.8',sim.last_error)

    def test_batch_rechecks_previously_good_channels(self):
        data = [({1:2.8,2:0},{1:1,2:1}),
                ({1:2.1,2:3.3},{1:1,2:1}),
                ({1:2.8,2:3.31},{1:1,2:1})]
        with simulated_readback({1:2.8,2:3.3}, data) as (sim, clock):
            self.assertTrue(sim.output_controls(True,[1,2]))
            calls=[c for c in sim._query_channels.call_args_list if c.args[0]=='MEAS{ch}:VOLT?']
            self.assertTrue(all(c.args[1]==[1,2] for c in calls))
            self.assertEqual(sim.last_output_confirmation['unstable_rounds'],2)

    def test_early_pass_then_late_rebound_resets_stability(self):
        data=[({1:4.2},{1:1})]*8 + [({1:3.56609},{1:1})] + [({1:4.2},{1:1})]
        with simulated_readback({1:4.2}, data) as (sim, clock):
            self.assertTrue(sim._verify_output_voltage([1]))
            self.assertGreaterEqual(clock[0],1.2)
            self.assertGreaterEqual(sim.last_output_confirmation['stable_s'],.3)
            self.assertEqual(sim.last_output_confirmation['unstable_rounds'],1)

    def test_persistent_oscillation_never_confirms(self):
        data=[({1:4.2 if i%2 else 3.56},{1:1}) for i in range(40)]
        with simulated_readback({1:4.2}, data) as (sim, clock):
            self.assertFalse(sim._verify_output_voltage([1]))
            self.assertFalse(sim.last_output_confirmation['ok'])
            self.assertIn('not stable',sim.last_error)

    def test_voltage_without_on_state_is_failure(self):
        with simulated_readback({1:2.8}, [({1:2.8},{1:0})]) as (sim, clock):
            self.assertFalse(sim._verify_output_voltage([1]))

    def test_invalid_measurement_is_failure(self):
        with simulated_readback({1:2.8}, [({1:math.nan},{1:1})]) as (sim, clock):
            self.assertFalse(sim._verify_output_voltage([1]))

    def test_disabled_output_does_not_wait_for_decay(self):
        sim=NGI83624('unused')
        sim._query_channels=Mock(return_value=['0','0'])
        sim._verify_output_voltage=Mock()
        self.assertTrue(sim.output_controls(False,[1,2]))
        sim._verify_output_voltage.assert_not_called()

    def test_stable_confirmation_logged_with_time_and_actual_readings(self):
        with simulated_readback({1:2.5}, [({1:2.4999},{1:1})]) as (sim, clock):
            logger=Mock()
            self.assertTrue(sim._verify_output_voltage([1],logger))
            logger.assert_called_once()
            self.assertIn('2.4999',logger.call_args.args[0])
            self.assertIn('duration_s',logger.call_args.args[0])


if __name__=='__main__':
    unittest.main()
