using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Web.Http;
using System.Web.Http.SelfHost;

namespace Tcp2HttpServer
{
    public class HttpServer
    {
        private HttpSelfHostServer server;

        public HttpServer(string ip, int port)
        {
            var config = new HttpSelfHostConfiguration($"http://{ip}:{port}");
            config.MapHttpAttributeRoutes();
            config.Routes.MapHttpRoute("DefaultApi", "api/{controller}/{action}");
            server = new HttpSelfHostServer(config);
        }

        public Task StartHttpServer()
        {
            return server.OpenAsync();
        }

        public Task CloseHttpServer()
        {
            return server.CloseAsync();
        }
    }
}
