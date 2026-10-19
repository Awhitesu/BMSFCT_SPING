import unittest
from unittest.mock import Mock
from devices.rigol_dg2052 import DG2052


class GeneratorTests(unittest.TestCase):
    def test_configure_channel_two_without_enabling_output(self):
        driver = DG2052()
        driver.command = Mock()
        driver.query = Mock(return_value='0,"No error"')
        driver.read_channel = Mock(return_value={'输出': 'OFF', '波形': 'SIN', '幅值单位': 'VPP',
            '幅值': '1', '偏置(V)': '0', '频率(Hz)': '1000', '负载(Ω)': '9.9E37'})
        driver.configure(2, '正弦波', 1000, 1, 0, 'INF')
        commands = [call.args[0] for call in driver.command.call_args_list]
        self.assertIn(':SOUR2:FREQ 1000', commands)
        self.assertIn(':SOUR2:VOLT:UNIT VPP', commands)
        self.assertFalse(any(':OUTP2 ON' in command for command in commands))
        self.assertFalse(any(':SOUR1' in command for command in commands))

    def test_instrument_error_blocks_success(self):
        driver = DG2052()
        driver.query = Mock(return_value='-222,"Data out of range"')
        driver.command = Mock()
        with self.assertRaises(ValueError):
            driver.configure(1, '方波', 1e6, 1, 0, '50')
        driver.command.assert_not_called()

    def test_invalid_combined_voltage(self):
        with self.assertRaises(ValueError):
            DG2052().configure(1, '正弦波', 1000, 10, 1, '50')

    def test_output_readback(self):
        driver = DG2052()
        driver.command = Mock()
        driver.query = Mock(side_effect=['0,"No error"', 'ON'])
        driver.read_channel = Mock(return_value={'输出': 'ON'})
        self.assertEqual(driver.output_control(1, True), {'输出': 'ON'})
        driver.command.assert_called_with(':OUTP1 ON')
