using PluginHelper.api;
using PluginHelper.plugin;
using ScanHelper;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;

namespace InfostepperPluginDemo
{
    public class Serial2KeyBoardPlugin : PluginHelper.plugin.IPlugin
    {
        public string Name
        {
            get { return "Serial2KeyBoard"; }
        }

        public string Description
        {
            get { return "串口转键盘输入插件。当软件串口连接且为最小化时，会将接收到的数据模拟为键盘输入.同时在主界面添加图标\"files/LOGO_100.PNG\""; }
        }

        public string Author
        {
            get { return "Infoscan"; }
        }

        public Version Version
        {
            get { return new Version(1, 0, 0); }
        }

        public void OnLoad()
        {
            Log.Debug($"Plugin:{Name} Load");
            var kwedge = new Kwedge();
            PluginHelper.api.AppEvents.OnAppStart.Add(kwedge.AppStart);
            PluginHelper.api.AppEvents.OnAppEnd.Add(kwedge.AppStop);
            PluginHelper.api.AppEvents.OnMessageReceive.Add(kwedge.MessageReceived);
            
            AppEvents.OnConnected.Add(kwedge.Connected);
            AppEvents.OnDisConnected.Add(kwedge.DisConnected);

            

        }



        public void OnUnload()
        {
            Log.Debug($"Plugin:{Name} UnLoad");
        }

        public void OnUpdate()
        {
            Log.Debug($"Plugin:{Name} Update");
        }
    }
}
