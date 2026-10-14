import os
import tempfile
import unittest
import zipfile
import struct
from unittest.mock import MagicMock

from core.burn_recorder import verify_project_readback
from devices.opteeq_sg400 import OpteeQSG400


class ProjectBindingTests(unittest.TestCase):
    def test_independent_step_editor_roundtrip(self):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtWidgets import QApplication
        from ui.dialogs.step_dialog import StepDialog
        app = QApplication.instance() or QApplication([])
        dialog = StepDialog()
        dialog.category_combo.setCurrentText('设备操作')
        dialog.sub_category_combo.setCurrentText('下载器')
        dialog.device_combo.setCurrentText('力捷丰 SG400 下载器 (OpteeQ SG400)')
        dialog.action_combo.setCurrentText('独立回读校验')
        dialog.opteeq_project.setCurrentText('test.opj')
        data = dialog.get_data()
        self.assertEqual(data['type'], '设置仪表')
        self.assertIn('PROJECT:test.opj', data['params'])
        reloaded = StepDialog(step_data=data)
        self.assertEqual(reloaded.action_combo.currentText(), '独立回读校验')
        self.assertEqual(reloaded.opteeq_project.currentText(), 'test.opj')
        reloaded.close()
        dialog.close()

    def test_independent_verification_does_not_program(self):
        from core.engine import ChannelWorker, SubStep, SubStepType
        from unittest.mock import patch
        manager = MagicMock()
        worker = ChannelWorker(0, [], device_manager=manager)
        step = SubStep(type=SubStepType.SET_INSTRUMENT, params={
            'device': '力捷丰 SG400', 'action': '独立回读校验',
            'params': 'PROJECT:C:\\test.opj / ARCHIVE_REPORT:0'})
        with patch('os.path.isfile', return_value=True), \
             patch('core.burn_recorder.parse_opj_metadata', return_value={}), \
             patch('core.burn_recorder.verify_project_readback', return_value=(b'AB', b'AB', 0, {'is_match': True})):
            success, result = worker._execute_sub_step_logic(step)
        self.assertTrue(success)
        self.assertEqual(result, 'PASS')
        self.assertEqual(worker.last_flash_status['kind'], 'SG400')
        self.assertEqual(worker.last_flash_status['result'], 'PASS')
        self.assertFalse(worker.last_flash_status['active'])
        manager.opteeq.run_project.assert_not_called()

    def test_recipe_readback_exception_fails(self):
        from core.engine import ChannelWorker, SubStep, SubStepType
        from unittest.mock import patch
        manager = MagicMock()
        manager.opteeq.query_state.return_value = 'Pass'
        manager.opteeq.run_project.return_value = True
        worker = ChannelWorker(0, [], device_manager=manager)
        step = SubStep(type=SubStepType.SET_INSTRUMENT, params={
            'device': '力捷丰 SG400', 'action': '烧录',
            'params': 'PROJECT:C:\\missing.opj / VERIFY_DIFF:1 / ARCHIVE_REPORT:0'})
        with patch('core.burn_recorder.parse_opj_metadata', return_value={'valid': True}), \
             patch('core.burn_recorder.verify_project_readback', side_effect=ValueError('回读截断')), \
             patch('os.path.isfile', return_value=True):
            success, result = worker._execute_sub_step_logic(step)
        self.assertFalse(success)
        self.assertEqual(worker.last_flash_status['result'], 'FAIL')
        self.assertFalse(worker.last_flash_status['active'])
        self.assertIn('回读截断', str(result))

    def test_actual_project_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'test.opj')
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('test.opd', struct.pack('<II', 0xAF402800, 2) + b'AB'
                                 + struct.pack('<II', 0xAF403800, 2) + b'CD')
            driver = MagicMock()
            driver.read_chip.side_effect = [b'AB', b'CX']
            progress = []
            source, actual, address, diff = verify_project_readback(
                driver, 'test.opj', path, progress_callback=lambda done, total, addr: progress.append((done, total)))
            self.assertEqual(progress, [(0, 4), (2, 4), (2, 4), (4, 4)])
            self.assertEqual(source, b'ABCD')
            self.assertFalse(diff['is_match'])
            self.assertEqual(diff['samples'][0]['address'], '0xAF403801')
            self.assertEqual(driver.read_chip.call_args_list[1].args[1:], (0xAF403800, 2))

    def test_failed_upload_does_not_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'test.opj')
            with open(path, 'wb') as stream:
                stream.write(b'new project')
            driver = OpteeQSG400()
            driver.is_connected = True
            driver._dll = MagicMock()
            driver._dll.OpteeQ_SendFile.return_value = False
            self.assertFalse(driver.run_project(path))
            driver._dll.OpteeQ_ExecuteScript.assert_not_called()


if __name__ == '__main__':
    unittest.main()
