"""RN boards: protocols in doc/板卡协议, zero-based Modbus addresses.

Insulation follows IR_MODBUS_TCP_README.md (single channel, 5M or 30M).
"""
import socket
import struct
import threading
import time

BOARD_SPECS = [('insulation', '绝缘板', 2, 1), ('resistor', '电阻板', 6, 6),
               ('relay', 'Relay Card', 1, 12), ('hv', 'HV Card', 1, 10),
               ('hsd', 'HSD Card', 2, 15), ('dmm', 'DMM Card', 2, 18)]


def board_settings(cfg):
    saved = cfg.get('rn_boards', {})
    result = []
    for kind, label, count, channels in BOARD_SPECS:
        for number in range(1, count+1):
            key = f'{kind}{number}'
            values = saved.get(key, {})
            sequence=int(values.get('number',number)) if kind=='resistor' else number
            if kind=='resistor' and not 1<=sequence<=6:raise ValueError('电阻板序号必须为1–6')
            limit=1500000 if kind=='resistor' else int(values.get('limit', 5000000 if kind=='insulation' else 0))
            if kind=='insulation' and limit not in (5000000,30000000): limit=5000000
            result.append(dict(key=key, kind=kind, name=f'{label} {number}', number=sequence, channels=channels,
                               ip=values.get('ip', ''), port=int(values.get('port', 502)),
                               unit=int(values.get('unit', 1)), enabled=bool(values.get('enabled', False)),
                               limit=limit))
    return result


class RNBoard:
    def __init__(self, setting):
        self.setting = setting
        self.ip, self.port = setting['ip'], setting['port']
        self.sock = None
        self.is_connected = False
        self.last_error = ''
        self.info = []
        self._transaction = 0
        self._lock = threading.RLock()

    def disconnect(self):
        with self._lock:
            if self.sock:
                self.sock.close()
            self.sock = None
            self.is_connected = False

    def connect(self):
        with self._lock:
            self.disconnect()
            try:
                self.sock = socket.create_connection((self.ip,self.port),3)
                self.sock.settimeout(3)
                self.is_connected = True
                self.info = self.read_registers(0x100,5)
                expected = 0x5254 if self.setting['kind'] in ('resistor','insulation') else 0x5243
                # Resistance firmware uses 0x103 as an enable mask, not a channel count.
                if self.info[1] != expected or (self.setting['kind'] not in ('resistor','insulation') and self.info[3] != self.setting['channels']):
                    raise ValueError(f'板卡类型或通道数量不匹配: ID=0x{self.info[1]:04X}, 通道={self.info[3]}')
                self.last_error = ''
                return True
            except Exception as exc:
                self.last_error = str(exc)
                self.disconnect()
                return False

    def _recv(self, count):
        data=b''
        while len(data)<count:
            chunk=self.sock.recv(count-len(data))
            if not chunk: raise ConnectionError('板卡关闭连接')
            data+=chunk
        return data

    def request(self, function, payload):
        with self._lock:
            if not self.is_connected:
                raise ConnectionError('板卡未连接')
            self._transaction=(self._transaction+1)&0xffff
            unit=self.setting['unit']
            frame=struct.pack('>HHHB',self._transaction,0,len(payload)+2,unit)+bytes([function])+payload
            try:
                self.sock.sendall(frame)
                tid,protocol,length,response_unit=struct.unpack('>HHHB',self._recv(7))
                if tid!=self._transaction or protocol!=0 or response_unit!=unit or not 2<=length<=254:
                    raise ValueError('Modbus响应头不匹配')
                pdu=self._recv(length-1)
                if pdu[0]==function|0x80:
                    raise ValueError(f'Modbus异常码 0x{pdu[1]:02X}')
                if pdu[0]!=function: raise ValueError('Modbus功能码不匹配')
                return pdu[1:]
            except Exception:
                self.disconnect()
                raise

    def read_registers(self, address, count):
        if not 1<=count<=125: raise ValueError('寄存器数量1–125')
        data=self.request(3,struct.pack('>HH',address,count))
        if len(data)!=1+count*2 or data[0]!=count*2: raise ValueError('寄存器响应长度不一致')
        return list(struct.unpack('>'+str(count)+'H',data[1:]))

    def read_coils(self):
        count=self.setting['channels']
        data=self.request(1,struct.pack('>HH',0,count))
        if len(data)!=1+(count+7)//8 or data[0]!=(count+7)//8: raise ValueError('线圈响应长度不一致')
        return [bool(data[1+i//8] & (1<<(i%8))) for i in range(count)]

    def set_relay(self, channel, state):
        if self.setting['kind'] in ('resistor','insulation') or not 1<=channel<=self.setting['channels']:
            raise ValueError('继电器通道错误')
        payload=struct.pack('>HH',channel-1,0xff00 if state else 0)
        with self._lock:
            if self.request(5,payload)!=payload: raise ValueError('继电器写入响应不一致')
            states=self.read_coils()
            if states[channel-1]!=state: raise ValueError('继电器状态回读不一致')
            return states

    def all_relays(self, state):
        if self.setting['kind'] in ('resistor','insulation'): raise ValueError('不是继电器板')
        count=self.setting['channels']
        values=bytes(sum((1<<(i%8)) for i in range(byte*8,min(count,byte*8+8))) if state else 0
                     for byte in range((count+7)//8))
        head=struct.pack('>HH',0,count)
        with self._lock:
            if self.request(15,head+bytes([len(values)])+values)!=head: raise ValueError('批量写入响应不一致')
            states=self.read_coils()
            if any(value!=state for value in states): raise ValueError('批量回读不一致')
            return states

    def _resistance_address(self, channel):
        if self.setting['kind'] not in ('resistor','insulation') or not 1<=channel<=self.setting['channels']:
            raise ValueError('电阻通道错误')
        return (channel-1)*2

    def read_resistance(self, channel=1):
        registers=self.read_registers(self._resistance_address(channel),2)
        return (registers[0]<<16)|registers[1]

    def read_resistances(self):
        self._resistance_address(1)
        registers=self.read_registers(0,self.setting['channels']*2)
        return {'resistances':[(registers[i]<<16)|registers[i+1] for i in range(0,len(registers),2)]}

    def set_resistance(self, value, channel=1):
        address=self._resistance_address(channel)
        limit=self.setting['limit']
        if self.setting['kind'] not in ('resistor','insulation') or not limit:
            raise ValueError('请先确认电阻协议及量程')
        step=10 if self.setting['kind']=='resistor' else 1000
        if value<0 or value>limit or value%step:
            raise ValueError(f'阻值需为{step}Ω整数倍，范围0–{limit}Ω')
        head=struct.pack('>HH',address,2)
        with self._lock:
            payload=head+b'\x04'+struct.pack('>HH',value>>16,value&0xffff)
            if self.request(16,payload)!=head: raise ValueError('阻值写入响应不一致')
            time.sleep(.1)
            actual=self.read_resistance(channel)
            if actual!=value: raise ValueError(f'阻值回读不一致: 设定{value}Ω / 回读{actual}Ω')
            return actual

    def set_resistance_output(self, enabled, resistance):
        """IR protocol: write zero to disconnect; nonzero to restore output."""
        if self.setting['kind']!='insulation' or not self.setting['limit']:
            raise ValueError('请先确认单通道绝缘板的电阻固件及量程')
        if enabled and resistance<=0: raise ValueError('开启输出需设置大于0Ω的阻值')
        with self._lock:
            self.set_resistance(resistance if enabled else 0)
            output=bool(self.read_registers(0x104,1)[0]&1)
            if output!=enabled: raise ValueError('输出状态回读不一致')
            return {'resistance':resistance if enabled else 0,'output':output}

    def read_insulation(self):
        if self.setting['kind']!='insulation': raise ValueError('不是绝缘板')
        with self._lock:
            return {'resistance':self.read_resistance(),'output':bool(self.read_registers(0x104,1)[0]&1)}
