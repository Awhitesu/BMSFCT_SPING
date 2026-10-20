using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Data;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Ports;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace DecodeAndGetImageDemo
{
    public partial class Form1 : Form
    {
        public Form1()
        {
            InitializeComponent();

            pictureBox1.SizeMode=PictureBoxSizeMode.Zoom;
        }

        #region Common
        private Encoding encoding = Encoding.Default;


        private void LogInfo(string message)
        {
            Invoke(new Action(() =>
            {
                richTextBox1.AppendText($"[{DateTime.Now:HH:mm:ss fff}]  Info> {message}\n");
                richTextBox1.ScrollToCaret();
            }));
        }

        private void WriteMessage(string message)
        {
            richTextBox1.AppendText($"[{DateTime.Now:HH:mm:ss fff}] {message}\n");
            richTextBox1.ScrollToCaret();
        }
        #endregion

        private bool isTcpConnected = false;
        private TcpClient tcpClient;

        private void button2_Click(object sender, EventArgs e)
        {
            if (!isTcpConnected)
            {
                try
                {
                    //TODO 根据实际场景自行修改参数（Modify parameters according to production environment）
                    tcpClient = new TcpClient();

                    tcpClient.Connect("192.168.0.100", 4096);
                    Thread.Sleep(200);
                    
                    isTcpConnected = true;
                    button2.Text = "Disconnect";
                    LogInfo($"{tcpClient.Client.LocalEndPoint} Connect To {tcpClient.Client.RemoteEndPoint} Success");
                    Thread tcpClientReceive = new Thread(TcpClientReceive) { IsBackground = true, Name = "TcpClientReceive" };
                    tcpClientReceive.Start();
                    StartUdpServer();

                    
                    //Remote control, with only one remote control terminal allowed at a time
                    //Only decoding without remote control, multiple connections can exist simultaneously
                    SendBytes(Utils.CreateCmd("REMON", "9000;12345", "C"));


                }
                catch (Exception ex)
                {
                    LogInfo("Tcp Client Connet Error:" + ex.Message);
                }
            }
            else
            {
                isTcpConnected = false;
                StopUdpServer();
                button2.Text = "Connect";
            }
        }

        private UdpClient udplistener = null;
        private bool isudplistening = false;

        private void StartUdpServer()
        {
            if(isudplistening)
            {
                return;
            }

            try
            {


                udplistener = new UdpClient(9000);
                udplistener.Client.ReceiveBufferSize = 10 * 1024 * 1024;
                isudplistening = true;
            }
            catch (SocketException se)
            {
                LogInfo("Udp Server Start Error:" + se.Message);
                return;
            }

            new Thread(UdpListener)
            {
                Name = "udp server",
                IsBackground = true
            }.Start();
        }

        private void StopUdpServer()
        {
            if (isudplistening)
            {
                try
                {

                    isudplistening = false;
                    udplistener.Close();
                }
                catch (Exception ex)
                {
                    //LogInfo($"{ex.Message}");
                }
            }
        }

        private void UdpListener()
        {
            byte[] start_bytes = Encoding.ASCII.GetBytes("STA");
            byte[] end_bytes = Encoding.ASCII.GetBytes("END");

            byte[] jpg_bytes = Encoding.ASCII.GetBytes("JPG");

            byte[] raw_bytes = Encoding.ASCII.GetBytes("RAW");

            byte[] csm_bytes = Encoding.ASCII.GetBytes("CSM");

            IPEndPoint groupEP = new IPEndPoint(IPAddress.Any, 0);
            bool isRawImage = false;
            int raw_Height = 0;
            int raw_Width = 0;

            bool isreceiveimage = false;
            

            MemoryStream imageStream = new MemoryStream();
            int image_length = 0;

            int receiveimagedatalength = 0;

            try
            {
                while (isudplistening)
                {
                    byte[] bytes = udplistener.Receive(ref groupEP);
                    Debug.WriteLine($"receive from {groupEP.Address},length = {bytes.Length}");
                    if (bytes.Length == 22 && Utils.IsBytesInIndex(bytes, 0, start_bytes)
                       && Utils.IsBytesInIndex(bytes, 22 - 3, end_bytes))
                    {
                        if(isreceiveimage)
                        {
                            LogInfo($"Receive Image Data Error, Drop This Data|{receiveimagedatalength}/{image_length}");
                        }

                        

                        if (Utils.IsBytesInIndex(bytes, 3, jpg_bytes))
                        {
                            //jpg image frame header
                            if (int.TryParse(Encoding.ASCII.GetString(bytes, 9, 8), out int jpgimagelength)
                                && jpgimagelength < 500000 && jpgimagelength > 50)
                            {
                                image_length = jpgimagelength;
                                imageStream = new MemoryStream(image_length);
                                isreceiveimage = true;
                                receiveimagedatalength = 0;
                                isRawImage = false;
                            }
                        }else if (Utils.IsBytesInIndex(bytes, 3, raw_bytes))
                        {
                            // 8bit raw image frame header
                            if (int.TryParse(Encoding.ASCII.GetString(bytes, 9, 4), out int width)
                                && int.TryParse(Encoding.ASCII.GetString(bytes, 13, 4), out int height)
                                && width*height < 2560*2048 && width*height > 100*100)
                            {
                                image_length = width*height;
                                imageStream = new MemoryStream(image_length);
                                isreceiveimage = true;
                                receiveimagedatalength = 0;
                                raw_Height= height;
                                raw_Width=width;
                                isRawImage = true;
                            }
                        }
                    }
                    else if (bytes.Length >= 8 && bytes.Length <= 1031)
                    {
                        // 8bit raw image data or normal image data
                        if (isreceiveimage)
                        {
                            imageStream.Write(bytes, 7, bytes.Length - 7);
                            receiveimagedatalength = receiveimagedatalength + bytes.Length - 7;
                            if (receiveimagedatalength >= image_length)
                            {
                                Debug.WriteLine($"Receive Image Data Success,Length = {receiveimagedatalength}");

                                if (!isRawImage)
                                {
                                    ParseNormalImageData(image_length, imageStream);
                                }else
                                {
                                    ParseRawImageData(image_length,raw_Width,raw_Height, imageStream);
                                }
                                isreceiveimage = false;
                            }
                        }
                    }else
                    {
                        LogInfo($"Udp Receive Unknown Data,Length = {bytes.Length}");
                    }


                }

                
                }
            catch (SocketException se)
            {
                if (se.ErrorCode == 10004)
                {

                }
                else
                {
                    LogInfo("Udp Server Socket Error:" + se.Message);
                }

            }
            catch (Exception ex)
            {
                LogInfo("Udp Server Error:" + ex.Message);
            }
            finally
            {
                isudplistening = false;
                udplistener.Close();
            }

        }

        private void ParseRawImageData(int dataLength, int raw_Width, int raw_Height, MemoryStream memoryStream)
        {
            memoryStream.Position = 0;
            try
            {
                var image = Utils.Raw8BitToBitmap(memoryStream, raw_Width, raw_Height);
                Invoke(new Action(() =>
                {

                    pictureBox1.Image = image;


                }));
            }
            catch (Exception ex)
            {
                Debug.WriteLine(ex.Message);
            }
        }

        private void ParseNormalImageData(int dataLength, MemoryStream memoryStream)
        {
            memoryStream.Position = 0;
            try
            {
                var image = new Bitmap(memoryStream);
                Invoke(new Action(() =>
                {

                    pictureBox1.Image = image;


                }));
            }
            catch (Exception ex)
            {
                Debug.WriteLine(ex.Message);
            }

        }

        private void SendBytes(byte[] buffer)
        {
            if (isTcpConnected)
            {
                tcpClient.GetStream().Write(buffer, 0, buffer.Length);
                WriteMessage("Tcp Client Send: " + Utils.BytesToHex(buffer));
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
                StopUdpServer();
                Invoke(new Action(() => {
                    button2.Text = "Connect";
                }));

            }
        }

        private void button4_Click(object sender, EventArgs e)
        {

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

        private void button1_Click(object sender, EventArgs e)
        {
            //TODO 可以咨询技术指定功能的指令，在需要时自行发送
            //(You can consult the instructions of technical designated functions and send them yourself when necessary)
            try
            {
                
                byte[] buffer = Utils.CreateCmd("PHTSHT","","C");
                SendBytes(buffer);
            }
            catch (Exception ex)
            {
                LogInfo("Send Error:" + ex.Message);
            }
        }
    }
}
