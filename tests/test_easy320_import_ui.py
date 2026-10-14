import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from openpyxl import Workbook
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ui.tabs.easy320_tab_standalone import Easy320StandaloneTab

app=QApplication.instance() or QApplication([])


def wait_idle(page):
    deadline=time.monotonic()+3
    while page.task.isRunning() and time.monotonic()<deadline:QTest.qWait(10)
    if page.task.isRunning():raise AssertionError('Operation did not finish')


class ImportUiTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'easy320_channels':[{'do':'DO0','name':'气缸'},{'do':'DO10','name':''}]}
        self.db=Mock();self.db.load_sys_config.side_effect=lambda:self.cfg
        self.db.save_sys_config.side_effect=lambda cfg:self.cfg.update(cfg) or True
        self.driver=Mock();self.driver.ip='unused';self.driver.port=502;self.driver.is_connected=True
        self.driver.write_relay.return_value=True
        self.page=Easy320StandaloneTab(self.driver,SimpleNamespace(easy320=self.driver,db_manager=self.db))

    def tearDown(self):self.page.shutdown();self.page.close()

    def test_loaded_mapping_and_click_use_octal_address(self):
        self.assertEqual(len(self.page.relay_buttons),2)
        self.assertIn('DO10',self.page.relay_buttons[1].text())
        self.page.on_relay_clicked(1,True);wait_idle(self.page)
        self.driver.write_relay.assert_called_once_with(8,True)
        self.assertEqual(self.page.relay_buttons[1].text(),'DO10')
        self.assertIn('#245c42',self.page.relay_buttons[1].styleSheet())

    def test_buttons_wrap_with_viewport_width_without_state_text(self):
        self.page.channels=[{'do':f'DO{i:o}','index':i,'name':''} for i in range(64)]
        self.page.rebuild_channels()
        self.page.relay_scroll.viewport().resize(1200,500)
        self.page.reflow_channels();wide=self.page.relay_columns
        self.page.relay_scroll.viewport().resize(400,500)
        self.page.reflow_channels();narrow=self.page.relay_columns
        self.assertLess(narrow,wide)
        self.assertEqual(len(self.page.relay_buttons),64)
        self.assertEqual(self.page.relay_grid.getItemPosition(self.page.relay_grid.indexOf(self.page.relay_buttons[63]))[:2],(63//narrow,63%narrow))
        self.page._update_btn_style(0,True)
        self.page._update_btn_style(1,False)
        self.assertEqual(self.page.relay_buttons[0].text(),'DO0')
        self.assertEqual(self.page.relay_buttons[1].text(),'DO1')
        self.assertIn('#245c42',self.page.relay_buttons[0].styleSheet())
        self.assertIn('#243247',self.page.relay_buttons[1].styleSheet())

    def test_import_replaces_count_and_persists_and_batch_only_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'channels.xlsx';book=Workbook();sheet=book.active
            sheet.append(['序号','名称'])
            for index in range(40):sheet.append([f'DO{index:o}',''])
            book.save(path);book.close()
            self.assertTrue(self.page.import_channel_file(path));wait_idle(self.page)
        self.assertEqual(len(self.page.relay_buttons),40)
        self.assertEqual(len(self.cfg['easy320_channels']),40)
        self.driver.control_selected_relays.return_value=True
        self.page.batch_control(False);wait_idle(self.page)
        self.driver.control_selected_relays.assert_called_once_with(list(range(40)),False)

    def test_invalid_import_preserves_existing_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'channels.xlsx';book=Workbook();sheet=book.active
            sheet.append(['序号','名称']);sheet.append(['DO8','invalid']);book.save(path);book.close()
            with patch('ui.tabs.easy320_tab_standalone.QMessageBox.warning') as warning:
                self.page.import_channel_file(path);wait_idle(self.page)
                warning.assert_called_once()
        self.assertEqual(len(self.page.relay_buttons),2)
        self.db.save_sys_config.assert_not_called()


if __name__=='__main__':unittest.main()
