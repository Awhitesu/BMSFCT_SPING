from core.production_statistics import ProductionStatistics


def test_repeated_ng_counts_one_product():
    stats = ProductionStatistics()
    stats.record('SN1', False, 'run1')
    stats.record('SN1', False, 'run2')
    assert stats.counts() == (1, 0, 1)
    assert stats.counts(False) == (2, 0, 2)


def test_retest_pass_replaces_ng_and_latest_ng_replaces_pass():
    stats = ProductionStatistics()
    stats.record('SN1', False)
    stats.record('SN1', True)
    assert stats.counts() == (1, 1, 0)
    stats.record('SN1', False)
    assert stats.counts() == (1, 0, 1)


def test_other_barcodes_missing_barcodes_and_duplicate_signal():
    stats = ProductionStatistics()
    stats.record(' SN1 ', False, 'a')
    stats.record('SN1', False, 'a')
    stats.record('SN2', True, 'b')
    stats.record('', False, 'c')
    stats.record('', False, 'd')
    assert stats.counts() == (4, 1, 3)
    stats.clear()
    assert stats.counts() == (0, 0, 0)
    stats.record('SN1', True, 'a')
    assert stats.counts() == (1, 1, 0)
