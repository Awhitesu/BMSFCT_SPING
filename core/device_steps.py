"""Typed device steps shared by recipe editor and sequence execution."""
import csv
import json
import math
import sys
import time
import threading
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from core.measurement_units import MeasurementValue

PREFIX='设备工步:'
LEGACY_PREFIX='DEVICE_STEP:'


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    default: object
    minimum: float = -1e12
    maximum: float = 1e12
    choices: tuple = ()


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    fields: tuple = ()
    read: bool = False
    help: str = ''


def number(key,label,value,minimum=0,maximum=1e9):
    return Field(key,label,float(value),minimum,maximum)


def integer(key,label,value,minimum=1,maximum=144):
    return Field(key,label,int(value),minimum,maximum)


def choice(key,label,options):
    return Field(key,label,options[0][1],choices=tuple(options))


CH2=integer('channel','通道（1–2）',1,1,2)
CH3=integer('channel','通道（1–3）',1,1,3)
CHANNEL=choice('source','信号通道',[('CH1','CH1'),('CH2','CH2')])
TRIGGER_SOURCE=choice('source','触发来源',[('CH1','CH1'),('CH2','CH2'),('电源线（仅边沿）','LINE')])
OUTPUT=choice('state','输出状态',[('保持现状','KEEP'),('开启','ON'),('关闭','OFF')])
CONNECT=Action('connect','连接检测',help='使用硬件管理中心保存的地址，不更改输出。')
VOLTAGE=number('voltage','电压 (V)',0,0,1000)
CURRENT=number('current','限流 (A)',.1,0,500)
VARIABLE=Field('variable','保存到变量（可留空）','')

DEVICES={
    'it6333b':('直流源','IT6333B 三通道电源'),
    'dmm34461':('测量仪器','34461A 万用表'),
    'dg2052':('信号发生器','DG2052 双通道信号发生器'),
    'tbs2102b':('示波器','TBS2102B 示波器'),
    'insulation':('板卡','绝缘板（单通道）'),
    'resistor':('板卡','电阻板（六通道）'),
    'relay':('板卡','Relay Card 继电器板'),
    'hv':('板卡','HV Card 高压板'),
    'hsd':('板卡','HSD Card 高边板'),
    'dmm':('板卡','DMM Card 切换板'),
    'simulator':('模拟电池','电池模拟器 (NGI 83624A Simulator)'),
    'dut_power':('直流源','IT6722A 被测物供电电源 (IT6722A DUT Power)'),
    'hv_source':('高压源','NGI 高压源 (HV Source)'),
    'easy320':('继电器','Easy320 继电器 (Easy320)'),
    'ca550':('校准源','CA550 校准仪 (CA550)'),
}

from devices.keysight_34461a import Keysight34461A
from devices.rigol_dg2052 import DG2052
from devices.rn_board import BOARD_SPECS

ACTIONS={
    'it6333b':(CONNECT,
        Action('configure','设置通道参数',(CH3,number('voltage','电压 (V)',0,0,60),number('current','限流 (A)',.1,0,3),OUTPUT),help='CH1/CH2最高60V，CH3最高5V；设置后回读校验。'),
        Action('on','开启通道输出',(CH3,)),Action('off','关闭通道输出',(CH3,)),
        Action('voltage','读取通道电压',(CH3,VARIABLE),True),
        Action('current','读取通道电流',(CH3,VARIABLE),True),
        Action('state','读取通道输出状态',(CH3,VARIABLE),True)),
    'dmm34461':(CONNECT,Action('measure','单次测量',(
        choice('function','测量功能',[(k,k) for k in Keysight34461A.FUNCTIONS]),
        Field('range','量程（AUTO 或正数）','AUTO'),VARIABLE),True,
        '电压V、电流A、电阻Ω、频率Hz、周期s；返回数值供测试项上下限判定。')),
    'dg2052':(CONNECT,Action('configure','设置通道波形',(
        CH2,choice('waveform','波形',[(k,k) for k in DG2052.WAVEFORMS]),
        number('frequency','频率 (Hz)',1000,1e-6,50e6),
        number('amplitude','幅值 (Vpp)',1,1e-6,20),
        number('offset','偏置 (V)',0,-10,10),
        choice('load','负载',[('高阻','INF'),('50Ω','50')]),OUTPUT)),
        Action('on','开启通道输出',(CH2,)),Action('off','关闭通道输出',(CH2,)),
        Action('frequency','回读频率',(CH2,VARIABLE),True),
        Action('amplitude','回读幅值',(CH2,VARIABLE),True),
        Action('offset','回读偏置',(CH2,VARIABLE),True),
        Action('state','回读输出状态',(CH2,VARIABLE),True)),
    'tbs2102b':(CONNECT,
        Action('channel','设置通道',(CHANNEL,
            choice('enabled','通道显示',[('开启','ON'),('关闭','OFF')]),
            choice('coupling','通道耦合',[('DC','DC'),('AC','AC')]),
            number('probe','探头倍率',10,1,1000),
            number('scale','垂直刻度 (V/div)',1,.001,100),
            number('position','垂直位置 (div)',0,-5,5),
            choice('bandwidth','带宽',[('全带宽','FULL'),('20MHz','TWENTY')]),
            choice('invert','反相',[('关闭','OFF'),('开启','ON')]))),
        Action('acquisition','设置时基与采集',(
            number('timescale','时基 (s/div)',.001,2e-9,100),
            choice('mode','采集模式',[('采样','SAMPLE'),('峰值检测','PEAKDETECT'),('平均','AVERAGE')]),
            choice('average','平均次数',[(str(n),str(n)) for n in (2,4,8,16,32,64,128,256,512)]))),
        Action('trigger','设置触发',(
            choice('kind','触发类型',[('边沿','EDGE'),('脉宽','WIDTH'),('欠幅','RUNT')]),TRIGGER_SOURCE,
            choice('mode','触发模式',[('自动','AUTO'),('正常','NORMAL')]),
            number('level','触发电平 (V)',1.65,-1000,1000),
            choice('slope','边沿',[('上升','RISE'),('下降','FALL')]),
            choice('coupling','触发耦合',[(x,x) for x in ('DC','HFREJ','LFREJ','NOISEREJ')]),
            number('holdoff','释抑时间 (s)',20e-9,20e-9,8),
            choice('polarity','脉冲极性',[('正','POSITIVE'),('负','NEGATIVE')]),
            choice('when','脉冲条件',[(x,x) for x in ('LESSTHAN','MORETHAN','EQUAL','UNEQUAL','OCCURS')]),
            number('width','脉宽 (s)',10e-6,1e-9,10),
            number('lower','欠幅下阈值 (V)',.8,-1000,1000),
            number('upper','欠幅上阈值 (V)',2,-1000,1000),
            number('tolerance','请求/回读允许偏差 (V)',.02,0,1000)),
            help='仅设置对应触发类型的字段；自动回读实际电平，超过允许偏差判失败。欠幅需要下阈值小于上阈值。'),
        Action('level','读取实际触发电平',(CHANNEL,VARIABLE),True),
        Action('run','连续采集'),Action('stop','停止采集'),
        Action('single','单次捕获并等待',(
            number('timeout','等待触发超时 (s)',10,.1,600),),help='等待采集结束；超时停止采集并判失败。'),
        Action('measure','读取即时测量',(
            CHANNEL,choice('measurement','测量项',[(label,value) for label,value in (
                ('频率','FREQUENCY'),('周期','PERIOD'),('峰峰值','PK2PK'),('均值','MEAN'),
                ('RMS','RMS'),('上升时间','RISE'),('下降时间','FALL'))]),VARIABLE),True),
        Action('waveform','读取波形并保存CSV',(CHANNEL,),
            help='自动暂停、读取并恢复；CSV保存到logs/sequence_waveforms，日志记录路径。'),
    ),
}

for kind,label,count,channels in BOARD_SPECS:
    board=integer('board','本类板卡编号',1,1,count)
    channel=integer('channel','本板通道',1,1,channels)
    if kind in ('resistor','insulation'):
        resistance=integer('resistance','阻值 (Ω)',1000,0,1500000 if kind=='resistor' else 30000000)
        actions=[CONNECT,Action('resistance','设置阻值',(board,channel,resistance)),
                 Action('read_resistance','读取设定阻值',(board,channel,VARIABLE),True,
                        '回读板卡保存的设定值，不是万用表实测值。')]
        if kind=='insulation':
            actions += [Action('on','开启绝缘输出',(board,resistance)),
                        Action('off','关闭绝缘输出',(board,)),
                        Action('state','读取绝缘输出状态',(board,VARIABLE),True)]
    else:
        actions=[CONNECT,Action('on','吸合指定继电器',(board,channel)),
                 Action('off','释放指定继电器',(board,channel)),
                 Action('all_on','全部吸合',(board,)),Action('all_off','全部释放',(board,)),
                 Action('state','读取指定继电器状态',(board,channel,VARIABLE),True,
                        '回读控制状态；不是触点实际电气反馈。')]
    actions[0]=Action('connect','连接检测',(board,),help='使用本类板卡调试页保存的板号、IP、端口及Unit ID；不更改输出。')
    if kind == 'resistor':
        global_channel = integer('global_channel','全局通道（0使用原板号）',0,0,36)
        actions = [Action(a.key,a.label,a.fields+(global_channel,) if a.key in ('resistance','read_resistance') else a.fields,a.read,a.help) for a in actions]
    ACTIONS[kind]=tuple(actions)

for device in ('dut_power','hv_source'):
    ACTIONS[device]=(CONNECT,Action('on','开启输出（独立）'),Action('off','关闭输出（独立）'))
ACTIONS['easy320']=(CONNECT,Action('state','读取指定继电器状态',(
    integer('channel','继电器通道',1,1,32),VARIABLE),True))
ACTIONS['ca550']=(CONNECT,)
ACTIONS['simulator']=(Action('connect','连接检测'),
    Action('on','开启指定通道（电压校验）',(integer('channel','全局通道',1),)),
    Action('off','关闭指定通道',(integer('channel','全局通道',1),)),
    Action('all_on','开启整套设备（电压校验）'),Action('all_off','关闭整套设备'),
    Action('range','设置指定通道量程',(integer('channel','全局通道',1),
        choice('range','量程',[(x,x) for x in ('AUTO','HIGH','LOW')])),
        help='切换量程会短暂关闭并恢复原先开启的通道。LOW前先将限流设为不超过1 mA；原先关闭的通道保持关闭。'),
    Action('current_limit','设置指定通道限流',(integer('channel','全局通道',1),CURRENT)),
    Action('voltage','读取指定通道电压',(integer('channel','全局通道',1),VARIABLE),True),
    Action('current','读取指定通道电流',(integer('channel','全局通道',1),VARIABLE),True))


def device_key(label):
    return next((key for key,(_,name) in DEVICES.items() if name==label),None)


def action_spec(device,label):
    return next((a for a in ACTIONS.get(device,()) if a.label==label),None)


def validate(device,action,values):
    if device not in ACTIONS:raise ValueError('未知设备测试步')
    spec=next((a for a in ACTIONS[device] if a.key==action),None)
    if not spec:raise ValueError('未知设备动作')
    if not isinstance(values,dict):raise ValueError('设备参数格式错误')
    unknown=set(values)-{f.key for f in spec.fields}
    if unknown:raise ValueError('未知参数：'+','.join(sorted(unknown)))
    result={}
    for field in spec.fields:
        value=values.get(field.key,field.default)
        if field.choices:
            if value not in [v for _,v in field.choices]:raise ValueError(f'{field.label}选项无效')
        elif isinstance(field.default,(float,int)):
            if isinstance(value,bool):raise ValueError(f'{field.label}不能为布尔值')
            value=float(value)
            if not math.isfinite(value) or not field.minimum<=value<=field.maximum:
                raise ValueError(f'{field.label}超出范围')
            if isinstance(field.default,int):
                if not value.is_integer():raise ValueError(f'{field.label}必须为整数')
                value=int(value)
        else:
            if not isinstance(value,str):raise ValueError(f'{field.label}必须为文本')
            value=value.strip()
        result[field.key]=value
    if device=='it6333b' and action=='configure' and result['channel']==3 and result['voltage']>5:
        raise ValueError('IT6333B CH3 电压上限5V')
    if device=='tbs2102b' and action=='trigger':
        if result['source']=='LINE' and result['kind']!='EDGE':raise ValueError('LINE来源仅用于边沿触发')
        if result['kind']=='RUNT' and result['lower']>=result['upper']:raise ValueError('欠幅下阈值必须小于上阈值')
        if result['kind']=='WIDTH' and result['when']=='OCCURS':raise ValueError('OCCURS仅适用于欠幅触发')
    if device in ('resistor','insulation') and 'resistance' in result:
        step=10 if device=='resistor' else 1000
        if result['resistance']%step:raise ValueError(f'阻值必须为{step}Ω整数倍')
        if action=='on' and result['resistance']==0:raise ValueError('绝缘输出开启阻值必须大于0Ω')
    return spec,result


def encode(device,action,values):
    spec,values=validate(device,action,values)
    readable={f.label:values[f.key] for f in spec.fields}
    return PREFIX+json.dumps({'schema_version':2,'device_id':device,'action_id':spec.key,'values':values,'设备':DEVICES[device][1],'动作':spec.label,'参数':readable},ensure_ascii=False,separators=(',',':'))


def is_device_step(text):
    return str(text).startswith((PREFIX,LEGACY_PREFIX))


def decode(text):
    if not is_device_step(text):return None
    if str(text).startswith(LEGACY_PREFIX):
        data=json.loads(str(text)[len(LEGACY_PREFIX):])
        if data.get('version')!=1:raise ValueError('设备测试步版本不支持')
        spec,values=validate(data['device'],data['action'],data['values'])
        return data['device'],spec,values
    data=json.loads(str(text)[len(PREFIX):])
    if data.get('schema_version') == 2:
        spec,values=validate(data['device_id'],data['action_id'],data['values'])
        return data['device_id'],spec,values
    key=device_key(data['设备']);spec=action_spec(key,data['动作'])
    if not spec:raise ValueError('设备或动作不支持')
    readable=data['参数'];labels={f.label:f.key for f in spec.fields}
    if not isinstance(readable,dict) or set(readable)-set(labels):raise ValueError('设备参数不支持')
    spec,values=validate(key,spec.key,{labels[name]:value for name,value in readable.items()})
    return key,spec,values


def checked(result,driver):
    if result is False or result is None:
        raise RuntimeError(getattr(driver,'last_error','') or '设备未确认操作成功')
    return result


def measurement(value,unit):
    if value is None or isinstance(value,bool):
        raise ValueError('测量数据缺失或类型无效')
    value=float(value)
    if not math.isfinite(value) or abs(value)>=9e37:raise ValueError('测量超量程或无效')
    return MeasurementValue(value,unit)


def execute(manager,text,variables,logger,cancelled=lambda:False):
    _ensure_active(cancelled)
    device,spec,p=decode(text)
    action=spec.key
    if getattr(manager,'is_virtual',False) is True:
        result=_virtual_result(device,spec,p)
        if p.get('variable'):variables[p['variable']]=result
        logger(f'[仿真，无硬件通信] {DEVICES[device][1]} / {spec.label} => {result}')
        return True,result
    # The manager owns drivers and their RLocks, shared across sequence workers.
    if device=='simulator':
        if action=='connect':
            sims={id(sim):sim for _,sim,_ in manager.simulator_channels()}
            if not sims:raise ValueError('没有启用的电池模拟器')
            for sim in sims.values():
                with sim._lock:
                    if not sim.is_connected:checked(sim.connect(),sim)
                    reply=sim._query('*IDN?',logger)
                    if not reply:raise ConnectionError('电池模拟器身份查询无响应')
            result='PASS'
        elif action in ('all_on','all_off'):
            checked(manager.broadcast_output(action=='all_on',logger,cancelled=cancelled),manager);result='PASS'
        else:
            sim,ch=manager._get_sim_and_ch(p['channel'])
            if not sim:raise ValueError('全局通道不存在或模拟器未启用')
            if action in ('on','off','range','current_limit'):
                from core.simulator_control import control_simulator
                operation={'on':'output','off':'output','range':'range','current_limit':'current'}[action]
                value=(action=='on') if operation=='output' else p['range'] if operation=='range' else p['current']
                control_simulator(sim,operation,[ch],value,logger=logger,cancelled=cancelled);result='PASS'
            else:
                from core.simulator_control import read_simulator
                value=read_simulator(sim,action,ch,logger,cancelled)
                if value<0 and (action=='voltage' or not callable(getattr(type(sim),'read_stable',None))):raise ValueError('模拟器测量失败')
                result=measurement(value,'V' if action=='voltage' else 'mA')
    else:
        if device in {kind for kind,_,_,_ in BOARD_SPECS}:
            if device == 'resistor' and p.get('global_channel',0):
                route = manager.resistor_routes().get(p['global_channel'])
                if not route: raise ValueError('全局电阻通道未配置或未启用')
                driver,local_channel = route
                p = dict(p,channel=local_channel)
            else:
                driver=manager.get_rn_board(f"{device}{p['board']}")
        else:
            enable_key={'dut_power':'dut_pwr','hv_source':'hv'}.get(device,device)
            if not manager.is_enabled(enable_key):raise ValueError('设备未启用，请在硬件管理中心启用')
            driver=getattr(manager,device,None)
            if driver is None:raise ValueError('设备驱动未初始化')
        lock=getattr(driver,'_lock',None)
        # Older drivers use a non-reentrant lock internally or have no lock.
        # Never acquire their internal Lock around connect/query calls.
        internal=lock if isinstance(lock,type(threading.RLock())) else nullcontext()
        with manager.device_step_lock(device+str(p.get('board',''))),internal:
            _ensure_active(cancelled)
            if not driver.is_connected:checked(driver.connect(),driver)
            _ensure_active(cancelled)
            if action=='connect':
                _check_connection(driver,device,logger);result='PASS'
            elif device=='it6333b':result=_supply(driver,action,p,cancelled)
            elif device=='dmm34461':
                value,unit=driver.measure(p['function'],p['range'] or 'AUTO')
                result=measurement(value,unit)
            elif device=='dg2052':result=_generator(driver,action,p,logger,cancelled)
            elif device=='tbs2102b':result=_scope(driver,action,p,logger,variables,cancelled)
            elif device in ('dut_power','hv_source'):
                from core.instrument_control import control_power
                control_power(driver, 'output_control', action == 'on', logger=logger, cancelled=cancelled)
                result='PASS'
            elif device=='easy320':
                states=driver.read_relays(32)
                if len(states)<p['channel']:raise ValueError('继电器状态回读不完整')
                result=measurement(int(states[p['channel']-1]),'')
            else:result=_board(driver,action,p,cancelled)
    if p.get('variable'):variables[p['variable']]=result
    logger(f'{DEVICES[device][1]} / {spec.label} => {result} {getattr(result,"unit","")}')
    return True,result


def _ensure_active(cancelled):
    if cancelled():
        raise RuntimeError('测试已停止，未执行后续设备操作')


def _configure_output(driver, configure, output, state, cancelled):
    try:
        _ensure_active(cancelled)
        configure()
        _ensure_active(cancelled)
    except Exception as original:
        # An explicitly requested OFF still runs when configuration fails or
        # cancellation arrives. An ON must never follow either failure.
        if state == 'OFF':
            try:
                checked(output(False), driver)
            except Exception as stop_error:
                raise RuntimeError(f'参数设置失败：{original}；关闭输出也未确认：{stop_error}') from original
        raise
    if state != 'KEEP':
        checked(output(state == 'ON'), driver)
    return 'PASS'


def _supply(driver,action,p,cancelled=lambda:False):
    from core.instrument_control import control_power
    ch=p['channel']
    def control(action, value):
        return control_power(driver, action, value, channel=ch, cancelled=cancelled)
    if action=='configure':
        def configure():
            control('set_current',p['current'])
            _ensure_active(cancelled)
            control('set_voltage',p['voltage'])
        return _configure_output(driver,configure,lambda state:control('output_control',state),p['state'],cancelled)
    if action in ('on','off'):
        control('output_control',action=='on');return 'PASS'
    voltage,current,state=driver.read_channel(ch)
    return measurement({'voltage':voltage,'current':current,'state':int(state)}[action],
                       {'voltage':'V','current':'A','state':''}[action])


def _check_connection(driver,device,logger):
    if device in {kind for kind,_,_,_ in BOARD_SPECS}:
        reply=driver.read_registers(0x100,5)
        if len(reply)!=5:raise ConnectionError('板卡身份回读不完整')
    elif device=='easy320':
        reply=driver.read_relays(32)
        if len(reply)!=32:raise ConnectionError('EASY320回读不完整')
    elif device=='hv_source':
        checked(driver.check_success(logger),driver);reply='通信及错误队列查询成功'
    elif device=='ca550':reply=driver.get_idn()
    elif device=='tbs2102b':reply=driver.command('*IDN?')
    else:reply=driver.query('*IDN?')
    if not reply or (isinstance(reply,str) and reply.upper().startswith('ERROR')):
        raise ConnectionError('设备查询无响应或报错')
    logger(f'连接检测实际回读：{reply}')


def _generator(driver,action,p,logger,cancelled=lambda:False):
    from core.instrument_control import control_generator
    ch=p['channel']
    def output(state):
        return control_generator(driver,'output',ch,state=state,cancelled=cancelled)
    if action=='configure':
        def configure():
            actual=control_generator(driver,'configure',ch,
                parameters=(p['waveform'],p['frequency'],p['amplitude'],p['offset'],p['load']),cancelled=cancelled)
            logger('信号发生器参数回读：'+json.dumps(actual,ensure_ascii=False))
        return _configure_output(driver,configure,output,p['state'],cancelled)
    if action in ('on','off'):
        output(action=='on');return 'PASS'
    commands={'frequency':(f':SOUR{ch}:FREQ?','Hz'),
              'amplitude':(f':SOUR{ch}:VOLT?','Vpp'),
              'offset':(f':SOUR{ch}:VOLT:OFFS?','V'),
              'state':(f':OUTP{ch}?','')}
    cmd,unit=commands[action];reply=driver.query(cmd)
    if action=='amplitude':
        reported=driver.query(f':SOUR{ch}:VOLT:UNIT?').strip().upper()
        unit={'VPP':'Vpp','VRMS':'Vrms','DBM':'dBm'}.get(reported,reported)
    if action=='state':
        if reply.upper() not in ('0','1','OFF','ON'):raise ValueError('输出状态回读无效')
        reply=int(reply.upper() in ('1','ON'))
    return measurement(reply,unit)


def _board(driver,action,p,cancelled=lambda:False):
    from core.board_control import control_board
    ch=p.get('channel',1)
    if action=='resistance':control_board(driver,'resistance',channel=ch,resistance=p['resistance'],cancelled=cancelled);return 'PASS'
    if action=='read_resistance':return measurement(driver.read_resistance(ch),'Ω')
    if driver.setting['kind']=='insulation':
        if action=='state':return measurement(int(driver.read_insulation()['output']),'')
        control_board(driver,'insulation',state=action=='on',resistance=p.get('resistance',0),cancelled=cancelled);return 'PASS'
    if action=='state':return measurement(int(driver.read_coils()[ch-1]),'')
    if action in ('all_on','all_off'):control_board(driver,'all_relays',state=action=='all_on',cancelled=cancelled)
    else:control_board(driver,'relay',channel=ch,state=action=='on',cancelled=cancelled)
    return 'PASS'


def _scope(driver,action,p,logger,variables,cancelled):
    source=p.get('source','CH1')
    if action=='channel':
        driver.apply([f'SELECT:{source} {p["enabled"]}',f'{source}:COUPLING {p["coupling"]}',
            f'{source}:PROBE:GAIN {1/p["probe"]:.12g}',f'{source}:SCALE {p["scale"]:.12g}',
            f'{source}:POSITION {p["position"]:.12g}',f'{source}:BANDWIDTH {p["bandwidth"]}',
            f'{source}:INVERT {p["invert"]}']);return 'PASS'
    if action=='acquisition':
        driver.apply([f'HORIZONTAL:SCALE {p["timescale"]:.12g}',f'ACQ:MODE {p["mode"]}',
                      f'ACQ:NUMAVG {p["average"]}']);return 'PASS'
    if action=='trigger':
        report=driver.trigger(p['kind'],source,p['mode'],p['level'],p['slope'],p['coupling'],
            p['holdoff'],p['polarity'],p['when'],p['width'],p['lower'],p['upper'])
        logger('触发请求/实际回读：'+json.dumps(report,ensure_ascii=False))
        for key,target in report['requested'].items():
            actual=float(measurement(report['actual'][key],'V'))
            variables[f'SCOPE_{source}_{key.upper()}']=actual
            if abs(actual-target)>p['tolerance']+1e-12:
                raise ValueError(f'触发{key}请求{target:g}V，实际{actual:g}V，超过允许偏差{p["tolerance"]:g}V')
        return 'PASS'
    if action=='level':return measurement(driver.command(f'TRIG:A:LEVEL:{source}?'),'V')
    if action in ('run','stop'):
        driver.apply(['ACQ:STOPAFTER RUNSTOP','ACQ:STATE ON'] if action=='run' else ['ACQ:STATE OFF'])
        return 'PASS'
    if action=='single':
        driver.apply(['ACQ:STATE OFF','ACQ:STOPAFTER SEQUENCE','ACQ:STATE ON'])
        deadline=time.monotonic()+p['timeout']
        try:
            while True:
                if cancelled():raise RuntimeError('测试已停止')
                if driver.command('ACQ:STATE?').upper() in ('0','OFF'):return 'PASS'
                if time.monotonic()>=deadline:raise TimeoutError('等待示波器单次触发超时')
                time.sleep(.1)
        except Exception as original:
            try:
                driver.command('ACQ:STATE OFF')
            except Exception as stop_error:
                raise RuntimeError(f'{original}；停止采集也失败：{stop_error}') from original
            raise
    if action=='measure':
        driver.apply([f'MEASUREMENT:IMMED:SOURCE1 {source}',f'MEASUREMENT:IMMED:TYPE {p["measurement"]}'])
        return measurement(driver.command('MEASUREMENT:IMMED:VALUE?'),driver.command('MEASUREMENT:IMMED:UNITS?').strip('"'))
    points=driver.waveform(source)
    root=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    directory=root/'logs'/'sequence_waveforms';directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'{source}_{datetime.now():%Y%m%d_%H%M%S_%f}.csv'
    with path.open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.writer(stream);writer.writerow(['time_s','voltage_V']);writer.writerows(points)
    variables[f'SCOPE_{source}_WAVEFORM_FILE']=str(path)
    variables[f'SCOPE_{source}_WAVEFORM_POINTS']=len(points)
    logger(f'波形{len(points)}点，CSV：{path}')
    return 'PASS'


def _virtual_result(device,spec,p):
    """Explicit dry-run values; never instantiate or connect real drivers."""
    if not spec.read:return 'PASS'
    if device=='dmm34461':
        return measurement(0,Keysight34461A.FUNCTIONS[p['function']][1])
    if device=='simulator' and spec.key=='current':return measurement(0,'mA')
    units={'voltage':'V','current':'A','read_resistance':'Ω','frequency':'Hz',
           'amplitude':'Vpp','offset':'V','level':'V','state':''}
    if device=='tbs2102b' and spec.key=='measure':
        unit={'FREQUENCY':'Hz','PERIOD':'s','RISE':'s','FALL':'s'}.get(p['measurement'],'V')
    else:unit=units.get(spec.key,'')
    return measurement(0,unit)
