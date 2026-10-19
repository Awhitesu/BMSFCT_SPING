"""Named CAN message lists scoped to one test execution."""
import base64
import json
import re
import threading
import time

DEVICE='CAN 周期报文列表'
PREFIX='CANPER64:'


def validate(c):
    if not isinstance(c,dict) or c.get('operation') not in ('start','stop'):raise ValueError('周期报文操作无效')
    if not isinstance(c.get('name'),str) or not c['name'].strip() or len(c['name'])>80:raise ValueError('请填写报文列表名称（1～80字符）')
    if c['name']!=c['name'].strip():raise ValueError('列表名称首尾不能有空格')
    if c['operation']=='stop':return c
    rows=c.get('frames')
    if not isinstance(rows,list) or not 1<=len(rows)<=100:raise ValueError('启动列表需要1～100条报文')
    for i,row in enumerate(rows,1):
        if not isinstance(row,dict):raise ValueError(f'第{i}条报文格式错误')
        if type(row.get('id')) is not int or not 0<=row['id']<=0x1FFFFFFF:raise ValueError(f'第{i}条报文ID无效')
        if type(row.get('type')) is not int or row['type'] not in (0,1,2):raise ValueError('CAN类型无效')
        if type(row.get('period_ms')) is not int or not 10<=row['period_ms']<=3600000:raise ValueError('发送周期应为10～3600000 ms')
        try:data=bytes.fromhex(row['data'])
        except (ValueError,TypeError,KeyError):raise ValueError(f'第{i}条数据必须是完整HEX字节')
        allowed=range(9) if row['type']==0 else (0,1,2,3,4,5,6,7,8,12,16,20,24,32,48,64)
        if len(data) not in allowed:raise ValueError(f'第{i}条数据长度不符合CAN/CAN FD帧长度')
    return c


def encode(c):
    validate(c)
    return PREFIX+base64.urlsafe_b64encode(json.dumps(c,ensure_ascii=False).encode()).decode()


def decode(text):
    match=re.search(r'(?:^|\s/\s)CANPER64:([^\s]+)',str(text))
    if not match:return None
    return validate(json.loads(base64.urlsafe_b64decode(match[1]).decode()))


def validate_plan(recipe,debug=False,active_names=()):
    """Check the actual enabled order, including debug-selected snapshots."""
    active=set(active_names)
    items=recipe.get('items',[]) if isinstance(recipe,dict) else recipe
    for item in items:
        if item.get('skip_runtime') or str(item.get('name','')).strip().startswith('#'):continue
        for step in item.get('sub_steps',[]):
            if step.get('skip_runtime') or str(step.get('name','')).strip().startswith('#'):continue
            c=decode(step.get('params',''))
            if c is None:continue
            name=c['name']
            if c['operation']=='start':
                if name in active:raise ValueError(f'周期报文列表【{name}】重复启动，请先停止')
                if len(active)>=16:raise ValueError('同时运行的报文列表不能超过16个')
                active.add(name)
            else:
                if name not in active:raise ValueError(f'周期报文列表【{name}】未在前面启动，不能停止')
                active.remove(name)
    if active and not debug:raise ValueError('以下周期报文列表缺少后续停止工步：'+ '、'.join(sorted(active)))


class PeriodicLists:
    def __init__(self,cancelled,logger):
        self.cancelled=cancelled;self.log=logger;self.groups={}

    def active_names(self):
        """Only live senders on their original connection reserve a name."""
        return [name for name, g in self.groups.items()
                if not g['stop'].is_set() and not g['error']
                and g['thread'] is not None and g['thread'].is_alive()
                and g['driver'].is_connected and g['driver'].bus is g['bus']
                and g['driver'].channel == g['interface']]

    def discard_inactive(self):
        active = set(self.active_names())
        for name in list(self.groups):
            if name in active:
                continue
            group = self.groups[name]
            group['stop'].set()
            group['thread'].join(timeout=2)
            if group['thread'].is_alive():
                raise RuntimeError(f'周期报文列表【{name}】旧任务尚未退出，禁止重复启动')
            del self.groups[name]
            self.log(f'[周期CAN已失效] {name}: {group["error"] or "连接变化或任务已结束"}；需要重新执行启动工步')

    def assert_uds_ids_available(self, driver, *ids):
        for name in self.active_names():
            group = self.groups[name]
            if group['driver'] is driver:
                conflicts = set(ids).intersection(group['ids'])
                if conflicts:
                    text = ', '.join(f'0x{i:X}' for i in sorted(conflicts))
                    raise ValueError(f'周期报文列表【{name}】占用了UDS请求/响应ID {text}，请修改周期列表，禁止混发诊断帧')

    def check(self):
        for name,group in self.groups.items():
            if group['error']:raise RuntimeError(f'周期报文列表【{name}】发送失败：'+group['error'])

    def start(self,c,driver,channel):
        validate(c);self.discard_inactive();self.check();name=c['name']
        if name in self.groups:raise ValueError(f'周期报文列表【{name}】已启动')
        if len(self.groups)>=16:raise ValueError('同时运行的报文列表不能超过16个')
        if self.cancelled():raise RuntimeError('测试已停止，不能启动周期发送')
        if not driver.is_connected:raise ValueError('CAN未连接，请先初始化')
        from devices.pcan_fd_driver import PCANFDDriver
        frames=[dict(row,data_bytes=bytes.fromhex(row['data'])) for row in c['frames']]
        bus=driver.bus;interface=driver.channel
        group=dict(stop=threading.Event(),error='',sent=0,thread=None,
                   driver=driver,bus=bus,interface=interface,ids={row['id'] for row in frames})
        def send(row):
            if self.cancelled() or group['stop'].is_set():return False
            if not driver.is_connected or driver.bus is not bus or driver.channel!=interface:raise RuntimeError('CAN连接或配方接口已变化')
            if row['type'] and not driver.fd_enabled:raise ValueError('当前配方为经典CAN，不能发送CAN FD')
            if not driver.send_can_message(channel,row['id'],row['type'],PCANFDDriver.length_to_dlc(len(row['data_bytes'])),row['data_bytes']):
                raise RuntimeError(driver.last_error or 'CAN发送返回失败')
            group['sent']+=1
            return True
        # Validate every row before the first bus write.
        if any(row['type'] for row in frames) and not driver.fd_enabled:raise ValueError('当前配方为经典CAN，不能发送CAN FD')
        try:
            for row in frames:
                if not send(row):raise RuntimeError('周期发送已取消')
        except Exception:
            group['stop'].set();raise
        def run():
            deadlines=[time.monotonic()+row['period_ms']/1000 for row in frames]
            try:
                while not self.cancelled() and not group['stop'].is_set():
                    now=time.monotonic()
                    for i,row in enumerate(frames):
                        if now>=deadlines[i]:
                            if not send(row):return
                            # Skip missed ticks instead of flooding a bus after a delay.
                            deadlines[i]=time.monotonic()+row['period_ms']/1000
                    group['stop'].wait(min(.05,max(0,min(deadlines)-time.monotonic())))
            except Exception as exc:
                group['error']=str(exc);group['stop'].set();self.log(f'[周期CAN ERROR] {name}: {exc}')
        self.groups[name]=group
        group['thread']=threading.Thread(target=run,name='PeriodicCAN-'+name,daemon=True)
        try:group['thread'].start()
        except Exception:self.groups.pop(name);group['stop'].set();raise
        self.log(f'[周期CAN启动] {name}，{len(frames)}条，参数={json.dumps(c,ensure_ascii=False)}')

    def stop(self,name):
        if name not in self.groups:raise ValueError(f'周期报文列表【{name}】未启动，不能停止')
        group=self.groups[name];group['stop'].set();group['thread'].join(timeout=2)
        if group['thread'].is_alive():raise RuntimeError(f'周期报文列表【{name}】停止超时')
        del self.groups[name]
        self.log(f'[周期CAN停止] {name}，已发送{group["sent"]}帧')
        if group['error']:raise RuntimeError(group['error'])

    def close(self):
        errors=[]
        for group in self.groups.values():group['stop'].set()
        for name in list(self.groups):
            try:self.stop(name)
            except Exception as exc:errors.append(str(exc))
        return errors
