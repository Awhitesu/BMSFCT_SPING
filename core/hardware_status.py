"""Read cached driver state without issuing instrument commands."""
import os


def configuration_snapshot(manager):
    """Capture the parameters actually held by the shared driver instances."""
    rows = {}
    devices = {
        'hv': ('hv_source', 'ip', 'port', None),
        'dut_pwr': ('dut_power', 'port', None, 'baudrate'),
        'it6333b': ('it6333b', 'port', None, 'baudrate'),
        'ca550': ('ca550', 'port', None, 'baudrate'),
        'scanner': ('scanner', 'port', None, 'baudrate'),
        'easy320': ('easy320', 'ip', 'port', None),
        'dg2052': ('dg2052', 'ip', 'port', None),
        'dmm34461': ('dmm34461', 'ip', 'port', None),
        'tbs2102b': ('tbs2102b', 'ip', None, None),
        'opteeq': ('opteeq', 'ip', 'port', None),
        'e2_rfp': ('e2_rfp', 'rfp_cli_path', None, None),
        'pcan': ('pcan', 'channel', 'nominal_bitrate', 'data_bitrate'),
    }
    for key, (attribute, address, port, baud) in devices.items():
        driver = getattr(manager, attribute, None)
        if driver is None:
            continue
        row = {'address': getattr(driver, address, ''), 'enabled': manager.is_enabled(key)}
        for field, name in (('port', port), ('baud', baud)):
            if name:
                row[field] = getattr(driver, name, '')
        rows[key] = row
    for slot, driver in enumerate(getattr(manager, 'simulators', ()), 1):
        key = f'sim{slot}'
        rows[key] = dict(address=driver.ip, port=driver.port,
                         enabled=manager.is_enabled(key), number=driver.sequence_number,
                         channels=driver.max_channels)
    driver = getattr(manager, 'pcan', None)
    if driver is not None:
        rows['zlg'] = dict(address=driver.zlg_ip, port=driver.zlg_port)
        rows['zlg_sdk'] = dict(address=driver.zlg_sdk_path)
    from devices.rn_board import board_settings
    db = getattr(manager, 'db_manager', None)
    for setting in board_settings((db.load_sys_config() if db else {}) or {}):
        board = getattr(manager, 'rn_boards', {}).get(setting['key'])
        actual = board.setting if board is not None else setting
        row = dict(address=actual['ip'], port=actual['port'], baud=actual['unit'], enabled=setting['enabled'])
        if setting['kind'] == 'resistor':
            row['number'] = actual['number']
        rows[setting['key']] = row
    return rows


def configuration_matches(key, draft, applied):
    for field, actual in applied.items():
        value = draft.get(field)
        if field in ('port', 'baud', 'number', 'channels'):
            try:
                if int(value) != int(actual):
                    return False
            except (ValueError, TypeError):
                return False
        elif field == 'address':
            value, actual = str(value).strip(), str(actual).strip()
            if key in ('zlg_sdk', 'e2_rfp') and value and actual:
                value, actual = (os.path.normcase(os.path.abspath(p)) for p in (value, actual))
            if value != actual:
                return False
        elif value != actual:
            return False
    return True
