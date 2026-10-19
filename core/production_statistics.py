"""Session statistics with switchable barcode deduplication."""
class ProductionStatistics:
    def __init__(self):
        self.events = []
        self.run_ids = set()
        self._counts_cache = {}
        self._details_cache = None

    def record(self, barcode, passed, run_id=None):
        if run_id and run_id in self.run_ids:
            return
        if run_id:
            self.run_ids.add(run_id)
        self.events.append((str(barcode or '').strip(), bool(passed)))
        self._counts_cache.clear()
        self._details_cache = None

    def counts(self, deduplicate=True):
        if deduplicate in self._counts_cache:return self._counts_cache[deduplicate]
        if deduplicate:
            latest = {}
            for index, (barcode, passed) in enumerate(self.events):
                # Missing barcodes must never merge unrelated products.
                latest[('sn', barcode) if barcode else ('anonymous', index)] = passed
            values = list(latest.values())
        else:
            values = [passed for _, passed in self.events]
        result=(len(values),sum(values),len(values)-sum(values))
        self._counts_cache[deduplicate]=result
        return result

    def restore(self, rows):
        self.clear()
        for barcode, result, identity in rows:
            self.record(barcode,result == 'PASS',f'db:{identity}')

    def details(self):
        if self._details_cache is not None:return dict(self._details_cache)
        first = {}
        for index,(barcode,passed) in enumerate(self.events):
            first.setdefault(('sn',barcode) if barcode else ('anonymous',index),passed)
        products=len(first)
        self._details_cache=dict(products=products,attempts=len(self.events),retests=len(self.events)-products,
                    first_passed=sum(first.values()),
                    first_pass_rate=100*sum(first.values())/products if products else 0.0)
        return dict(self._details_cache)

    def clear(self):
        self.events.clear()
        self.run_ids.clear()
        self._counts_cache.clear()
        self._details_cache = None
