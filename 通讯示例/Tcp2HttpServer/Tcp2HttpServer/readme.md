## 说明
http server,前端可以调用指定api控制设备解码并获取解码数据  
需要以管理员权限打开

## 使用
1. 确认设备和电脑连接正常
1. `program.cs` 中修改默认设备ip地址
1. 允许程序
1. 浏览器输入`http://127.0.0.1:4000/api/home/EnumDevice` 获取设备信息
1. 浏览器控制设备进行触发并获取解码结果，` http://127.0.0.1:4000/api/home/SendAndReadResult?address=0&cmd=TON`
1. 更多功能见`homecontroller.cs` 