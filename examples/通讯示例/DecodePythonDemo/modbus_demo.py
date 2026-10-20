'''
Copyright (C) 2016, GDK Corporation.
Author: xjfjian
Date: 2025-04-14 17:05:10
LastEditTime: 2025-04-15 10:33:49
LastEditors: dujianping
Version: V1.0.0
Description: 使用modbus tcp和设备通讯获取解码结果示例
'''
import time
# 首次使用需要`pip install pymodbus`安装依赖包 | First use requires' pip install pymodbus' to install dependency packages
from pymodbus.client import ModbusTcpClient
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian
# 定义Modbus服务器的IP和端口 | Define the IP address and port of the Modbus server
ip_address = '192.168.0.100'
port = 502
# 创建Modbus TCP客户端 | Create a Modbus TCP client
client = ModbusTcpClient(ip_address, port)
# 连接到Modbus服务器 | Connect to the Modbus server
if client.connect():
    try:
        while True: #指令循环触发，如果使用IO控制设备触发，可以仅周期性调用read_holding_registers读取解码结果 | Instruction loop trigger, if using IO control devices trigger, can only periodically call read_holding_registers to read the decoding result
            # 1. 清空上一次触发数据 | Clear the previous trigger data
            client.write_register(0x00, 0b100)
            # 2. 控制设备触发 | Control device trigger
            client.write_register(0x00, 0b10)
            # 3. 读数据, 长度102 | Read data, length 102
            icount = 0
            while icount < 10:
                time.sleep(0.1)  # 轮询间隔 | Polling interval
                result = client.read_holding_registers(0x00, 102)
                if result.isError():
                    print("读取寄存器时出错:", result)
                    break
                else:
                    # 输出状态标志位结果，详情见modbus 内存映射表 | Output the status flag result, see modbus memory mapping table
                    print("解码状态:", bin(result.registers[0]))
                    # 获取条码内容长度 | Get the length of the barcode content
                    barcodelength = (result.registers[1] >> 8) & 0xFF
                    print("解码结果长度:", barcodelength)
                    # 解析条码内容，如果是非ascii码，需要修改转换方法 | Parse the barcode content, if it is not ascii code, you need to modify the conversion method
                    if barcodelength > 0:
                        decoder = BinaryPayloadDecoder.fromRegisters(result.registers[2:])
                        resultstr=decoder.decode_string(barcodelength)
                        print("解码结果内容:", resultstr)
                        break
                    else:     
                        icount += 1
                        print(f"第{icount}次查询没有解码结果")
                   
    except KeyboardInterrupt:
        print("程序被用户中断")
    finally:
        # 断开连接 | Disconnect
        client.close()
else:
    print("无法连接到Modbus服务器")