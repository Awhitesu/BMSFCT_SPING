using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Data;
using System.Drawing;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace CommunicationSimpleDemo
{
    public partial class Form1 : Form
    {
        public Form1()
        {
            InitializeComponent();

           
        }

        #region Common
        private Encoding encoding = Encoding.Default;
        

        private void LogInfo(string message)
        {
            Invoke(new Action(() =>
            {
                richTextBox2.AppendText($"[{DateTime.Now:HH:mm:ss fff}] {message}\n");
                richTextBox2.ScrollToCaret();
            }));
        }

        private void WriteMessage(string message)
        {
            richTextBox1.AppendText($"[{DateTime.Now:HH:mm:ss fff}] {message}\n");
            richTextBox1.ScrollToCaret();
        }
        #endregion

        #region SerialPort Demo

        private bool isPortOpen = false;
        private void button1_Click(object sender, EventArgs e)
        {
            if(!isPortOpen)
            {
                try
                {
                    //TODO 根据实际场景自行修改参数（Modify parameters according to production environment）
                    //部分串口初始化代码在  form1.designer.cs 中

                    serialPort1.BaudRate = 115200;
                    serialPort1.DtrEnable = true;
                    serialPort1.PortName = "COM2";
                    serialPort1.ReadBufferSize = 64 * 1024;
                    serialPort1.Open();
                    isPortOpen = true;
                    button1.Text = "Close Serial Port";
                    LogInfo(serialPort1.PortName+" Open Success");
                }
                catch(Exception ex)
                {
                    LogInfo("Com Open Error:" + ex.Message);
                }

            }else
            {
                try
                {
                    LogInfo(serialPort1.PortName + " Close");
                    serialPort1.Close();
                    isPortOpen = false;
                    button1.Text = "Open Serial Port";
                }catch
                {
                  
                }
            }
        }


        private void serialPort1_DataReceived(object sender, System.IO.Ports.SerialDataReceivedEventArgs e)
        {
            byte[] buffer = new byte[serialPort1.BytesToRead];
            int len=serialPort1.Read(buffer, 0, buffer.Length);
            string result=encoding.GetString(buffer, 0, len);


            Invoke(new Action<string>((x) =>
            {
                WriteMessage(x);
            }), $"{serialPort1.PortName} Receive {len}:\n{result}");
            

        }
        #endregion

        #region TCP Client Demo
        //设备默认网络模式
        //Default Mode

        private bool isTcpConnected = false;
        private TcpClient tcpClient;

        private void button2_Click(object sender, EventArgs e)
        {
            if(!isTcpConnected)
            {
                try
                {
                    //TODO 根据实际场景自行修改参数（Modify parameters according to production environment）
                    tcpClient = new TcpClient();
                    
                    tcpClient.Connect("192.168.0.100", 4096);
                    isTcpConnected = true;
                    button2.Text = "Tcp Client Disconnect";
                    LogInfo($"{tcpClient.Client.LocalEndPoint} Connect To {tcpClient.Client.RemoteEndPoint} Success");
                    Thread tcpClientReceive = new Thread(TcpClientReceive) { IsBackground = true, Name = "TcpClientReceive" };
                    tcpClientReceive.Start();

                }
                catch(Exception ex)
                {
                    LogInfo("Tcp Client Connet Error:" +ex.Message);
                }
            }
            else
            {
                isTcpConnected=false;
                button2.Text = "Tcp Client Connect";
            }

        }

        private void TcpClientReceive()
        {
            try
            {
                byte[] buffer = new byte[1024];
                while (isTcpConnected)
                {

                    // TODO: 可以设置tcpClient.ReceiveTimeout，默认无限
                    // 超时时触发异常，此时在异常中可以写代码判断网络连接状态，进行重连报警等操作
                    // tcpClient.ReceiveTimeout default:0-unlimited
                    // When timeout occurs, an exception is triggered.
                    // In this case, you can write code to judge the network connection status and perform reconnection alarm and other operations
                    int len = tcpClient.GetStream().Read(buffer, 0, buffer.Length);
                    if (len == 0)
                    {

                        LogInfo("Remote Tcp Server Close Connection");
                        break;
                    }
                    else
                    {
                        
                        string result = Utils.GetString(buffer, 0, len);
                        Invoke(new Action<string>((x) =>
                        {
                            WriteMessage(x);
                        }), $"Tcp Client Receive From {tcpClient.Client.RemoteEndPoint}:\n{result}");

                        

                    }
                }
            }
            catch (Exception e)
            {
                LogInfo(e.Message);
            }
            finally
            {
                LogInfo("Tcp Disconnect");
                isTcpConnected = false;
                tcpClient.Close();
                Invoke(new Action(() => {
                    button2.Text = "Tcp Client Connect";
                }));
               
            }
        }
        #endregion


        #region TCP Server Demo
        //需要开启设备TCP 服务器功能
        //Device Need Enable Tcp Server 
        private bool isListening = false;

        private TcpListener tcpListener;
        private TcpClient tcpAClient;
        bool IsAConnected=false;
        private void button3_Click(object sender, EventArgs e)
        {
            
            if(!isListening)
            {
                try
                {
                    LogInfo("Tcp Listener Starting");

                    //TODO 根据实际场景自行修改参数（Modify parameters according to production environment）
                    tcpListener = new TcpListener(IPAddress.Any, 4096);
                    tcpListener.Start();

                    Thread tcpListenThread = new Thread(TcpListen) { IsBackground = true, Name = "TcpListen" };
                    tcpListenThread.Start();
                    isListening = true;
                    button3.Text = "Stop TCP Server Listen";
                    

                }
                catch (Exception ex)
                {

                    LogInfo("Tcp Listen Error:"+ex.Message);
                }

            }
            else
            {
                try
                {
                    LogInfo("Tcp Listener Stoping");
                    tcpListener.Stop();
                    isListening = false;
                    button3.Text = "Start TCP Server Listen";
                }catch
                {

                }
            }
        }

        private void TcpListen()
        {
            try
            {
                while (true)
                {
                    LogInfo("Tcp Listener Started.Wait For Connection...");
                    TcpClient client = tcpListener.AcceptTcpClient();
                    if (IsAConnected)
                    {
                        //TODO 这里是单连接示例，替换已连接tcp，如果是允许多连接，可以建立新的socket
                        //(Here is a single connection example. Replace the connected TCP. If multiple connections are allowed, a new socket can be established)
                        LogInfo("Close Current Connection...");
                        tcpAClient?.Close();

                    }

                    tcpAClient = client;
                    IsAConnected = true;

                    Thread tcpAReceive = new Thread(TcpAReceive) { IsBackground = true, Name = "TcpAReceive" };
                    tcpAReceive.Start();


                    LogInfo($"Accept Connection From {tcpAClient.Client.RemoteEndPoint}");
                }
            }
            catch (Exception e)
            {
                LogInfo("TcpListen Stop|" + e.Message);
                isListening = false;
                Invoke(new Action(() => {
                 
                    button3.Text = "Start TCP Server Listen";
                }));
            }

        }

        private void TcpAReceive()
        {
            try
            {
                byte[] buffer = new byte[1024];
                while (IsAConnected)
                {
                    
                    int len = tcpAClient.Client.Receive(buffer);
                    if (len == 0)
                    {

                        LogInfo("Remote Client Close Connection");
                        break;
                    }
                    else
                    {
                        string result = Utils.GetString(buffer, 0, len);
                        Invoke(new Action<string>((x) =>
                        {
                            WriteMessage(x);
                        }), $"Tcp Server Receive From {tcpAClient.Client.RemoteEndPoint}:\n{result}");
                    }
                }
            }
            catch (Exception e)
            {
                LogInfo(e.Message);
            }
            finally
            {
                LogInfo("TcpA Disconnect");
                IsAConnected = false;
                tcpAClient.Close();
                
            }
        }

        #endregion

        #region Data Send Demo
        private void button4_Click(object sender, EventArgs e)
        {
            //TODO 可以咨询技术指定功能的指令，在需要时自行发送
            //(You can consult the instructions of technical designated functions and send them yourself when necessary)
            try
            {
                if (string.IsNullOrEmpty(textBox1.Text.Trim()))
                    return;

                byte[] buffer = encoding.GetBytes(textBox1.Text.Trim());

                SendBytes(buffer);
            }
            catch (Exception ex)
            {
                LogInfo("Send Error:" + ex.Message);
            }
        }

        private void SendBytes(byte[] buffer)
        {
            if (isPortOpen)
            {

                serialPort1.Write(buffer, 0, buffer.Length);
                WriteMessage("Serial Send: " + Utils.BytesToHex(buffer));
            }

            if (isTcpConnected)
            {
                tcpClient.GetStream().Write(buffer, 0, buffer.Length);
                WriteMessage("Tcp Client Send: " + Utils.BytesToHex(buffer));
            }

            if (IsAConnected)
            {
                tcpAClient.GetStream().Write(buffer, 0, buffer.Length);
                WriteMessage("Tcp Server Send: " + Utils.BytesToHex(buffer));
            }
        }

        #endregion

        #region Command Send Demo
        private void button5_Click(object sender, EventArgs e)
        {
            try
            {
                //查询所有设备支持的设置 Query the settings supported by all devices
                byte[] buffer = Utils.HexToBytes("02470D53464C03");
                SendBytes(buffer);
            }catch(Exception ex)
            {
                LogInfo("Send Command Error:" + ex.Message);
            }
        }


        private void button6_1_Click(object sender, EventArgs e)
        {
            try
            {   //cmdtype="ip"
                //fulltag:"TRAIPA",value:"192.168.0.100",cmdtype="ip"
                //var valuebytes = IPAddress.Parse("192.168.0.100").GetAddressBytes();
                //byte[] buffer = Utils.CreateCmd("TRAIPA", Utils.GetString(valuebytes, 0, valuebytes.Length));

                //cmdtype="ip"
                //fulltag:"ETHSIP",value:"192.168.0.100",cmdtype="ip"
                byte[] buffer = Utils.CreateCmd("ETHSIP", "192.168.0.100");
            }
            catch (Exception ex)
            {
                LogInfo("Send Command Error:" + ex.Message);
            }
        }

        private void button6_2_Click(object sender, EventArgs e)
        {
            try
            {   //cmdtype="hexstring"
                //fulltag:"EDTFCN",value:"0x4E52",cmdtype="hexstring"
                var valuebytes = Utils.HexToBytes("0x4E52".Replace("0x", ""));
                byte[] buffer = Utils.CreateCmd("TRAIPA", Utils.GetString(valuebytes, 0, valuebytes.Length));
            }
            catch (Exception ex)
            {
                LogInfo("Send Command Error:" + ex.Message);
            }
        }

        private void button6_Click(object sender, EventArgs e)
        {
            try
            {
                //cmdtype="bool"||"value"||"string"||"reference"
                //fulltag:"IOPBEE",value:"1",fulltagen:"beeper",ValueRefKeyEn:"1:on|0:off"
                //设置蜂鸣器开 set beeper ON
                byte[] buffer = Utils.CreateCmd("IOPBEE", "1");
                SendBytes(buffer);
            }
            catch (Exception ex)
            {
                LogInfo("Send Command Error:" + ex.Message);
            }
        }

        private void button7_Click(object sender, EventArgs e)
        {
            try
            {
                //查询蜂鸣器的状态 Read Beeper Value
                byte[] buffer = Utils.CreateCmd("IOPBEE", "","R");
                SendBytes(buffer);
            }
            catch (Exception ex)
            {
                LogInfo("Send Command Error:" + ex.Message);
            }
        }


        private void button8_Click(object sender, EventArgs e)
        {
            try
            {
                //恢复设备出厂设置后，需要重新建立连接（After resetting the device, it is necessary to establish a new connection）
                byte[] buffer = Utils.CreateCmd("PRMRST", "", "C");
                SendBytes(buffer);
            }
            catch (Exception ex)
            {
                LogInfo("Send Command Error:" + ex.Message);
            }
        }

        #endregion

        private void richTextBox1_TextChanged(object sender, EventArgs e)
        {

        }

        private void richTextBox2_TextChanged(object sender, EventArgs e)
        {

        }

        private void textBox1_TextChanged(object sender, EventArgs e)
        {

        }
    }
}
