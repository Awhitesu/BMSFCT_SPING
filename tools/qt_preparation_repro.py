"""Repeat the PLC-to-preparation transition without accessing hardware."""
import faulthandler
from pathlib import Path
import sys

faulthandler.enable(all_threads=True)
root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / 'tests')]
from PySide6.QtWidgets import QApplication
app = QApplication([])
import test_bug_fixes as cases

for index in range(300):
    print(f'BEGIN CYCLE {index}', flush=True)
    cases.test_successful_plc_reset_allows_repeated_same_command()
    cases.test_preparation_is_async_and_cancel_does_not_start_run(None)
    print(f'END CYCLE {index}', flush=True)
print('PREPARATION_STRESS_PASS', flush=True)
