"""Six independent resistor boards exposed as fixed global channels 1..36."""
import time
from concurrent.futures import ThreadPoolExecutor


def channel_routes(boards):
    routes={}
    for board in boards.values():
        setting=board.setting
        if setting['kind']!='resistor' or not setting['enabled']:continue
        number=setting['number']
        for local in range(1,7):
            global_channel=(number-1)*6+local
            if global_channel in routes:raise ValueError('电阻板序号重复')
            routes[global_channel]=(board,local)
    return routes


def operate_channels(boards,channels,operation,resistance=0):
    routes=channel_routes(boards);channels=list(channels)
    if not channels:raise ValueError('没有选中有效通道')
    if len(channels)!=len(set(channels)) or any(ch not in routes for ch in channels):
        raise ValueError('所选通道未配置或重复')
    if operation not in ('read','set','disconnect'):raise ValueError('操作无效')
    if operation=='set' and (not isinstance(resistance,int) or not 0<=resistance<=1500000 or resistance%10):
        raise ValueError('阻值需为10Ω整数倍，范围0–1,500,000Ω')
    groups={}
    for channel in channels:
        board,local=routes[channel]
        groups.setdefault(id(board),(board,[]))[1].append((channel,local))
    started=time.perf_counter()
    def execute(group):
        board,selected=group;values={};errors={}
        try:
            if operation=='disconnect':
                board.disconnect();return values,errors
            if not board.is_connected and not board.connect():raise ConnectionError(board.last_error)
        except Exception as exc:
            return values,{ch:str(exc) for ch,_ in selected}
        for channel,local in selected:
            try:
                values[channel]=(board.set_resistance(resistance,local) if operation=='set'
                                 else board.read_resistance(local))
            except Exception as exc:errors[channel]=str(exc)
        return values,errors
    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        results=list(pool.map(execute,groups.values()))
    values={ch:value for row,_ in results for ch,value in row.items()}
    errors={ch:error for _,row in results for ch,error in row.items()}
    return {'ok':not errors,'values':values,'errors':errors,'elapsed':time.perf_counter()-started}
