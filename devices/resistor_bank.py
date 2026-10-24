"""Enabled resistor boards, ordered by sequence, expose consecutive channels."""
import time
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from core.board_control import control_board
from core.numeric_validation import finite_reading


def channel_routes(boards):
    routes={}
    enabled=[board for board in boards.values() if board.setting['kind']=='resistor' and board.setting['enabled']]
    numbers=[board.setting['number'] for board in enabled]
    if any(type(n) is not int or not 1<=n<=6 for n in numbers):raise ValueError('电阻板序号必须为1～6')
    if len(numbers)!=len(set(numbers)):raise ValueError('电阻板序号重复')
    for position,board in enumerate(sorted(enabled,key=lambda b:b.setting['number'])):
        setting=board.setting
        if setting['kind']!='resistor' or not setting['enabled']:continue
        number=setting['number']
        if type(number) is not int or not 1<=number<=6:raise ValueError('电阻板序号必须为1～6')
        for local in range(1,7):
            global_channel=position*6+local
            if global_channel in routes:raise ValueError('电阻板序号重复')
            routes[global_channel]=(board,local)
    return routes


def select_channels(text, available=range(1,37)):
    available=set(available)
    text=str(text).strip().upper().replace('，',',').replace('；',',').replace(';',',')
    modes={'所有':'all','全部':'all','ALL':'all','所有奇数':'odd','奇数':'odd','ODD':'odd',
           '所有偶数':'even','偶数':'even','EVEN':'even'}
    mode=modes.get(text)
    if mode:
        selected=sorted(ch for ch in available if mode=='all' or ch%2==(1 if mode=='odd' else 0))
    else:
        selected=[]
        for part in text.split(','):
            match=re.fullmatch(r'\s*(\d+)(?:\s*-\s*(\d+))?\s*',part)
            if not match:raise ValueError('通道请填写1～36、1,7,13、7-12、所有、所有奇数或所有偶数')
            first=int(match[1]);last=int(match[2] or first)
            if not 1<=first<=last<=36:raise ValueError('电阻全局通道范围为1～36，范围必须从小到大')
            selected.extend(range(first,last+1))
        if len(selected)!=len(set(selected)):raise ValueError('电阻通道不能重复')
        if any(ch not in available for ch in selected):raise ValueError('指定电阻通道未配置或未启用')
        selected.sort()
    if not selected:raise ValueError('没有符合条件的已启用电阻通道')
    return selected


def execute_selection(manager, selection, resistance, read, cancelled, logger):
    routes=manager.resistor_routes()
    channels=select_channels(selection,routes)
    result=_operate_channels(routes,channels,'read' if read else 'set',resistance,
                             cancelled,logger,manager.device_step_lock)
    if cancelled():raise RuntimeError('测试已停止')
    if not result['ok']:
        raise RuntimeError('电阻板批量操作失败：'+'；'.join(
            f'CH{ch}: {error}' for ch,error in result['errors'].items()))
    return result['values']


def operate_channels(boards,channels,operation,resistance=0):
    return _operate_channels(channel_routes(boards),list(channels),operation,resistance)


def _operate_channels(routes,channels,operation,resistance=0,
                      cancelled=lambda:False,logger=None,lock_for=None):
    if not channels:raise ValueError('没有选中有效通道')
    if len(channels)!=len(set(channels)) or any(ch not in routes for ch in channels):
        raise ValueError('所选通道未配置或重复')
    if operation not in ('read','set','disconnect'):raise ValueError('操作无效')
    if operation=='set' and (type(resistance) is not int or not 0<=resistance<=1500000 or resistance%10):
        raise ValueError('阻值需为10Ω整数倍，范围0–1,500,000Ω')
    groups={}
    for channel in channels:
        board,local=routes[channel]
        groups.setdefault(id(board),(board,[]))[1].append((channel,local))
    started=time.perf_counter()
    def execute(group):
        board,selected=group;values={};errors={}
        try:
            key=board.setting.get('key',f'resistor{board.setting["number"]}')
            with lock_for(key) if lock_for else nullcontext():
                if cancelled():raise RuntimeError('测试已停止')
                if operation=='disconnect':
                    board.disconnect();return values,errors
                if not board.is_connected and not board.connect():raise ConnectionError(board.last_error)
                if cancelled():raise RuntimeError('测试已停止')
                local=[ch for _,ch in selected]
                if operation=='set':
                    actual=({local[0]:control_board(board,'resistance',channel=local[0],resistance=resistance,cancelled=cancelled)}
                            if len(local)==1 else
                            control_board(board,'resistances',channels=local,resistance=resistance,cancelled=cancelled))
                elif len(local)==1:
                    actual={local[0]:board.read_resistance(local[0])}
                else:
                    readings=board.read_resistances()['resistances']
                    if len(readings)!=6:raise ValueError('批量阻值回读通道不完整')
                    actual={ch:readings[ch-1] for ch in local}
                if cancelled():raise RuntimeError('测试已停止')
                for channel,ch in selected:
                    value=finite_reading(actual[ch])
                    if value<0:raise ValueError('电阻回读值不能为负数')
                    values[channel]=actual[ch]
                if logger:
                    for channel,ch in selected:
                        logger(f'电阻CH{channel} -> 序号{board.setting["number"]} / 本板CH{ch}: {values[channel]} Ω')
        except Exception as exc:
            return values,{ch:str(exc) for ch,_ in selected}
        return values,errors
    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        results=list(pool.map(execute,groups.values()))
    values={ch:value for row,_ in results for ch,value in row.items()}
    errors={ch:error for _,row in results for ch,error in row.items()}
    return {'ok':not errors,'values':values,'errors':errors,'elapsed':time.perf_counter()-started}
