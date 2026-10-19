"""Generate documented simulation-only DBC/recipe examples, never select hardware."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.dbc_signals import import_dbc,catalog,encode_step,validate_recipe_dbc
from core.read_until import attach

DBC='''VERSION "BMSFCT simulation example - NOT a production protocol"
NS_ :
BS_:
BU_: BMS Tester
BO_ 848 BMS_Status: 8 BMS
 SG_ PackVoltage : 0|16@1+ (0.1,0) [0|6553.5] "V" Tester
 SG_ PackCurrent : 16|16@1- (0.1,0) [-3276.8|3276.7] "A" Tester
 SG_ SOC : 32|8@1+ (0.4,0) [0|100] "%" Tester
 SG_ BmsState : 40|8@1+ (1,0) [0|3] "" Tester
 SG_ FaultCount : 48|8@1+ (1,0) [0|255] "" Tester
 SG_ AliveCounter : 56|8@1+ (1,0) [0|255] "" Tester
BO_ 849 BMS_CellStats: 8 BMS
 SG_ MinCellVoltage : 0|16@1+ (0.001,0) [0|6] "V" Tester
 SG_ MaxCellVoltage : 16|16@1+ (0.001,0) [0|6] "V" Tester
 SG_ MaxTemperature : 32|8@1- (1,0) [-128|127] "degC" Tester
 SG_ MinTemperature : 40|8@1- (1,0) [-128|127] "degC" Tester
VAL_ 848 BmsState 0 "Sleep" 1 "Ready" 2 "Run" 3 "Fault";
CM_ "SIMULATION ONLY. Replace signal definitions and test limits with approved project values.";
'''


def create(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    dbc_path=folder/'BMS_DBC_仿真示例.dbc';dbc_path.write_text(DBC,encoding='utf-8')
    asset=import_dbc(dbc_path);signals=catalog(asset);items=[]
    def item(name,signal,lower,upper,unit='',wait=False,output='value',variable=''):
        row=next(r for r in signals if r['signal']==signal)
        config=dict(row,sha256=asset['sha256'],output=output,variable=variable,timeout=3,interval=.05,max_age=.5,new_frame=True)
        params=encode_step(config)+' / PCH:1'
        if wait:params=attach(params,dict(min=float(lower),max=float(upper),timeout=5,interval=.05,consecutive=2))
        items.append(dict(name=name,min=str(lower),max=str(upper),standard_type='字符串' if output=='choice' else '数值',
            retry_count='不复测',strategy='任何NG停止',unit=unit or 'NULL',sub_steps=[dict(name='读取 '+row['label'],
            type='CANDBC交互',device='CANDBC 交互',action='读取DBC信号',params=params,is_judgment=True,fail_strategy='失败停止')]))
    item('等待BMS就绪','BmsState',1,1,wait=True)
    item('BMS总电压','PackVoltage',340,380,'V',wait=True,variable='BMS_PackVoltage')
    item('静态总电流','PackCurrent',-1,1,'A',wait=True,variable='BMS_PackCurrent')
    item('SOC范围','SOC',20,100,'%',variable='BMS_SOC')
    item('最高单体电压','MaxCellVoltage',2.5,4.2,'V',variable='BMS_MaxCell')
    item('最低单体电压','MinCellVoltage',2.5,4.2,'V',variable='BMS_MinCell')
    item('最高温度','MaxTemperature',-20,60,'degC')
    item('无故障','FaultCount',0,0,wait=True)
    item('状态文字','BmsState','Ready','--',output='choice')
    items.append(dict(name='单体压差',min='0',max='0.1',unit='V',standard_type='数值',retry_count='不复测',strategy='任何NG停止',
        sub_steps=[dict(name='最高单体减最低单体',type='公式计算',device='公式计算 (Calculate)',action='公式计算',
            params='FORMULA:BMS_MaxCell - BMS_MinCell',is_judgment=True,fail_strategy='失败停止')]))
    recipe=dict(name='DBC_仿真示例_请勿直接用于生产',project_no='DBC-DEMO',model_name='Simulation only',
        recipe_number=0,sn_regex='^DBC_DEMO_[0-9]+$',
        can_config=dict(channel='PCAN_USBBUS1',mode='经典 CAN',nominal_bitrate=500000,data_bitrate=2000000),
        dbc=asset,items=items,example_only=True)
    validate_recipe_dbc(recipe)
    recipe_path=folder/'DBC_仿真测试序列.json';recipe_path.write_text(json.dumps(recipe,ensure_ascii=False,indent=2),encoding='utf-8')
    return recipe,recipe_path,dbc_path


if __name__=='__main__':
    _,recipe,dbc=create(Path(__file__).resolve().parents[1]/'doc'/'DBC示例')
    print(recipe);print(dbc)
