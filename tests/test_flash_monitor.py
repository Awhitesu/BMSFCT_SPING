"""Offline regression checks. No hardware or production Record.txt is accessed."""

import queue
import unittest
from unittest.mock import Mock, mock_open, patch

from core.app_flash_step import _record_state, run_app_flash
from core.flash_progress import FlashProgress
from devices.pcan_fd_driver import PCANFDDriver


def sf(payload):
    return bytes([len(payload)]) + payload if len(payload) <= 7 else bytes([0, len(payload)]) + payload


def download(size, data_format=0):
    return b"\x34" + bytes([data_format, 0x44]) + b"\x80\x00\x00\x00" + size.to_bytes(4, "big")


class ProgressTests(unittest.TestCase):
    def test_unmatched_frames_are_visible_in_diagnostics(self):
        tracker = FlashProgress()
        tracker.feed(dict(can_id=0x123, direction='RX', data=b'\x01\x00'))
        status = tracker.snapshot()
        self.assertEqual(status['received_frames'], 1)
        self.assertEqual(status['observed_ids'], ['0x123'])
        self.assertIsNone(status['last_can_age'])

    def test_external_flash_ellipsis_status(self):
        self.assertEqual(_record_state('Flash...'), 'FLASH')
        self.assertEqual(_record_state('Flash…'), 'FLASH')
        self.assertEqual(_record_state('PASSword'), '')

    def setUp(self):
        self.now = 0.0
        self.progress = FlashProgress(clock=lambda: self.now)

    def message(self, frame, response=False, **extra):
        self.progress.feed(dict(can_id=0x7EC if response else 0x7E4,
                                direction="RX", data=frame, **extra))

    def uds(self, payload, response=False):
        self.message(sf(payload), response)

    def begin(self, size, data_format=0):
        self.uds(download(size, data_format))
        self.uds(b"\x74\x10\x08", True)

    def block(self, sequence, data=b"ABCD"):
        self.uds(b"\x36" + bytes([sequence]) + data)

    def ack(self, sequence):
        self.uds(b"\x76" + bytes([sequence]), True)

    def test_confirmed_bytes_not_sent_bytes(self):
        self.begin(8)
        self.block(1)
        self.assertEqual(self.progress.snapshot()["percent"], 0)
        self.ack(1)
        self.assertEqual(self.progress.snapshot()["percent"], 50)
        self.block(2)
        self.ack(2)
        self.assertEqual(self.progress.snapshot()["percent"], 100)
        self.assertNotIn("成功", self.progress.phase)

    def test_retries_duplicate_acks_and_counter_wrap(self):
        self.begin(260 * 4)
        for number in range(1, 261):
            block = number & 255
            self.block(block)
            self.block(block)  # request retry before ACK
            self.ack(block)
            self.ack(block)  # duplicate ACK
            self.block(block)
            self.ack(block)  # request retry after ACK
        self.assertEqual(self.progress.confirmed, 1040)
        self.assertEqual(self.progress.snapshot()["percent"], 100)

    def test_missing_download_or_missing_ack_is_unknown(self):
        self.block(1)
        self.ack(1)
        self.assertIsNone(self.progress.snapshot()["percent"])
        self.begin(8)
        self.block(1)
        self.block(2)  # previous ACK is missing
        self.ack(2)
        self.assertIsNone(self.progress.snapshot()["percent"])

    def test_isotp_classic_and_fd_payload_lengths_exclude_padding(self):
        for frame_length in (8, 64):
            self.setUp()
            payload = b"\x36\x01" + b"x" * 5000  # extended FF length encoding
            self.begin(5000)
            self.message(b"\x10\x00" + len(payload).to_bytes(4, "big") + payload[:frame_length - 6])
            offset, sequence = frame_length - 6, 1
            while offset < len(payload):
                frame = bytes([0x20 | sequence]) + payload[offset:offset + frame_length - 1]
                self.message(frame.ljust(frame_length, b"\xCC"))
                offset += frame_length - 1
                sequence = (sequence + 1) & 15
            self.ack(1)
            self.assertEqual(self.progress.confirmed, 5000)
            self.assertEqual(self.progress.snapshot()["percent"], 100)

    def test_lost_cf_and_timeout_invalidate_progress(self):
        self.begin(20)
        self.message(b"\x10\x16\x36\x01ABCD")
        self.message(b"\x22ABCDEFG")  # should have been CF sequence 1
        self.ack(1)
        self.assertIsNone(self.progress.snapshot()["percent"])
        self.begin(20)
        self.message(b"\x10\x16\x36\x01ABCD")
        self.now = 3.0
        self.assertIsNone(self.progress.snapshot()["percent"])

    def test_segments_compression_and_pending_response(self):
        self.begin(4)
        self.block(1)
        self.ack(1)
        self.uds(b"\x31\x01\x02\x02")
        self.uds(b"\x7F\x31\x78", True)
        self.assertEqual(self.progress.phase, "校验中")
        self.assertFalse(self.progress.warning)
        self.begin(8)
        self.assertEqual(self.progress.segment, 2)
        self.assertEqual(self.progress.snapshot()["percent"], 0)
        self.begin(16, data_format=0x10)
        self.assertIsNone(self.progress.snapshot()["percent"])

    def test_filters_errors_and_negative_response(self):
        self.begin(4)
        self.progress.feed({"can_id": 0x123, "direction": "RX", "data": sf(b"\x36\x01AB")})
        self.progress.feed({"can_id": 0x7E4, "direction": "TX", "data": sf(b"\x36\x01AB")})
        self.assertIsNone(self.progress.pending)
        self.uds(b"\x7F\x36\x72", True)
        self.assertIn("0x72", self.progress.warning)
        self.message(b"", is_error_frame=True)
        self.assertIsNone(self.progress.snapshot()["percent"])


class FanoutTests(unittest.TestCase):
    def test_queue_full_does_not_block_listeners_and_unsubscribe(self):
        driver = PCANFDDriver()
        driver.msg_queue = queue.Queue(maxsize=1)
        received = []
        good = lambda message: received.append(message)
        bad = Mock(side_effect=RuntimeError("observer failure"))
        driver.add_message_listener(bad)
        driver.add_message_listener(good)
        info = dict(can_id=0x7E4, direction="RX", data=b"\x02\x10\x02")
        driver._publish(info)
        driver._publish(info)
        self.assertEqual(len(received), 2)
        self.assertEqual(len(driver._rx_history), 2)
        self.assertEqual(driver.msg_queue.qsize(), 1)
        self.assertIsNot(received[0], info)
        driver.remove_message_listener(good)
        driver.remove_message_listener(bad)
        driver._publish(info)
        self.assertEqual(len(received), 2)


class RecordTests(unittest.TestCase):
    def run_record(self, contents, timeout=1.0, cancel=None, fill_progress=False, write_error=False):
        driver = PCANFDDriver()
        driver.is_connected = True
        driver.send_can_message = Mock(side_effect=AssertionError("monitor must never transmit"))
        statuses = []
        now = [10.0]
        reader = mock_open()
        reader.return_value.read.side_effect = contents
        original_open = reader.side_effect

        def open_record(*args, **kwargs):
            # In particular, this must already hold when opening Record to write Start.
            self.assertEqual(len(driver._message_listeners), 1)
            if write_error and len(args) > 1 and args[1] == "w":
                raise PermissionError("write denied")
            if fill_progress and len(args) > 1 and args[1] == "r":
                for can_id, data in [
                    (0x7E4, sf(download(4))), (0x7EC, sf(b"\x74\x10\x08")),
                    (0x7E4, sf(b"\x36\x01ABCD")), (0x7EC, sf(b"\x76\x01")),
                ]:
                    driver._publish(dict(can_id=can_id, direction="RX", data=data))
            return original_open(*args, **kwargs)

        reader.side_effect = open_record
        with patch("builtins.open", reader), patch("core.app_flash_step.os.makedirs"), \
             patch("core.app_flash_step.os.fsync"), patch("core.app_flash_step.time.monotonic", side_effect=lambda: now[0]), \
             patch("core.app_flash_step.time.sleep", side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)):
            result = run_app_flash("offline/Record.txt", timeout, poll_interval=0.1,
                                   progress_callback=statuses.append, pcan=driver, cancelled=cancel)
        self.assertFalse(driver._message_listeners)
        driver.send_can_message.assert_not_called()
        return result, statuses, reader

    def test_raw_record_and_final_pass_override_can(self):
        result, statuses, reader = self.run_record(["Start", "FLASH", "Pass - 2026.09.23 15.01.38"])
        self.assertEqual(result, (True, "PASS"))
        self.assertEqual(statuses[-1]["record"], "Pass - 2026.09.23 15.01.38")
        self.assertFalse(statuses[-1]["active"])
        self.assertTrue(any(s["record"] == "FLASH" for s in statuses))
        reader.return_value.write.assert_called_once_with("Start\n")

    def test_transfer_100_does_not_automatically_pass(self):
        result, statuses, _ = self.run_record(["FLASH"] * 20, timeout=0.3, fill_progress=True)
        self.assertFalse(result[0])
        self.assertTrue(result[1].startswith("TIMEOUT"))
        self.assertTrue(any(s["percent"] == 100 and s["active"] for s in statuses))
        self.assertEqual(statuses[-1]["phase"], "等待Record结果超时")

    def test_record_fail_still_fails_with_full_can_progress(self):
        result, statuses, _ = self.run_record(["FAIL - checksum"], fill_progress=True)
        self.assertEqual(result, (False, "FAIL"))
        self.assertEqual(statuses[-1]["record"], "FAIL - checksum")

    def test_transient_read_errors_and_empty_updates(self):
        result, statuses, _ = self.run_record([PermissionError("busy"), "", "FLASH", "PASS"])
        self.assertTrue(result[0])
        self.assertTrue(any(s["record_error"] for s in statuses))
        self.assertFalse(statuses[-1]["record_error"])

    def test_cancel_and_write_failure_cleanup(self):
        result, statuses, reader = self.run_record([], cancel=lambda: True)
        self.assertEqual(result, (False, "STOPPED"))
        reader.assert_not_called()
        result, statuses, _ = self.run_record([], write_error=True)
        self.assertFalse(result[0])
        self.assertIn("写入", result[1])
        self.assertFalse(statuses[-1]["active"])

    def test_state_is_latest_complete_line_not_substring(self):
        self.assertEqual(_record_state("Start\nFLASH\nPass - today"), "PASS")
        self.assertEqual(_record_state("FAIL old\nStart\nFLASH"), "FLASH")
        self.assertEqual(_record_state("NOT PASS\npath/FAIL.txt\nPASSING"), "")

    def test_no_pcan_still_reports_record(self):
        statuses = []
        reader = mock_open(read_data="Pass - done")
        with patch("builtins.open", reader), patch("core.app_flash_step.os.makedirs"), patch("core.app_flash_step.os.fsync"):
            result = run_app_flash("offline/Record.txt", 1, progress_callback=statuses.append, pcan=None)
        self.assertEqual(result, (True, "PASS"))
        self.assertIsNone(statuses[-1]["percent"])
        self.assertIn("仅显示Record", statuses[-1]["can_warning"])

    def test_cancel_while_polling_ignores_late_pass(self):
        cancelled = Mock(side_effect=[False, False, True])
        result, statuses, _ = self.run_record(["PASS"], cancel=cancelled)
        self.assertEqual(result, (False, "STOPPED"))
        self.assertEqual(statuses[-1]["result"], "STOPPED")


if __name__ == "__main__":
    unittest.main()
