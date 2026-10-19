using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;

namespace Tcp2HttpServer
{
    static class TcpClientUtils
    {
        static List<TcpClient> tcpClients = new List<TcpClient>();

        public static Encoding Encoding = Encoding.UTF8;

        public static int AddClient()
        {
            tcpClients.Add(new TcpClient() { ReceiveTimeout = 3000 ,SendTimeout=1000}); ;
            return tcpClients.Count;
        }

        public static void DeleteClient(int index)
        {
            tcpClients[index].Close();  
            tcpClients.RemoveAt(index);
        }

        public static bool Connect(int index,string ip)
        {
            try
            {
                if(tcpClients[index].Connected)
                {
                    tcpClients[index].Close();
                }
                tcpClients[index]=new TcpClient() { ReceiveTimeout = 3000, SendTimeout = 1000 };
                tcpClients[index].Connect(ip, 4096);
                return true;
            }catch (Exception e)
            {
                Debug.WriteLine(e);
                return false;
            }
        }

        public static string SendAndReceive(int index, byte[] cmdBytes)
        {
            DiscordInBuffer(index);   
            byte[] buffer=new byte[128];      
            tcpClients[index].GetStream().Write(cmdBytes,0,cmdBytes.Length);
            int len = tcpClients[index].GetStream().Read(buffer, 0, buffer.Length);
            return Encoding.GetString(buffer,0,len);
        }

        public static void SendOnly(int index, byte[] cmdBytes)
        {
            tcpClients[index].GetStream().Write(cmdBytes, 0, cmdBytes.Length);
        }


        private static void DiscordInBuffer(int index)
        {
            if (tcpClients[index].Available > 0)
            {
                int length = tcpClients[index].Available;
                tcpClients[index].GetStream().Read(new byte[length], 0, length);
            }
        }

        public static void CloseConnect(int index)
        {
            tcpClients[index].Close();
        }

        public static List<TcpClientInfo> EnumDevice()
        {
            List<TcpClientInfo> list = new List<TcpClientInfo>();   
            foreach (var item in tcpClients)
            {
                TcpClientInfo client = new TcpClientInfo();
                if(item.Connected)
                {
                    client.Connected = true;
                    client.RemoteEndPoint = item.Client.RemoteEndPoint.ToString();
                }
                list.Add(client);
            }
            return list;
        }

    }

    public class TcpClientInfo
    {
        public string RemoteEndPoint=string.Empty;

        public bool Connected=false;

    }
}
