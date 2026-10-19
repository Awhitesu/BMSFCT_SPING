import unittest
from unittest.mock import Mock
from devices.manager import DeviceManager
from devices.simulator_config import simulator_settings


class SimulatorMappingTests(unittest.TestCase):
    def manager(self, cfg):
        db = Mock()
        db.load_sys_config.return_value = cfg
        return DeviceManager(db)

    def test_number_independent_of_slot_and_fixed_blocks(self):
        m = self.manager(dict(sim1_number=2, sim1_channels=12,
                              sim2_enabled=True, sim2_number=1, sim2_channels=24,
                              sim5_enabled=True, sim5_channels=24))
        for global_ch, slot, local in ((1, 1, 1), (24, 1, 24), (25, 0, 1),
                                       (36, 0, 12), (97, 4, 1), (120, 4, 24)):
            self.assertEqual(m._get_sim_and_ch(global_ch), (m.simulators[slot], local))
        for channel in (0, 37, 48, 49, 121, 1.5):
            self.assertEqual(m._get_sim_and_ch(channel), (None, None))

    def test_disabled_and_duplicate_numbers(self):
        self.assertEqual(self.manager({})._get_sim_and_ch(25), (None, None))
        for cfg in (dict(sim2_enabled=True, sim2_number=1), dict(sim1_channels=25), dict(sim1_number=7)):
            with self.assertRaises(ValueError):
                simulator_settings(cfg)

    def test_sixth_device_and_total_limit(self):
        m = self.manager(dict(sim6_enabled=True, sim6_channels=24))
        self.assertEqual(len(m.simulators), 6)
        self.assertEqual(m._get_sim_and_ch(121), (m.simulators[5], 1))
        self.assertEqual(m._get_sim_and_ch(144), (m.simulators[5], 24))
        self.assertEqual(m._get_sim_and_ch(145), (None, None))
        m = self.manager({f'sim{i}_{key}': value for i in range(1, 7)
                          for key, value in (('enabled', True), ('channels', 24))})
        self.assertEqual(len(m.simulator_channels()), 144)

    def test_combined_device_channels(self):
        m = self.manager(dict(sim1_number=2, sim1_channels=2,
                              sim2_enabled=True, sim2_number=1, sim2_channels=3))
        channels = m.simulator_channels()
        self.assertEqual([ch for ch, _, _ in channels], [1, 2, 3, 25, 26])
        self.assertIs(channels[0][1], m.simulators[1])
        self.assertIs(channels[-1][1], m.simulators[0])

    def test_broadcast_all_actual_channels(self):
        m = self.manager(dict(sim1_channels=3, sim2_enabled=True, sim2_channels=2))
        for sim in m.simulators:
            sim.is_connected = True
            sim.read_output_state = Mock(return_value=False)
            sim.set_voltage = Mock(return_value=True)
            sim.set_voltages = Mock(return_value=True)
        self.assertTrue(m.broadcast_voltage(3.7))
        m.simulators[0].set_voltages.assert_called_once_with(3.7,[1,2,3],None)
        m.simulators[1].set_voltages.assert_called_once_with(3.7,[1,2],None)
        m.simulators[2].set_voltages.assert_not_called()


if __name__ == '__main__':
    unittest.main()
