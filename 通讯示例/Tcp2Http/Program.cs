using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;

//TCP连接扫码设备，并将扫码结果通过http转发的示例
namespace Tcp2Http
{
    internal class Program
    {
        
        const string nrstring="NR\r\n";
        static readonly Encoding Barcode_Encoding = Encoding.UTF8;
        // const string triggercmd = "TON";
        // const string untriggercmd = "TOFF";

        static string access_token = "";
        static void Main(string[] args)
        {
            try
            {
                string deviceip = "192.168.0.100";
                TcpClient client = new TcpClient(deviceip, 4096);

                HttpConnect();

                NetworkStream stream = client.GetStream();
                byte[] buffer = new byte[1024];

                Console.WriteLine("条码数据接收监听中");
                while (true)
                {
                    int length = stream.Read(buffer, 0, buffer.Length);
                    if (length == 0)
                    {
                        Console.WriteLine("连接断开");
                        break;
                    }
                    if (length == 4 && Barcode_Encoding.GetString(buffer, 0, 4) == nrstring)
                    {
                        DecodeFailMessageSend();
                    }
                    else if (length > 4 && length < 30)
                    {
                        DecodeResultSend(buffer, 0, length);
                    }
                    else
                    {
                        UnknownMessageHandle(buffer, 0, length);
                        
                    }
                }
            }
            catch(Exception ex)
            {
                //实际生产环境可能需要报警机制或者重连机制
                Console.WriteLine(ex.Message);
                Console.ReadLine();
            }
        }

        private static void HttpConnect()
        {
            //如果需要授权，联系mes工程师获取授权方法，编写代码获取token
            //如果不需要授权，无视该部分代码
            access_token = "access_token";
        }

        private static void UnknownMessageHandle(byte[] buffer, int index, int length)
        {
            Console.WriteLine("未知的数据，数据长度：" + length);
        }

        private static void DecodeResultSend(byte[] buffer, int index, int length)
        {
            Barcode barcode = new Barcode()
            {
                IsDecodeSuccess = true,
                BarcodeStr = Barcode_Encoding.GetString(buffer,0,length),
                Other = "other"

            };
            if (HttpMessageSend(barcode.ToString()))
            {
                Console.WriteLine("Http转发成功");
            }
//            192.168.0.162 - - [2023 - 06 - 06 14:19:07] "POST /api/V1/barcodeupload?access_token=access_token HTTP/1.1" 200 153 0.004005
//{ 'IsDecodeSuccess': True, 'BarcodeStr': '123456789', 'Other': 'other'}
        }

        private static void DecodeFailMessageSend()
        {
            Barcode barcode = new Barcode()
            {
                IsDecodeSuccess=false,
                BarcodeStr = string.Empty,
                Other="other"

            };
           if(HttpMessageSend(barcode.ToString()))
            {
                Console.WriteLine("Http转发成功");
            }
//            192.168.0.162 - - [2023 - 06 - 06 14:18:15] "POST /api/V1/barcodeupload?access_token=access_token HTTP/1.1" 200 153 0.002642
//{ 'IsDecodeSuccess': False, 'BarcodeStr': '', 'Other': 'other'}
        }

        private static bool HttpMessageSend(string content)
        {
            try
            {
                //实际url和权限认证方式以及请求参数以MES系统规定为准
                string host = "http://192.168.1.200/api/V1/barcodeupload?access_token=" + access_token;
                Encoding encoding = Barcode_Encoding;
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(host);
                request.Timeout = 1000;
                request.Method = "post";
                request.ContentType = "application/json";
                request.KeepAlive = true;
                byte[] buffer = encoding.GetBytes(content);
                request.ContentLength = buffer.Length;
                request.GetRequestStream().Write(buffer, 0, buffer.Length);
                HttpWebResponse response = (HttpWebResponse)request.GetResponse();
                if (response.StatusCode == HttpStatusCode.OK)
                {
                    //StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.Default);
                    //string result = reader.ReadToEnd();
                    //Console.WriteLine(result);
                    return true;
                }
                else
                {
                    return false;
                }
            }catch (Exception ex)
            {
                Console.WriteLine(ex.Message);
                return false;
            }
         
        }
    }

    class Barcode
    {
        public bool IsDecodeSuccess { get; set; }
        public string  BarcodeStr { get; set; }
        
        /// <summary>
        /// 其他消息，可以自行增加，比如产线信息，工位信息
        /// </summary>
        public string  Other { get; set; }

        public override string ToString()
        {
            //实际返回格式以MES系统为准
            Dictionary<string, object> dict = new Dictionary<string, object>
            {
                { "IsDecodeSuccess", IsDecodeSuccess },
                { "BarcodeStr", BarcodeStr },
                { "Other", Other }
            };
            return Newtonsoft.Json.JsonConvert.SerializeObject(dict);

            
        }
    }
}
