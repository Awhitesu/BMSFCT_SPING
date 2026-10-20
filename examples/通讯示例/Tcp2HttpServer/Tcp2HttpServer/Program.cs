using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Http;

namespace Tcp2HttpServer
{
    internal class Program
    {
        static HttpServer httpServer;
        static bool isRunning = false;
        const string deviceIp1 = "192.168.0.162";
        static void Main(string[] args)
        {          
            StartServer();                 
            Console.ReadLine();
            if (isRunning)
            {
                StopServer();
                Thread.Sleep(200);
            }
        }

        static async void StartServer()
        {
            try
            {
                httpServer = new HttpServer("localhost", 4000);
                await httpServer.StartHttpServer();
                Console.WriteLine("Http Server启动成功");
                isRunning=true;
                TcpClientUtils.AddClient();
                TcpClientUtils.AddClient();
                if (TcpClientUtils.Connect(0, deviceIp1))
                {
                    Console.WriteLine("TCP 连接默认扫码设备成功");
                }else
                {
                    Console.WriteLine("TCP 连接默认扫码设备失败");
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("服务启动失败：");
                Console.WriteLine(ex.Message);
            }
        }

        static async void StopServer()
        {
            try
            {
                isRunning = false;
                TcpClientUtils.CloseConnect(0);
                await httpServer.CloseHttpServer();
                Console.WriteLine("服务关闭成功：");

            }
            catch (Exception ex)
            {
                Console.WriteLine("服务关闭失败：");
                Console.WriteLine(ex.Message);
            }
        }
    }
}
