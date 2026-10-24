import ctypes
import importlib.util
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock
import can
from devices.zlg_net_bus import ZLGNetBus

ROOT = Path(__file__).resolve().parents[1] / 'vendor' / 'zlgcan_python_260529'


class ZLGTests(unittest.TestCase):
    def test_missing_sdk_reports_selected_path_before_loading_driver(self):
        missing = ROOT / 'missing-sdk'
        with self.assertRaises(FileNotFoundError) as error:
            ZLGNetBus(missing, '192.0.2.10', 8000)
        self.assertIn(str(missing / 'zlgcan.dll'), str(error.exception))
        self.assertIn('重新选择并保存', str(error.exception))

    def test_four_channel_receive_is_fair(self):
        spec = importlib.util.spec_from_file_location('test_zcan_four', ROOT / 'zlgcan.py')
        types = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(types)
        bus = ZLGNetBus.__new__(ZLGNetBus)
        bus.types, bus.can = types, can
        bus.lock = threading.RLock()
        bus.handle = 100
        bus.handles = {i: 100 + i for i in range(4)}
        bus._rx_next = 0
        bus.dll = MagicMock()
        bus.dll.ZCAN_GetReceiveNum.side_effect = lambda h, fd: int(not fd)
        def receive(h, ptr, count, wait):
            ptr._obj.frame.can_id = 0x123
            ptr._obj.frame.can_dlc = 1
            ptr._obj.frame.data[0] = h - 100
            return 1
        bus.dll.ZCAN_Receive.side_effect = receive
        messages = [bus.recv() for _ in range(8)]
        self.assertEqual([m.channel for m in messages], [0, 1, 2, 3] * 2)
        self.assertEqual([bytes(m.data) for m in messages[:4]], [bytes([i]) for i in range(4)])

    def test_dynamic_config_targets_physical_channel_and_applies(self):
        spec = importlib.util.spec_from_file_location('test_zcan_config', ROOT / 'zlgcan.py')
        types = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(types)
        bus = ZLGNetBus.__new__(ZLGNetBus)
        bus.types = types
        bus.lock = threading.RLock()
        bus.device, bus.handle, bus.physical_channel = 12, 13, 2
        bus.dll = MagicMock()
        captured = []
        def set_value(device, path, pointer):
            if path == b'0/add_dynamic_data':
                captured.append((pointer._obj.key, pointer._obj.value))
            else:
                self.assertIsInstance(pointer._obj, ctypes.c_uint)
                captured.append((path, pointer._obj.value))
            return 1
        bus.dll.ZCAN_SetValue.side_effect = set_value
        bus.configure_channel(500000, 2000000, True)
        self.assertEqual(captured, [(b'DYNAMIC_CONFIG_CAN2_NOMINALBAUD', b'500000'),
            (b'DYNAMIC_CONFIG_CAN2_DATABAUD', b'2000000'),
            (b'DYNAMIC_CONFIG_CAN2_USERES', b'1'), (b'0/apply_dynamic_data', 0)])
        bus.configure_channel(500000, 2000000, True, persist=True)
        self.assertEqual(captured[-1], (b'0/apply_dynamic_data', 1))
        captured.clear()
        settings = [dict(enabled=i != 1, nominal=500000, data=2000000, termination=i == 3) for i in range(4)]
        bus.configure_channels(settings)
        self.assertEqual(len(captured), 20)
        self.assertEqual(captured[5], (b'DYNAMIC_CONFIG_CAN1_ENABLE', b'0'))
        self.assertEqual(captured[18], (b'DYNAMIC_CONFIG_CAN3_USERES', b'1'))
        self.assertEqual(bus.enabled_channels, {0, 2, 3})
        bus.dll.ZCAN_SetValue.side_effect = [1, 1, 1, 0]
        with self.assertRaisesRegex(RuntimeError, '应用失败'):
            bus.configure_channel(500000, 2000000, False)

    def test_wrong_architecture_is_clear(self):
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            with self.assertRaisesRegex(RuntimeError, '64位'):
                ZLGNetBus(ROOT, '192.168.0.177', 8000)

    def test_can_and_fd_frame_conversion(self):
        spec = importlib.util.spec_from_file_location('test_vendor_zcan', ROOT / 'zlgcan.py')
        types = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(types)
        bus = ZLGNetBus.__new__(ZLGNetBus)
        bus.types, bus.can = types, can
        bus.lock = threading.RLock()
        bus.handle = 123
        bus.physical_channel = 0
        bus.dll = MagicMock()
        bus.dll.ZCAN_TransmitFD.return_value = 1
        bus.dll.ZCAN_Transmit.return_value = 1
        msg = can.Message(arbitration_id=0x1234, is_extended_id=True,
                          is_fd=True, bitrate_switch=True, data=bytes(range(64)))
        bus.send(msg)
        frame = bus.dll.ZCAN_TransmitFD.call_args.args[1]._obj.frame
        self.assertEqual(frame.can_id, 0x80001234)
        self.assertEqual(frame.len, 64)
        self.assertEqual(frame.flags, 1)
        self.assertEqual(bytes(frame.data), bytes(range(64)))
        bus.send(can.Message(arbitration_id=0x123, is_extended_id=False, data=b'AB'))
        self.assertEqual(bus.dll.ZCAN_Transmit.call_args.args[1]._obj.frame.can_dlc, 2)
        bus.send(can.Message(arbitration_id=0x123, is_extended_id=False,
                             is_remote_frame=True, dlc=8))
        remote = bus.dll.ZCAN_Transmit.call_args.args[1]._obj.frame
        self.assertEqual(remote.can_id, 0x40000123)
        self.assertEqual(remote.can_dlc, 8)
        with self.assertRaises(ValueError):
            bus.send(can.Message(arbitration_id=0x123, is_extended_id=False, data=bytes(9)))
        bus.dll.ZCAN_GetReceiveNum.side_effect = [0, 1]
        def receive(handle, pointer, count, wait):
            pointer._obj.frame.can_id = 0x80001234
            pointer._obj.frame.len = 2
            pointer._obj.frame.flags = 1
            pointer._obj.frame.data[0] = 65
            pointer._obj.frame.data[1] = 66
            return 1
        bus.dll.ZCAN_ReceiveFD.side_effect = receive
        got = bus.recv()
        self.assertTrue(got.is_fd and got.is_extended_id and got.bitrate_switch)
        self.assertEqual(got.arbitration_id, 0x1234)
        self.assertEqual(bytes(got.data), b'AB')


if __name__ == '__main__':
    unittest.main()
