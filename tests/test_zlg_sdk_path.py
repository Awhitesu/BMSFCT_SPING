from pathlib import Path

from devices import manager


def test_relative_sdk_uses_bundle_root_not_working_directory(tmp_path, monkeypatch):
    bundle = tmp_path / '_internal'
    sdk = bundle / 'vendor' / 'zlgcan(20260806)' / 'zlgcan_x64'
    sdk.mkdir(parents=True)
    (sdk / 'zlgcan.dll').write_bytes(b'test')
    monkeypatch.setattr(manager, '_PROJECT_ROOT', bundle)
    monkeypatch.setattr(manager, '_ZLG_SDK_DEFAULT_DIR', sdk)
    monkeypatch.chdir(tmp_path)
    for value in ('', 'vendor/zlgcan(20260806)/zlgcan_x64',
                  'zlgcan(20260806)/zlgcan_x64'):
        assert Path(manager.resolve_zlg_sdk_path(value)) == sdk


def test_explicit_absolute_sdk_is_preserved(tmp_path):
    custom = tmp_path / 'external-sdk'
    assert manager.resolve_zlg_sdk_path(str(custom)) == str(custom)
