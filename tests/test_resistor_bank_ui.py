import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ui.tabs.resistor_bank_tab import ResistorBankTab

app=QApplication.instance() or QApplication([])


class ResistorBankUiTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'rn_boards':{'resistor1':{'enabled':True,'ip':'192.168.1.101','number':2},
                              'resistor2':{'enabled':True,'ip':'192.168.1.102','number':1}}}
        self.db=Mock();self.db.load_sys_config.side_effect=lambda:self.cfg
        self.db.save_sys_config.side_effect=lambda cfg:self.cfg.update(cfg) or True
        self.page=ResistorBankTab(SimpleNamespace(db_manager=self.db))

    def tearDown(self):self.page.shutdown();self.page.close()

    def wait_idle(self):
        deadline=time.monotonic()+3
        while self.page.task.isRunning() and time.monotonic()<deadline:QTest.qWait(10)
        self.assertFalse(self.page.task.isRunning())

    def test_shows_36_channels_and_selects_only_active(self):
        self.assertEqual(len(self.page.bank_widgets),36)
        self.assertEqual(self.page.routes[1][0].setting['key'],'resistor2')
        self.page.select_channels(True)
        self.assertEqual(self.page.selected_channels(),list(range(1,13)))
        self.assertFalse(self.page.bank_widgets[36][1].isEnabled())

    def test_single_and_batch_use_captured_channels_and_values(self):
        with patch('ui.tabs.resistor_bank_tab.operate_channels',return_value={
                'ok':True,'values':{7:1230},'errors':{},'elapsed':.1}) as action:
            self.page.run_bank('set',[7],1230);self.wait_idle()
            self.assertEqual(action.call_args.args[1:],([7],'set',1230))
            self.assertIn('1,230',self.page.bank_widgets[7][3].text())

    def test_save_order_after_old_page_deleted_and_restore_controls(self):
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.page.config.cellWidget(0,1).setValue(1)
        self.page.config.cellWidget(1,1).setValue(2)
        self.page.save();self.wait_idle()
        self.assertEqual(self.cfg['rn_boards']['resistor1']['number'],1)
        self.assertEqual(self.page.routes[1][0].setting['key'],'resistor1')
        self.assertTrue(self.page.save_button.isEnabled())

    def test_duplicate_sequence_does_not_save(self):
        self.page.config.cellWidget(0,1).setValue(1)
        with patch('ui.tabs.resistor_bank_tab.QMessageBox.warning') as warning:
            self.page.save();warning.assert_called_once()
        self.db.save_sys_config.assert_not_called()


if __name__=='__main__':unittest.main()
