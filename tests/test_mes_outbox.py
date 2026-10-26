import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.mes_client import MesCheckResult
from core.mes_outbox import create_upload_job, pending_jobs, send_job
from test_generic_mes import settings, endpoint


def rows():
    return [dict(name='电压', step_id='step-1', lower=3, upper=4, value='3.2', unit='V', passed=True)]


def job(tmp_path, **overrides):
    return Path(create_upload_job(settings(**overrides), 'SN-1', True, rows(), tmp_path/'test.db', 'run-1'))


def test_failed_twice_then_replay_keeps_original_parameters_and_payload(tmp_path):
    path = job(tmp_path)
    original = json.loads(path.read_text(encoding='utf-8'))
    with patch('core.mes_outbox.GenericMesClient') as client:
        client.return_value.post.return_value = MesCheckResult(False, '网络超时')
        failed = send_job(path)
        assert not failed['passed'] and failed['attempts'] == 2
        assert client.return_value.post.call_count == 2
        assert path in pending_jobs(tmp_path/'test.db')
        saved = json.loads(path.read_text(encoding='utf-8'))
        assert len(saved['history']) == 2 and saved['last_error'] == '网络超时'
        assert saved['payload'] == original['payload']
    # A new runtime/configuration cannot replace the frozen URL, station or values.
    with patch('core.mes_outbox.GenericMesClient') as client:
        client.return_value.post.return_value = MesCheckResult(True, '成功')
        replay = send_job(path)
        client.assert_called_once_with(original['url'], original['timeout'])
        client.return_value.post.assert_called_once_with(original['payload'])
    assert replay['passed'] and not path.exists()
    archived = json.loads(Path(replay['path']).read_text(encoding='utf-8'))
    assert archived['state'] == 'sent' and len(archived['history']) == 3
    with patch('core.mes_outbox.GenericMesClient') as client:
        assert send_job(path)['passed']
        client.assert_not_called()


@pytest.mark.parametrize('outcomes,expected,attempts', [
    ([MesCheckResult(True, '成功')], True, 1),
    ([MesCheckResult(False, '网络波动'), MesCheckResult(True, '成功')], True, 2),
    ([MesCheckResult(False, '工单关闭'), MesCheckResult(False, '工单关闭')], False, 2),
    ([TimeoutError('超时'), OSError('断网')], False, 2),
])
def test_retry_budget_and_last_error(tmp_path, outcomes, expected, attempts):
    path = job(tmp_path)
    with patch('core.mes_outbox.GenericMesClient.post', side_effect=outcomes) as post:
        result = send_job(path)
    assert result['passed'] is expected and result['attempts'] == attempts
    assert post.call_count == attempts
    if not expected:
        assert path.exists() and result['message']


def test_configured_one_attempt_does_not_retry(tmp_path):
    path = job(tmp_path, generic_mes_upload_attempts=1)
    with patch('core.mes_outbox.GenericMesClient.post', return_value=MesCheckResult(False, '失败')) as post:
        assert send_job(path)['attempts'] == 1
    assert post.call_count == 1 and path.exists()


def test_cancelled_round_preserves_pending_file_without_request(tmp_path):
    path = job(tmp_path)
    with patch('core.mes_outbox.GenericMesClient.post') as post:
        assert not send_job(path, lambda: True)['passed']
    post.assert_not_called()
    assert path.exists()


def test_repeated_sn_has_separate_files(tmp_path):
    first, second = job(tmp_path), job(tmp_path)
    assert first != second
    assert len(pending_jobs(tmp_path/'test.db')) == 2


@pytest.mark.parametrize('count', [80, 266, 10000])
def test_large_payload_has_no_local_total_size_or_item_count_limit(tmp_path, count):
    lots = rows() * count
    path = Path(create_upload_job(settings(), 'SN', False, lots, tmp_path/'test.db'))
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert len(json.dumps(saved['payload']['checkDetail'], ensure_ascii=False)) > 5000
    with patch('core.mes_outbox.GenericMesClient.post', return_value=MesCheckResult(True, '成功')) as post:
        result = send_job(path)
    assert result['passed'] and not path.exists()
    post.assert_called_once_with(saved['payload'])
    assert len(post.call_args.args[0]['checkDetail']) == count
    archived = json.loads(Path(result['path']).read_text(encoding='utf-8'))
    assert archived['payload'] == saved['payload']
    assert len(archived['source']['rows']) == count


def test_invalid_field_build_failure_is_still_retained(tmp_path):
    broken = rows()
    broken[0]['name'] = 'x' * 51
    path = Path(create_upload_job(settings(), 'SN', True, broken, tmp_path/'test.db'))
    assert path.exists()
    with patch('core.mes_outbox.GenericMesClient.post') as post:
        assert not send_job(path)['passed']
    post.assert_not_called()
    assert path.exists()


def test_large_payload_reaches_http_server_and_preserves_data_on_rejection(tmp_path, endpoint):
    endpoint.status, endpoint.body = 200, {'success': False, 'msg': '服务端拒绝示例'}
    path = Path(create_upload_job(settings(generic_mes_upload_url=endpoint.url),
        'SN', True, rows() * 266, tmp_path/'test.db'))
    before = json.loads(path.read_text(encoding='utf-8'))['payload']
    result = send_job(path)
    assert not result['passed'] and '服务端拒绝示例' in result['message']
    assert result['attempts'] == 2 and len(endpoint.requests) == 2
    assert all(request[2] == before for request in endpoint.requests)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert len(saved['payload']['checkDetail']) == 266
    assert saved['payload'] == before and saved['state'] == 'pending'
    assert all(event['status_code'] == 200 for event in saved['history'])


def test_successfully_recorded_but_not_archived_is_not_sent_again(tmp_path):
    path = job(tmp_path)
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved['state'] = 'sent'
    saved.pop('payload_schema', None)
    path.write_text(json.dumps(saved), encoding='utf-8')
    with patch('core.mes_outbox.GenericMesClient.post') as post:
        result = send_job(path)
    post.assert_not_called()
    assert result['passed'] and Path(result['path']).exists() and not path.exists()


def test_bad_file_is_preserved_and_reported(tmp_path):
    path = job(tmp_path)
    path.write_text('{broken', encoding='utf-8')
    with patch('core.mes_outbox.GenericMesClient.post') as post:
        result = send_job(path)
    assert not result['passed'] and path.read_text() == '{broken'
    post.assert_not_called()


def test_real_http_failure_then_success_uses_same_json_payload(tmp_path, endpoint):
    endpoint.responses = [(503, {'success': False, 'msg': '网关暂不可用'}), (200, {'success': True})]
    path = job(tmp_path, generic_mes_upload_url=endpoint.url.replace('/check', '/upload'))
    result = send_job(path)
    assert result['passed'] and result['attempts'] == 2
    assert len(endpoint.requests) == 2
    assert endpoint.requests[0] == endpoint.requests[1]
    route, content_type, body = endpoint.requests[0]
    assert route == '/upload' and 'application/json' in content_type
    assert body['uniqueId'] == 'SN-1' and body['stationId'] == 'P01' and body['isPass'] == 'pass'
    assert body['checkDetail'][0]['checkType'] == '1' and body['checkDetail'][0]['checkItem'] == '电压'
    assert body['checkDetail'][0]['stValue'] == '3.5'


def test_legacy_pending_file_is_corrected_from_original_measurements_before_replay(tmp_path):
    path = job(tmp_path)
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved.pop('payload_schema')
    old_payload = dict(saved['payload'])
    old_payload['checkDetail'] = json.dumps([dict(checkType='OLD', checkItem='1', stValue='3~4',
                                                   realValue='3.2', itemUnit='V', itemRes='pass')])
    saved['payload'] = old_payload
    saved['source']['config'].update(generic_mes_check_type='OLD', generic_mes_check_item='1',
                                      generic_mes_detail_format='json_string')
    saved['history'] = [dict(passed=False, message='旧请求失败')]
    path.write_text(json.dumps(saved), encoding='utf-8')
    def upload(payload):
        on_disk = json.loads(path.read_text(encoding='utf-8'))
        assert on_disk['payload'] == payload and on_disk['legacy_payload'] == old_payload
        assert payload['checkDetail'] == [dict(checkType='1', checkItem='电压', stValue='3.5',
                                               realValue='3.2', itemUnit='V', itemRes='pass')]
        return MesCheckResult(True, '成功')
    with patch('core.mes_outbox.GenericMesClient.post', side_effect=upload) as post:
        result = send_job(path)
    assert result['passed'] and post.call_count == 1
    archived = json.loads(Path(result['path']).read_text(encoding='utf-8'))
    assert archived['legacy_payload'] == old_payload
    assert archived['payload_schema'] == 2
    assert len(archived['history']) == 2 and archived['history'][0]['message'] == '旧请求失败'


def test_legacy_file_without_source_is_preserved_without_guessing_data(tmp_path):
    path = job(tmp_path)
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved.pop('payload_schema')
    saved.pop('source')
    path.write_text(json.dumps(saved), encoding='utf-8')
    original = path.read_bytes()
    with patch('core.mes_outbox.GenericMesClient.post') as post:
        result = send_job(path)
    assert not result['passed'] and '缺少原始测试明细' in result['message']
    post.assert_not_called()
    assert path.read_bytes() == original
