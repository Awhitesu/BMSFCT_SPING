from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


def test_barcode_dedup_switch_and_clear():
    page = OverviewTab()
    page.plc_signal_timer.stop()
    try:
        page._active_stats_barcode = 'SN1'
        page.on_worker_test_finished(1, False)
        page.on_worker_test_finished(1, False)
        assert (page.total_count, page.fail_count) == (1, 1)
        page.on_worker_test_finished(1, True)
        assert (page.total_count, page.pass_count, page.fail_count) == (1, 1, 0)
        assert page.lbl_stat_rate_val.text() == '100.00%'
        assert page.set_stats_deduplicate(False)
        assert (page.total_count, page.pass_count, page.fail_count) == (3, 1, 2)
        assert page.set_stats_deduplicate(True)
        assert page.total_count == 1
        page.on_worker_test_finished(2, False)
        assert page.total_count == 1
        page.reset_statistics()
        page.on_worker_test_finished(1, False)
        assert (page.total_count, page.fail_count) == (1, 1)
    finally:
        page.close()


def test_setting_persistence_failure_restores_mode_and_sn_uses_worker():
    page = OverviewTab()
    page.plc_signal_timer.stop()
    try:
        db = Mock()
        db.load_sys_config.return_value = {'other_setting': 123}
        db.save_sys_config.return_value = False
        page.db_manager = db
        with patch('ui.tabs.overview_tab.QMessageBox.warning'):
            assert not page.set_stats_deduplicate(False)
        assert page.stats_deduplicate
        db.save_sys_config.return_value = True
        assert page.set_stats_deduplicate(False)
        assert db.save_sys_config.call_args.args[0] == {
            'other_setting': 123, 'stats_deduplicate_barcode': False}
        assert page.set_stats_deduplicate(True)
        page.engine = SimpleNamespace(workers={1: SimpleNamespace(
            sn_barcode='actualSN', execution_id='run1')})
        page._active_stats_barcode = 'wrongSN'
        page.on_worker_test_finished(1, False)
        page.on_worker_test_finished(1, False)
        assert page.production_statistics.events == [('actualSN', False)]
    finally:
        page.close()
