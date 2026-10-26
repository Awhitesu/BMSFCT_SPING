"""锐能 MES 查询及上传数据契约，与精华 moveInCheck 独立。"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
import json
import math
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

from core.mes_client import MesCheckResult


DEFAULTS = {
    'generic_mes_query_enabled': True,
    'generic_mes_upload_enabled': True,
    'generic_mes_query_url': 'http://192.168.5.65/api/mes/checkBmsSnInfo',
    'generic_mes_upload_url': 'http://192.168.5.65/api/mes/checkBmsProc',
    'generic_mes_station_id': '',
    'generic_mes_timeout': 10.0,
    'generic_mes_max_check_times': 3,
    'generic_mes_upload_attempts': 2,
}


def settings_from(config):
    return {key: deepcopy(config.get(key, value)) for key, value in DEFAULTS.items()}


def config_path(db_path=None):
    from core import system_config
    folder = Path(db_path).resolve().parent / 'config' if db_path else Path(system_config.default_path()).parent
    return folder / 'generic_mes.json'


def load_runtime_settings(system_settings, db_path=None):
    """The master switch lives in sys_config; disabled means no file access."""
    enabled = system_settings.get('generic_mes_enabled', False)
    if type(enabled) is not bool:
        raise ValueError('锐能MES总开关无效')
    if not enabled:
        return {}
    if system_settings.get('mes_precheck_enabled', False):
        raise ValueError('精华MES与锐能MES不能同时启用，请重新选择方案')
    cfg = load_file(config_path(db_path))
    if not cfg['generic_mes_query_enabled'] or not cfg['generic_mes_upload_enabled']:
        raise ValueError('启用锐能MES时必须同时开启扫码查询和结果上传，请重新保存锐能参数')
    return cfg


def load_file(path, *, validate=True):
    with open(path, encoding='utf-8-sig') as stream:
        cfg = json.load(stream)
    if not isinstance(cfg, dict):
        raise ValueError('锐能MES配置文件必须是JSON对象')
    return validate_settings(cfg) if validate else settings_from(cfg)


def save_file(config, path):
    from core import system_config
    system_config.update(validate_settings(config), str(path))


def _field(value, name, required=True):
    text = str(value if value is not None else '').strip()
    if (required and not text) or len(text) > 50:
        raise ValueError(f'锐能MES {name} 必须为{"1–50" if required else "0–50"}个字符')
    return text


def validate_settings(config):
    cfg = settings_from(config)
    for key, lower, upper in (('generic_mes_max_check_times', 0, 1000000),
                              ('generic_mes_upload_attempts', 1, 2)):
        if type(cfg[key]) is not int or not lower <= cfg[key] <= upper:
            raise ValueError(f'锐能MES {key} 必须是{lower}–{upper}之间的整数')
    for key in ('generic_mes_query_enabled', 'generic_mes_upload_enabled'):
        if type(cfg[key]) is not bool:
            raise ValueError('锐能MES查询/上传开关无效')
    if not (cfg['generic_mes_query_enabled'] or cfg['generic_mes_upload_enabled']):
        return cfg
    _field(cfg['generic_mes_station_id'], 'stationId')
    timeout = float(cfg['generic_mes_timeout'])
    if not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError('锐能MES超时必须大于0且不超过120秒')
    for action in ('query', 'upload'):
        if cfg[f'generic_mes_{action}_enabled']:
            url = urlparse(str(cfg[f'generic_mes_{action}_url']).strip())
            if url.scheme not in ('http', 'https') or not url.hostname:
                raise ValueError(f'锐能MES {action} 接口URL无效')
    return cfg


def _item_codes(item, fallback_sequence):
    sequence = item.get('sequence', fallback_sequence)
    text = str(sequence).strip()
    if isinstance(sequence, bool) or not text.isascii() or not text.isdigit() or int(text) < 1:
        raise ValueError('锐能MES测试项序号必须是从1开始的正整数')
    return (
        _field(str(int(text)), 'checkType'),
        _field(item['name'], 'checkItem'),
    )


def standard_value(lower, upper):
    """Both limits -> midpoint; one limit -> that original standard."""
    values = [value for value in (lower, upper) if value is not None and str(value).strip()]
    if not values:
        return ''
    if len(values) == 1:
        return str(values[0]).strip()
    try:
        with localcontext() as context:
            context.prec = 64
            first, second = (Decimal(str(value).strip()) for value in values)
            if not first.is_finite() or not second.is_finite():
                raise ValueError('标准值上下限必须是有限数值')
            midpoint = (first + second) / 2
            text = format(midpoint, 'f')
            if '.' in text:
                text = text.rstrip('0').rstrip('.')
            return '0' if midpoint == 0 else text
    except InvalidOperation as exc:
        if str(values[0]) == str(values[1]):
            return str(values[0]).strip()
        raise ValueError('无法计算标准值：上下限必须是数值') from exc


def validate_recipe(config, items):
    cfg = validate_settings(config)
    if cfg['generic_mes_upload_enabled']:
        for sequence, item in enumerate(items, 1):
            if not item.get('skip_runtime') and not str(item.get('name', '')).strip().startswith('#'):
                try:
                    _item_codes(item, sequence)
                except ValueError as exc:
                    raise ValueError(f'测试项【{item.get("name", "")}】：{exc}') from exc


class GenericMesClient:
    def __init__(self, url, timeout=10):
        self.url = str(url).strip()
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 120:
            raise ValueError('锐能MES请求超时无效')

    def post(self, payload):
        req = request.Request(self.url, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8'),
                              headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                status, raw = response.status, response.read()
        except error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (error.URLError, TimeoutError, OSError) as exc:
            return MesCheckResult(False, f'锐能MES请求失败：{exc}')
        try:
            body = json.loads(raw.decode('utf-8-sig'))
        except (ValueError, UnicodeError):
            return MesCheckResult(False, f'HTTP {status}：锐能MES返回的内容不是有效JSON', status,
                                  raw.decode('utf-8', errors='replace'))
        passed = 200 <= status < 300 and isinstance(body, dict) and body.get('success') is True
        message = str(body.get('msg') or body.get('message') or body.get('error') or '') if isinstance(body, dict) else ''
        message = message or ('锐能MES处理成功' if passed else '锐能MES未明确返回 success: true')
        if not 200 <= status < 300:
            message = f'HTTP {status}：{message}'
        return MesCheckResult(passed, message, status, body)


def check_before_test(config, sn, cancelled, logger):
    cfg = validate_settings(config)
    if not cfg['generic_mes_query_enabled']:
        return MesCheckResult(True, '')
    if cancelled():
        return MesCheckResult(False, '已取消')
    payload = dict(uniqueId=_field(sn, 'uniqueId'), stationId=_field(cfg['generic_mes_station_id'], 'stationId'))
    logger('[锐能MES过站] 开始 | ' + json.dumps(payload, ensure_ascii=False))
    try:
        result = GenericMesClient(cfg['generic_mes_query_url'], cfg['generic_mes_timeout']).post(payload)
    except Exception as exc:
        result = MesCheckResult(False, f'锐能MES查询异常：{exc}')
    if result.passed:
        try:
            if not isinstance(result.response, dict) or not isinstance(result.response.get('data'), dict):
                raise ValueError('查询响应缺少有效的data信息')
            data = result.response['data']
            if not isinstance(data, dict) or str(data.get('uniqueId', '')).strip() != payload['uniqueId']:
                raise ValueError('查询返回的产品唯一码与本次SN不一致')
            for key in ('orderId', 'totalNum', 'procNum', 'checkTimes'):
                if key not in data or data[key] is None or not str(data[key]).strip():
                    raise ValueError(f'查询响应缺少有效字段 {key}')
            count = str(data['checkTimes']).strip()
            if not count.isascii() or not count.isdigit():
                raise ValueError('查询返回的检测次数checkTimes不是非负整数')
            limit = cfg['generic_mes_max_check_times']
            if int(count) > limit:
                raise ValueError(f'检测次数 {count} 大于最大允许检测次数 {limit}，禁止测试')
            result = MesCheckResult(True, f'锐能MES查询通过，检测次数 {count}，上限 {limit}',
                                    result.status_code, result.response)
        except (KeyError, TypeError, ValueError) as exc:
            result = MesCheckResult(False, str(exc), result.status_code, result.response)
    logger(f'[锐能MES过站] {"PASS" if result.passed else "NG"} | {result.message} | '
           + json.dumps(result.response, ensure_ascii=False))
    if cancelled():
        return MesCheckResult(False, '已取消，MES响应不再用于启动测试')
    return result


def build_upload_payload(config, sn, passed, rows, *, validate=True):
    cfg = validate_settings(config)
    details = []
    for sequence, row in enumerate(rows, 1):
        check_type, check_item = _item_codes(row, sequence)
        lower, upper = row.get('lower'), row.get('upper')
        standard = standard_value(lower, upper)
        unit = str(row.get('unit') or '')
        if unit.upper() in ('NULL', 'NONE', '--'):
            unit = ''
        details.append(dict(checkType=check_type, checkItem=check_item,
                            stValue=_field(standard, 'stValue', required=False),
                            realValue=str(row.get('value') if row.get('value') is not None else ''),
                            itemUnit=_field(unit, 'itemUnit', required=False),
                            itemRes='pass' if row['passed'] else 'fail'))
    if not details:
        raise ValueError('锐能MES上传没有已完成的检测项目')
    payload = dict(uniqueId=_field(sn, 'uniqueId'), stationId=_field(cfg['generic_mes_station_id'], 'stationId'),
                isPass='pass' if passed else 'fail',
                checkDetail=details)
    if validate:
        validate_upload_payload(payload)
    return payload


def validate_upload_payload(payload):
    """Validate fields without a total checkDetail length or item-count limit."""
    _field(payload.get('uniqueId'), 'uniqueId')
    _field(payload.get('stationId'), 'stationId')
    if payload.get('isPass') not in ('pass', 'fail'):
        raise ValueError('锐能MES综合判定必须为pass或fail')
    detail = payload.get('checkDetail')
    if not isinstance(detail, list) or not detail:
        raise ValueError('锐能MES checkDetail必须为非空JSON数组')
    for row in detail:
        for field in ('checkType', 'checkItem', 'stValue', 'realValue', 'itemUnit', 'itemRes'):
            if field not in row or not isinstance(row[field], str):
                raise ValueError(f'锐能MES {field} 必须是字符串')
            _field(row[field], field, required=field in ('checkType', 'checkItem', 'itemRes'))
        if row['itemRes'] not in ('pass', 'fail'):
            raise ValueError('锐能MES项目判定必须为pass或fail')
        if not row['checkType'].isascii() or not row['checkType'].isdigit() or int(row['checkType']) < 1:
            raise ValueError('锐能MES checkType必须是测试项的正整数序号字符串')
