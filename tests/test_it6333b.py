import unittest
from unittest.mock import Mock
from devices.itech_it6333b import IT6333B


class SupplyTests(unittest.TestCase):
    def test_channel_selection_before_set_and_verify(self):
        d = IT6333B()
        d.write = Mock(return_value=True)
        d.query = Mock(side_effect=['2', '12.000'])
        self.assertTrue(d.set_voltage(2, 12))
        self.assertEqual([c.args[0] for c in d.write.call_args_list], ['INST:NSEL 2', 'VOLT 12.000'])
        self.assertEqual([c.args[0] for c in d.query.call_args_list], ['INST:NSEL?', 'VOLT?'])

    def test_channel_three_limits(self):
        d = IT6333B()
        d.write = Mock()
        self.assertFalse(d.set_voltage(3, 6))
        self.assertFalse(d.set_current(1, 3.1))
        d.write.assert_not_called()

    def test_wrong_selected_channel_blocks_output(self):
        d = IT6333B()
        d.write = Mock(return_value=True)
        d.query = Mock(return_value='1')
        self.assertFalse(d.output_control(3, True))
        d.write.assert_called_once_with('INST:NSEL 3')

    def test_channel_readback(self):
        d = IT6333B()
        d.write = Mock(return_value=True)
        d.query = Mock(side_effect=['3', '4.9', '0.02', '0'])
        self.assertEqual(d.read_channel(3), (4.9, 0.02, False))
