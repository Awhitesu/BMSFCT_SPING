import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from ui.tabs.board_debug_tab import BoardDebugTab


class BoardRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        db = Mock()
        db.load_sys_config.return_value = {'rn_boards': {
            'hsd1': {'enabled': True, 'ip': '192.168.1.201'}}}
        self.page = BoardDebugTab(Mock(db_manager=db), 'hsd')
        self.page.current().is_connected = True

    def tearDown(self):
        self.page.shutdown()
        self.page.close()

    def test_hidden_or_configuration_page_does_not_poll(self):
        with patch.object(self.page, 'run') as run:
            self.page.poll_relays()
            run.assert_not_called()
            self.page.show()
            self.app.processEvents()
            self.assertTrue(self.page.poll_timer.isActive())
            self.page.poll_relays()
            self.assertTrue(run.call_args.kwargs['poll'])
            run.reset_mock()
            self.page.tabs.setCurrentIndex(0)
            self.page.poll_relays()
            run.assert_not_called()
            self.page.hide()
            self.assertFalse(self.page.poll_timer.isActive())

    def test_unchanged_readback_does_not_restyle_buttons(self):
        states = [False] * 15
        self.page.show_relay_states(states)
        button = self.page.relay_buttons[0]
        with patch.object(button, 'setStyleSheet', wraps=button.setStyleSheet) as style:
            self.page.polling = True
            self.page.result('poll', True, states)
            style.assert_not_called()
            states[1] = True
            self.page.result('poll', True, states)
            style.assert_not_called()
            self.assertTrue(self.page.relay_buttons[1].isChecked())
            # Failed writes must restore an optimistic button toggle.
            button.setChecked(True)
            self.page.result('poll', True, states)
            self.assertFalse(button.isChecked())
            style.assert_called_once()
        self.assertEqual(self.page.results.rowCount(), 0)


if __name__ == '__main__':
    unittest.main()
