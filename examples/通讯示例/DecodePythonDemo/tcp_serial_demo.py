'''
Copyright (C) 2016, GDK Corporation.
Author: cx
Date: 2022-08-17 13:38:55
LastEditTime: 2024-09-04 12:28:01
LastEditors: xjfjian
Version: V1.0.0
Python Version: 3.10
Description: 使用python通过tcp或者串口和设备通讯示例，代码仅供演示，实际应用需完善异常处理
FilePath: /DecodePythonDemo/main.py
'''
from socket import *
from time import sleep
import os

# serial 模块需pip安装
import serial



remoteip=['192.168.0.162',4096]



# TCP客户端连接扫码设备demo
def decode_demo(): 
    '''
    解码示例
    ''' 
    # 建立TCP连接
    client = new_tcpconnect(addr=remoteip[0], port=remoteip[1])
    # 解码设置，获取解码结果支持串口
    # client = new_serialconnect('COM2',115200)
    sleep(0.01)
 
    # 发送解码触发指令
    sendcmd(client,'TON')
    # 发送16进制形式指令
    # client.send(bytes.fromhex('544F4E'))
    # 接收解码数据，超时时间 0.3*10s
    # 如果使用外部触发，可以不限时间不断监听接收
    result = try_receive(client,interval = 0.1).decode('gbk')
    print(f'decode result: {result}')
    sendcmd(client,'TOFF')
    client.close()
   
def romupdate_demo(filepath:str)->bool:
  '''
  固件更新，演示用，实际需要对文件进行验证再进行更新
  '''
  client = new_tcpconnect(addr=remoteip[0], port=remoteip[1])
  filename = os.path.basename(filepath)
  with open(filepath, 'rb') as f:
    data = f.read()
    firmware_len = len(data)
   
  sendcmd(client, cmd = 'ROM0000', tag = "D")
  try_receive(client)
  sendcmd(client, cmd = f'STOR\r\n{filename}\r\n{firmware_len}\r\n', tag = '')
  result = try_receive(client).decode('utf-8')
  print(result)
  if result is not  None and result.startswith('150'):
    client.sendall(data)
    icount=20
    while True:
      received = try_receive(client)
      if len(received) > 0:
        result=received.decode('utf-8')
        print(result)
        if result.startswith('226'):
          result = try_receive(client,interval=0.2).decode('utf-8')
          print(result)
          print('固件传输完成，请耐心等待设备重启，设备更新完成之前请勿断开电源')          
          return True
        else:
          return False
      else:
        sleep(1)
        icount=icount-1
        if icount < 1:
          print('time out')
          return False
  else:
    return False


def new_tcpconnect(addr:str, port:int,*, blocking :bool= False)->socket: 
  client = socket(AF_INET,SOCK_STREAM)
  client.connect((addr, port))
  client.setblocking(blocking)
  return client

def new_serialconnect(portname:str='COM1', baudrate:int=115200)->serial.Serial: 
  client=serial.Serial(portname,baudrate)
  return client



def send_setcmd(client:socket|serial.Serial,fulltag:str='TAGXXX',value:str='',waittime:float=0.01,islegal:bool=True) -> bool:
  '''
  发送设置指令  
  ```
      # 设置方式1，128码开
      assert send_setcmd(client,'128EAN','1')
  ```
  '''
  sendcmd(client = client, cmd = f"{fulltag}{value}", tag = 'M')  
  sleep(waittime)  
  recv = try_receive(client)
  start= 0x06 if islegal else 0x15
  if len(recv) < 3:
    return False 
  if recv[0]!=start or recv[-1]!=0x0d:
    return False
  if recv[1]>0x84:
    return False
  taglength = 2
  length= recv[1] + taglength
  if recv[1]>=0x81 and recv[0] <=0x84:
    taglength= recv[1] - 0x7e
    length = int.from_bytes(recv[2:taglength],'big') + taglength
  if length != len(recv):
    return False
  if islegal:
    result = recv[taglength+1:-1].decode('charmap')
    return result==f'M{fulltag}{value}'
  else:
    return True

def sendcmd(client:socket|serial.Serial, cmd:str, tag:str = "R")->bool:
  '''
description: 发送指令
Date: 2022-08-25 11:23:08
LastEditors: dujianping
### 参数说明
- cmd:指令内容
- tag:指令类型，R/M/C/G
### 示例代码
```
  sendcmd(client,'RAWIMG','C')
```
  '''
  if cmd  not in {"TON", "TOFF"} and len(tag) == 1:
    cmd = __createcmd(cmd,tag)
  try:
    if isinstance(client,socket):
      client.send(cmd.encode('charmap'))
    elif isinstance(client,serial.Serial):
      client.write(cmd.encode('charmap'))
    else:
      return False
    return True
  except (Exception, ) as e:
    print(e)
    return False

def __createcmd(cmd:str, tag:str = "R")->str:
  length=len(cmd)+1
  if length <=127 and length > 2 :
    cmd=f'\x02{tag}\x0d{chr(length)}{cmd}\x03'
    return cmd
  elif length<=65535 and length>127:
    cmd=f'\x02{tag}\x0d\x82{chr(length>>8)}{chr(length%256)}{cmd}\x03'
  elif length>65535:
    lengthstr= int.to_bytes(length,4, 'big').decode('charmap')
    cmd=f'\x02{tag}\x0d\x84{lengthstr}{cmd}\x03'
    return cmd
  else:
    return cmd  

def try_receive(client:socket|serial.Serial, *,interval :float = 0.02, retry_count:int = 10,count:int = -1) -> bytes | None:
  '''
description: tcp或者串口接收数据
Date: 2022-08-25 11:23:08
LastEditors: dujianping
### 示例代码
```
  receive = try_receive(client)
  assert len(recv) > 2 and recv[0]==0x06 and recv[-1]==0x0d
```
```
  receive = try_receive(client,interval=0.1,retry_count=16)
```
  '''
  if isinstance(client,socket):
    return __tcp_try_receive(client,interval,retry_count,count)
  elif isinstance(client,serial.Serial):
    return __serial_try_receive(client,interval,retry_count,count)
  else:
    return None


def __tcp_try_receive(client:socket,interval :float = 0.02, retry_count:int = 10,count:int = -1) -> bytes:
  received = b''
  buffersize = 1024 if count > 1024 or count < 1 else count
  while True:
    try:
      val = client.recv(buffersize)
      received += val
      if count > 0:
        receivecount=len(received)
        if receivecount>=count:
          break
        if count-receivecount<1024:
          buffersize=count-receivecount      
    except (Exception):
      sleep(interval)
      retry_count -= 1
      if retry_count <= 0:
        break
  return received

def __serial_try_receive(client:serial.Serial,interval :float = 0.02, retry_count:int = 10,count:int = -1) -> bytes:
  received = b''
  buffersize = 1024 if count > 1024 or count < 1 else count
  while True:
    try:
      if client.in_waiting>0: 
        if client.in_waiting>buffersize:
          val = client.read(buffersize)
          received += val
        else:
          val = client.read_all()
          received += val
      else:
        sleep(interval)
        retry_count -= 1
        if retry_count <= 0:
          break

      if count > 0:
        receivecount=len(received)
        if receivecount>=count:
          break
        if count-receivecount<1024:
          buffersize=count-receivecount      
    except (Exception):
      break     
  return received



def load_configs(client:socket|serial.Serial)->list[dict[str,str]]|None:
  '''
description: 读取设备配置文件
Date: 2022-08-25 11:23:08
LastEditors: dujianping
### 示例代码
``` python
    cfgs = load_configs(client)  
    name = get_configsvalue(cfgs,'INFTYP')  
    assert name is not None and name.startswith('FV') 
``` 
  '''
  sendcmd(client, cmd = "SFL", tag = 'G')
  cfg_content = ''
  receive=b''
  if isinstance(client,socket):
    receive=try_receive(client)
  elif isinstance(client,serial.Serial):
    receive=try_receive(client,interval=0.1,retry_count=16)
  else:
    return None
  if receive[0]!=0x06:
    return None
  elif receive[1]==0x82:
    cfg_content=receive[6,].decode('gbk').strip()
  else:
    cfg_content=receive[1,].decode('gbk').strip()
  lines = cfg_content.split('\n')
  lines = [x.strip().split(',') for x in lines]
  lines = [x for x in lines if len(x) > 6]
  headers = lines[0]
  cfgs = [dict(zip(headers, line)) for line in lines[1:]]
  return cfgs
    
def get_configsvalue(cfgs:list[dict[str,str]],fulltag:str="TAGXXX",type:str="Value")->str|None:
  '''
description: 从配置文件中获取某项的值
Date: 2022-08-25 11:24:53
LastEditors: dujianping
### 参数说明
- cfgs:配置列表  
- fulltag:指令全称  
- type:列名称  
### 示例代码
``` python
    name = get_configsvalue(cfgs,'INFTYP')  
    assert name is not None and name.startswith('FV')  
```
  '''
  
  for cfg in cfgs:
    if(fulltag==cfg['FullTag']):
      return cfg[type].strip()
  return None

if '__main__' == __name__:
    # 取消对应注释测试demo功能，固件更新需修改路径为自己实际路径
    # 建议每次只运行一个示例
    decode_demo()

    # 固件更新仅支持网络传输
    # romupdate_demo('D:\RomFiles\FV104Update_V3.34T6_20220815.tar.gz')
