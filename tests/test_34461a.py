import socket
import threading
import unittest
from unittest.mock import Mock
from devices.keysight_34461a import Keysight34461A


class MeterTests(unittest.TestCase):
    def test_fragmented_socket_response_and_measurement(self):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        commands = []
        def serve():
            conn, _ = server.accept()
            with conn, conn.makefile('rb') as stream:
                for _ in range(2):
                    command = stream.readline().decode().strip()
                    commands.append(command)
                    if command == '*IDN?':
                        conn.sendall(b'Keysight,34461')
                        conn.sendall(b'A,SERIAL,1.0\n')
                    else:
                        conn.sendall(b'+3.700000E+00\n')
            server.close()
        worker = threading.Thread(target=serve)
        worker.start()
        meter = Keysight34461A('127.0.0.1', server.getsockname()[1])
        self.assertTrue(meter.connect())
        self.assertEqual(meter.measure('直流电压'), (3.7, 'V'))
        meter.disconnect()
        worker.join(2)
        self.assertEqual(commands, ['*IDN?', 'MEAS:VOLT:DC? AUTO'])

    def test_overload_and_range_validation(self):
        meter = Keysight34461A()
        meter.query = Mock(return_value='9.9E37')
        with self.assertRaises(ValueError):
            meter.measure('直流电压')
        with self.assertRaises(ValueError):
            meter.measure('直流电流', '-1')
        meter.query.return_value = '0.001'
        self.assertEqual(meter.measure('直流电流', '0.01'), (0.001, 'A'))
        meter.query.assert_called_with('MEAS:CURR:DC? 0.01')

    def test_disconnected_command(self):
        with self.assertRaises(ConnectionError):
            Keysight34461A().query('*IDN?')
