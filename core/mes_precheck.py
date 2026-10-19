"""Pre-test MES gate. No verdict cache: each attempt checks its scanned SN."""
import math
from urllib.parse import urlparse

from core.mes_client import MesClient


def validate_settings(config):
    enabled = config.get('mes_precheck_enabled', False)
    if type(enabled) is not bool:
        raise ValueError('MES过站检查开关无效')
    if not enabled:
        return
    url = urlparse(str(config.get('mes_url', '')).strip())
    if url.scheme not in ('http', 'https') or not url.hostname:
        raise ValueError('请配置有效的MES接口URL')
    if not str(config.get('mes_equipment_no', '')).strip():
        raise ValueError('请配置MES设备/工站编号equipmentNo')
    timeout = float(config.get('mes_timeout', 10))
    if not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError('MES超时必须在0–120秒之间且大于0')


def check_before_test(config, sn, operator, cancelled, logger):
    validate_settings(config)
    if not config.get('mes_precheck_enabled', False):
        return True, ''
    if cancelled():
        return False, '已取消'
    logger(f"[MES前置过站] 开始 | SN={sn} | equipmentNo={config['mes_equipment_no']} | orderNo={config.get('mes_order_no', '')} | userName={operator}")
    try:
        result = MesClient(config['mes_url'], config.get('mes_timeout', 10)).move_in_check(
            config['mes_equipment_no'], config.get('mes_order_no', ''), sn, operator)
        passed, message = result.passed is True, result.message
    except Exception as exc:
        passed, message = False, f'MES请求异常: {exc}'
    logger(f"[MES前置过站] {'PASS' if passed else 'FAIL'} | SN={sn} | {message}")
    if cancelled():
        return False, '已取消，MES响应不再用于启动测试'
    return passed, message
