using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Web.Http;

namespace Tcp2HttpServer
{
    public class HomeController : ApiController
    {
 
        /// <summary>
        /// 发送指令并获取返回结果
        /// </summary>
        /// <param name="address"></param>
        /// <param name="cmd"></param>
        /// <returns></returns>
        [HttpGet]
        public IHttpActionResult SendAndReadResult(int address,string cmd)
        {
            // http://127.0.0.1:4000/api/home/SendAndReadResult?address=0&cmd=TON
            //{
            //    "message": "ABC123456",
            //    "address": "0",
            //    "cmd": "TON"
            //}
            try
            {
                string result = TcpClientUtils.SendAndReceive(address, Encoding.ASCII.GetBytes(cmd));
                return Json(new Dictionary<string, string>
            {
                { "message", $"{result}" },
                { "address", $"{address}" },
                 { "cmd", $"{cmd}" }
            });

            }
            catch (Exception ex)
            {

                return BadRequest(ex.Message);

            }
        }

        /// <summary>
        /// 仅发送指令，16进制
        /// </summary>
        /// <param name="address"></param>
        /// <param name="hexcmd"></param>
        /// <returns></returns>
        [HttpGet]
        public IHttpActionResult SendHexCmdOnly(int address, string hexcmd)
        {
            try
            {
                // http://127.0.0.1:4000/api/home/SendHexCmdOnly?address=0&hexcmd=544F4646
                string result = TcpClientUtils.SendAndReceive(address, Utils.HexStringToByteArray(hexcmd));
                return Json(new Dictionary<string, string>{
                { "message", "OK" },{ "address", $"{address}" },
                 { "hexcmd", $"{hexcmd}" }});

            }
            catch (Exception ex)
            {

                return BadRequest(ex.Message);

            }
        }


        /// <summary>
        /// 连接设备
        /// </summary>
        /// <param name="address"></param>
        /// <param name="ip"></param>
        /// <returns></returns>
        [HttpGet]
        public IHttpActionResult ConnectDevice(int address, string ip)
        {
            // http://127.0.0.1:4000/api/home/ConnectDevice?address=0&ip=192.168.0.100
            try
            {
                bool result= TcpClientUtils.Connect(address, ip);
                return Json(new Dictionary<string, string>{
                { "result", $"{result}" },{ "address", $"{address}" },
                 { "ip", $"{ip}" }});

            }
            catch (Exception ex)
            {

                return BadRequest(ex.Message);

            }

        }

        /// <summary>
        /// 断开设备连接
        /// </summary>
        /// <param name="address"></param>
        /// <returns></returns>
        [HttpGet]
        public IHttpActionResult DisConnectDevice(int address)
        {
            // http://127.0.0.1:4000/api/home/DisConnectDevice?address=0
            try
            {
                TcpClientUtils.CloseConnect(address);
                return Json(new Dictionary<string, string>{
                { "message", "OK" },{ "address", $"{address}" }});

            }
            catch (Exception ex)
            {

                return BadRequest(ex.Message);

            }

        }

        /// <summary>
        /// 删除设备
        /// </summary>
        /// <param name="address"></param>
        /// <returns></returns>
        [HttpGet]
        public IHttpActionResult DeleteDevice(int address)
        {
            // http://127.0.0.1:4000/api/home/DeleteDevice?address=0
            try
            {
                TcpClientUtils.DeleteClient(address);
                return Json(new Dictionary<string, string>{
                { "message", "OK" },{ "address", $"{address}" }});

            }
            catch (Exception ex)
            {

                return BadRequest(ex.Message);

            }

        }

        /// <summary>
        /// 增加设备
        /// </summary>
        /// <returns></returns>
        [HttpGet]
        public IHttpActionResult AddDevice()
        {
            // http://127.0.0.1:4000/api/home/AddDevice
            try
            {
                int count=TcpClientUtils.AddClient();
                return Json(new Dictionary<string, string>{
                { "message", "OK" },{ "Count",$"{count}"} });

            }
            catch (Exception ex)
            {

                return BadRequest(ex.Message);

            }

        }

        /// <summary>
        /// 枚举设备
        /// </summary>
        /// <returns></returns>
        [HttpGet]
        public IHttpActionResult EnumDevice()
        {
            // http://127.0.0.1:4000/api/home/EnumDevice
            try
            {
               
                return Json(TcpClientUtils.EnumDevice());

            }
            catch (Exception ex)
            {

                return BadRequest(ex.Message);

            }

        }
    }
}
