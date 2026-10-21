from copy import deepcopy
import ipaddress

NOMINAL = (50000, 100000, 125000, 250000, 500000, 800000, 1000000)
DATA = (100000, 125000, 250000, 500000, 800000, 1000000, 2000000, 4000000, 5000000)


def validate_zlg_config(value):
    value = deepcopy(value)
    ipaddress.IPv4Address(value['ip'])
    if not 1 <= int(value['port']) <= 65535:
        raise ValueError('TCP端口必须为1–65535')
    if len(value['channels']) != 4:
        raise ValueError('必须配置四个通道')
    for index, row in enumerate(value['channels']):
        if row['nominal'] not in NOMINAL or row['data'] not in DATA:
            raise ValueError(f'CH{index+1}波特率不受当前接口支持')
        if type(row['enabled']) is not bool or type(row['termination']) is not bool:
            raise ValueError('通道使能和终端电阻必须为布尔值')
    enabled = [index+1 for index,row in enumerate(value['channels']) if row['enabled']]
    if not enabled:raise ValueError('至少启用一个物理通道')
    # Retain the former default only as a legacy connection anchor; steps carry their own ports.
    channel = int(value.get('send_channel', enabled[0]))
    if not 1 <= channel <= 4:raise ValueError('旧配方通道编号必须为CH1–CH4')
    value['send_channel'] = channel if channel in enabled else enabled[0]
    return value


