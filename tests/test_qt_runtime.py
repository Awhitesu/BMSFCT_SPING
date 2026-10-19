import pytest

from core.qt_runtime import validate_qt_versions


@pytest.mark.parametrize('version',['6.10.0','6.10.1','6.5.0'])
def test_unvalidated_old_runtime_is_rejected(version):
    with pytest.raises(RuntimeError,match='PYSIDE-3246'):
        validate_qt_versions(version,version,version)


def test_mixed_binding_versions_are_rejected():
    with pytest.raises(RuntimeError,match='版本不一致'):
        validate_qt_versions('6.10.3','6.10.1','6.10.3')


@pytest.mark.parametrize('version',['6.10.2','6.10.3','6.11.0'])
def test_matching_fixed_runtime_is_accepted(version):
    assert validate_qt_versions(version,version,version)
