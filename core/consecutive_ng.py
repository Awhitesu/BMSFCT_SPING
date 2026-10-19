"""Count distinct failed products within a streak of completed tests."""


class ConsecutiveNgAlarm:
    def __init__(self, enabled=False, threshold=3):
        self.seen_runs = set()
        self.configure(enabled, threshold)

    def configure(self, enabled, threshold):
        if type(enabled) is not bool or type(threshold) is not int or not 1 <= threshold <= 10000:
            raise ValueError('连续不良报警阈值必须为1–10000的整数')
        self.enabled, self.threshold = enabled, threshold
        self.count = 0
        self.failed_barcodes = set()
        self.alarmed = False

    def record(self, passed, barcode, run_id):
        if not self.enabled or not run_id or run_id in self.seen_runs:
            return False
        self.seen_runs.add(run_id)
        if passed:
            self.count = 0
            self.failed_barcodes.clear()
            self.alarmed = False
            return False
        barcode = str(barcode or '').strip()
        if barcode and barcode in self.failed_barcodes:
            return False
        if barcode:
            self.failed_barcodes.add(barcode)
        self.count += 1
        if self.count >= self.threshold and not self.alarmed:
            self.alarmed = True
            return True
        return False
