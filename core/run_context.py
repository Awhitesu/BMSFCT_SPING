"""Immutable, credential-filtered recipe and configured hardware evidence."""
import hashlib
import json


def _safe(value):
    if isinstance(value, dict):
        return {str(key): ('[REDACTED]' if any(word in str(key).lower().replace('_','')
                for word in ('password','secret','token','apikey','credential')) else _safe(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def build_run_context(recipe, configuration=None):
    recipe = _safe({key: value for key, value in recipe.items() if key != 'operator_name'})
    allowed = {'ip','port','com','baudrate','enabled','number','sequence_number',
               'max_channels','channels','kind','limit','name','key','simulators','rn_boards',
               'channel','device_type','connect_mode'}
    def hardware(value):
        if isinstance(value, dict):
            return {key: hardware(item) for key,item in value.items()
                    if key in allowed or key.endswith(('_ip','_port','_com','_baudrate','_enabled',
                                                       '_channels','_sequence','_connect_mode','_number'))}
        if isinstance(value, list): return [hardware(item) for item in value]
        return value
    # Board identifiers are dictionary keys, not configuration field names.
    cfg = configuration or {}
    devices = hardware(cfg)
    if isinstance(cfg.get('rn_boards'),dict):
        devices['rn_boards'] = {key: hardware(value) for key,value in cfg['rn_boards'].items()}
    devices = _safe(devices)
    def digest(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
            separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
    from core.report_settings import settings_from
    return dict(schema=1, recipe=recipe, recipe_sha256=digest(recipe),report_settings=settings_from(cfg),
                configured_hardware=devices, hardware_sha256=digest(devices))
