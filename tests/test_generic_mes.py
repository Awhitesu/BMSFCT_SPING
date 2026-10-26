import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core import generic_mes as mes
from core.mes_client import MesCheckResult
from ui.tabs.overview_tab import OverviewTab


def settings(**overrides):
    return {**mes.DEFAULTS, 'generic_mes_query_enabled': True, 'generic_mes_upload_enabled': True,
            'generic_mes_query_url': 'http://127.0.0.1/check', 'generic_mes_upload_url': 'http://127.0.0.1/upload',
            'generic_mes_station_id': 'P01', **overrides}


def query_body(sn='SN', count='1'):
    return {'success': True, 'data': dict(uniqueId=sn, checkTimes=count,
                                        orderId='ORDER-1', totalNum='500', procNum='300')}


@pytest.fixture
def endpoint():
    state = SimpleNamespace(body={'success': True}, status=200, requests=[], responses=[])
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state.requests.append((self.path, self.headers['Content-Type'],
                                   json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            status, body = state.responses.pop(0) if state.responses else (state.status, state.body)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8'))

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f'http://127.0.0.1:{server.server_port}/check'
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize('body,status,passed,message', [
    (query_body('SN中文'), 200, True, '通过'),
    ({'success': False, 'msg': '前站未完成'}, 200, False, '前站未完成'),
    ({'success': 'true'}, 200, False, 'success'),
    ({'success': 1}, 200, False, 'success'),
    ({'code': 200}, 200, False, 'success'),
    (True, 200, False, 'success'),
    ({'success': True, 'msg': '服务不可用'}, 503, False, '服务不可用'),
    (b'<html>gateway error</html>', 200, False, 'JSON'),
])
def test_contract_requires_real_boolean_and_preserves_rejection(endpoint, body, status, passed, message):
    endpoint.body, endpoint.status = body, status
    cfg = settings(generic_mes_query_url=endpoint.url)
    result = mes.check_before_test(cfg, 'SN中文', lambda: False, Mock())
    assert result.passed is passed and message in result.message
    path, content_type, payload = endpoint.requests[0]
    assert path == '/check' and 'application/json' in content_type
    assert payload == {'uniqueId': 'SN中文', 'stationId': 'P01'}


def test_master_switch_disabled_never_opens_file_even_if_it_is_broken(tmp_path):
    path = tmp_path / 'config' / 'generic_mes.json'
    path.parent.mkdir()
    path.write_text('{broken', encoding='utf-8')
    with patch('core.generic_mes.load_file', side_effect=AssertionError('disabled must not load')):
        assert mes.load_runtime_settings({'generic_mes_enabled': False}, tmp_path/'data.db') == {}
        assert mes.load_runtime_settings({}, tmp_path/'data.db') == {}
    with pytest.raises(ValueError):
        mes.load_runtime_settings({'generic_mes_enabled': True}, tmp_path/'data.db')


def test_enabled_settings_load_only_independent_json_and_freeze_values(tmp_path):
    path = mes.config_path(tmp_path/'data.db')
    cfg = settings()
    mes.save_file(cfg, path)
    snapshot = mes.load_runtime_settings({'generic_mes_enabled': True, 'mes_url': 'PSA'}, tmp_path/'data.db')
    assert snapshot == cfg
    mes.save_file(settings(generic_mes_station_id='CHANGED'), path)
    assert snapshot['generic_mes_station_id'] == 'P01'
    assert json.loads(path.read_text(encoding='utf-8'))['generic_mes_station_id'] == 'CHANGED'


def test_query_rejection_precedes_hardware_and_record_creation():
    page = SimpleNamespace(_prepare_cancel=threading.Event(), preparation_log=Mock(),
                           _apply_recipe_hardware=Mock(), db_manager=Mock())
    snapshot = {'_generic_mes': settings(), 'operator_name': 'alice'}
    with patch.object(mes.GenericMesClient, 'post', return_value=MesCheckResult(False, '条码不属于本站')):
        ok, reason = OverviewTab._prepare_run(page, snapshot, 'SN-1')
    assert not ok and '条码不属于本站' in reason
    page._apply_recipe_hardware.assert_not_called()
    page.db_manager.start_traced_test.assert_not_called()


def test_legacy_gate_is_preserved_and_scan_first_does_not_query_twice():
    page = SimpleNamespace(_prepare_cancel=threading.Event(), preparation_log=Mock(), channel_id=1,
                           _apply_recipe_hardware=Mock(return_value=(True, 'ready')), db_manager=None)
    snapshot = {'_generic_mes': settings(), '_mes_precheck': {'mes_precheck_enabled': True}, '_wait_board': True}
    with patch('core.mes_precheck.check_before_test', return_value=(False, 'PSA拒绝')) as psa, \
         patch.object(mes.GenericMesClient, 'post', return_value=MesCheckResult(True, 'OK', 200, query_body())) as generic:
        assert not OverviewTab._prepare_run(page, snapshot, 'SN')[0]
        generic.assert_not_called()
        psa.return_value = (True, 'PSA通过')
        assert OverviewTab._prepare_run(page, snapshot, 'SN')[0]
        assert snapshot['_mes_already_checked']
        snapshot['_wait_board'] = False
        assert OverviewTab._prepare_run(page, snapshot, 'SN')[0]
        generic.assert_called_once()
        page._apply_recipe_hardware.assert_called_once()


def test_independent_switches_and_cancelled_query_discard_response():
    with patch.object(mes.GenericMesClient, 'post', return_value=MesCheckResult(True, 'OK')) as post:
        assert mes.check_before_test(settings(generic_mes_query_enabled=False), 'SN', lambda: False, Mock()).passed
        post.assert_not_called()
        cancel = threading.Event()
        post.side_effect = lambda *_: cancel.set() or MesCheckResult(True, 'OK')
        assert not mes.check_before_test(settings(), 'SN', cancel.is_set, Mock()).passed


def test_payload_uploads_every_measured_item_and_ignores_old_mapping_overrides():
    cfg = settings(generic_mes_item_mapping=[dict(source='step-1', checkType='77', checkItem='100', stValue='3270', itemUnit='mV')])
    rows = [dict(name='Cell电压', step_id='step-1', lower=3000, upper=3500, value=3156, unit='mV', passed=True),
            dict(name='继电器', lower=None, upper=None, value='NG', unit='NULL', passed=False)]
    payload = mes.build_upload_payload(cfg, 'SN-1', False, rows)
    assert payload == dict(uniqueId='SN-1', stationId='P01', isPass='fail', checkDetail=[
        dict(checkType='1', checkItem='Cell电压', stValue='3250', realValue='3156', itemUnit='mV', itemRes='pass'),
        dict(checkType='2', checkItem='继电器', stValue='', realValue='NG', itemUnit='', itemRes='fail')])
    cfg['generic_mes_detail_format'] = 'json_string'
    assert mes.build_upload_payload(cfg, 'SN-1', False, rows)['checkDetail'] == payload['checkDetail']


@pytest.mark.parametrize('field,value', [('generic_mes_query_enabled', 'true'), ('generic_mes_station_id', ''),
    ('generic_mes_query_url', 'bad-url'), ('generic_mes_timeout', float('nan')), ('generic_mes_timeout', 0)])
def test_bad_enabled_configuration_cannot_start(field, value):
    with pytest.raises((ValueError, TypeError)):
        mes.validate_recipe(settings(**{field: value}), [{'name': 'test'}])


@pytest.mark.parametrize('count,allowed', [('0', True), ('2', True), ('3', True), ('4', False),
                                         ('-1', False), ('3.0', False), ('abc', False), ('', False), (True, False)])
def test_count_boundary_is_strictly_greater_than_limit(count, allowed):
    response = query_body(count=count)
    with patch.object(mes.GenericMesClient, 'post', return_value=MesCheckResult(True, 'OK', 200, response)):
        result = mes.check_before_test(settings(generic_mes_max_check_times=3), 'SN', lambda: False, Mock())
    assert result.passed is allowed
    if count == '4':
        assert '4' in result.message and '3' in result.message


@pytest.mark.parametrize('response', [dict(success=True), query_body('OTHER'),
    dict(success=True, data={'uniqueId': 'SN', 'checkTimes': '1'}), dict(success=True, data=[]),
    dict(success=True, data=None)])
def test_missing_or_wrong_query_data_cannot_start(response):
    with patch.object(mes.GenericMesClient, 'post', return_value=MesCheckResult(True, 'OK', 200, response)):
        assert not mes.check_before_test(settings(), 'SN', lambda: False, Mock()).passed


@pytest.mark.parametrize('key,value', [('generic_mes_max_check_times', -1), ('generic_mes_max_check_times', True),
    ('generic_mes_max_check_times', '3'), ('generic_mes_upload_attempts', 0), ('generic_mes_upload_attempts', 3)])
def test_count_and_upload_attempt_limits_are_validated(key, value):
    with pytest.raises(ValueError):
        mes.validate_settings(settings(**{key: value}))


def test_runtime_refuses_two_factories_and_query_bypass(tmp_path):
    cfg = settings(generic_mes_query_enabled=False)
    mes.save_file(cfg, mes.config_path(tmp_path/'test.db'))
    with pytest.raises(ValueError, match='同时启用'):
        mes.load_runtime_settings({'generic_mes_enabled': True, 'mes_precheck_enabled': True}, tmp_path/'test.db')
    with pytest.raises(ValueError, match='必须同时开启'):
        mes.load_runtime_settings({'generic_mes_enabled': True}, tmp_path/'test.db')


@pytest.mark.parametrize('lower,upper,expected', [
    (3000, 3500, '3250'), ('3', '4', '3.5'), ('0.1', '0.2', '0.15'),
    ('1.001', '1.002', '1.0015'), (-10, 10, '0'), (-10, -5, '-7.5'),
    (0, 0, '0'), (3, 3, '3'), (3, None, '3'), (None, 4, '4'),
    (0, None, '0'), (None, 0, '0'), ('', 4, '4'), (3, '', '3'),
    (None, None, ''), ('PASS', None, 'PASS'), ('00001', None, '00001'),
    ('00001', '00001', '1'),
])
def test_standard_is_midpoint_or_single_actual_limit(lower, upper, expected):
    assert mes.standard_value(lower, upper) == expected


def test_sequence_names_and_values_ignore_legacy_global_parameters():
    cfg = settings(generic_mes_check_type='OLD-TYPE', generic_mes_check_item='OLD-ITEM',
                   generic_mes_detail_format='json_string')
    rows = [dict(sequence=2, name='电压', lower=3, upper=4, value=3.2, unit='V', passed=True),
            dict(sequence=4, name='温度', lower=None, upper=50, value=25, unit='℃', passed=False)]
    details = mes.build_upload_payload(cfg, 'SN', False, rows)['checkDetail']
    assert isinstance(details, list)
    assert [row['checkType'] for row in details] == ['2', '4']
    assert [row['checkItem'] for row in details] == ['电压', '温度']
    assert [row['stValue'] for row in details] == ['3.5', '50']
    assert [row['realValue'] for row in details] == ['3.2', '25']
    assert [row['itemUnit'] for row in details] == ['V', '℃']
    assert [row['itemRes'] for row in details] == ['pass', 'fail']
