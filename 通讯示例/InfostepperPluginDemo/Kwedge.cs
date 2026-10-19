using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;
using PluginHelper.api;
using ScanHelper;

using System.Drawing;

namespace InfostepperPluginDemo
{
    internal class Kwedge
    {
        
        public Kwedge() {
            byte2string = new Dictionary<byte, string>() {
 { 0x08, "{BACKSPACE}" }
,{ 0x09, "{TAB}"}
,{ 0x0d, "{ENTER}"}
,{ 0x11, "{UP}"}
,{ 0x12, "{DOWN}"}
,{ 0x13, "{LEFT}"}
,{ 0x14, "{RIGHT}"}
,{ 0x16, "{HOME}"}
,{ 0x19, "{END}"}
,{ 0x1b, "{ESCAPE}"}
,{ 0xE1, "{F1}"}
,{ 0xE2, "{F2}"}
,{ 0xE3, "{F3}"}
,{ 0xE4, "{F4}"}
,{ 0xE5, "{F5}"}
,{ 0xE6, "{F6}"}
,{ 0xE7, "{F7}"}
,{ 0xE8, "{F8}"}
,{ 0xE9, "{F9}"}
,{ 0xEA, "{F10}"}
,{ 0xEB, "{F11}"}
,{ 0xEC, "{F12}"}
            };


        }
       
        internal void AppStart()
        {

            foreach (var item in Plugincore.Instance.MainForm.Controls)
            {

                if (item is System.Windows.Forms.ToolStrip)
                {
                    var toolstrip = item as System.Windows.Forms.ToolStrip;
                    var tool = new System.Windows.Forms.ToolStripButton();
                    string path = Path.Combine(Plugincore.Instance.CurrentDirectory
                        , "files/LOGO_100.PNG");
                    if (File.Exists(path))
                    {
                        tool.Image = Image.FromFile(path);
                        tool.ImageScaling = ToolStripItemImageScaling.None;
                        tool.Alignment = ToolStripItemAlignment.Right;
                        tool.AutoSize = false;
                        tool.Size = new Size(110, 50);

                        toolstrip.Items.Add(tool);
                    }

                    break;
                }


            }
        }

        internal void AppStop()
        {
            Debug.WriteLine("AppStop Handle");
        }

        internal void Connected()
        {
            string showname=string.Empty;
            if (Plugincore.Instance.pageConnectMode != PageConnectMode.Offline)
            {
                showname = Infostepper.SDK2.CommonUtils.GetTableResult(Plugincore.Instance.myInfoSystem.ConfigParameters.MTable
                    , "INFNAM", "Value");
            }
           
            Debug.WriteLine($"Device:{Plugincore.Instance.DeviceName}({showname})Connected");
             
        }

        internal void DisConnected()
        {
            Debug.WriteLine("DisConnected");
        }
        

        private Dictionary<byte, string> byte2string;
        internal void MessageReceived(byte[] bytes)
        {
            Debug.WriteLine($"mode:{Plugincore.Instance.pageConnectMode}|state:{Plugincore.Instance.MainForm.WindowState}|length:{bytes.Length}");
            if(Plugincore.Instance.MainForm.WindowState==System.Windows.Forms.FormWindowState.Minimized)
            {
                if (bytes.Length < 200 && bytes.Length > 0)
                {
                    try
                    {
                        if (Plugincore.Instance.Encoding == Encoding.ASCII)
                        {
                            StringBuilder stringBuilder = new StringBuilder(bytes.Length);
                            foreach (byte item in bytes)
                            {
                                if (item >= 0x21 && item <= 0x7e)
                                {
                                    stringBuilder.Append((char)item);
                                }
                                else if(byte2string.ContainsKey(item))
                                {
                                    stringBuilder.Append(byte2string[item]);
                                }else
                                {
                                    continue;
                                }
                            }
                            string str = stringBuilder.ToString();
                            if (str.Length > 0)
                            {
                                SendKeys.Send(str);
                            }
                        }
                        else
                        {
                            Clipboard.SetText(Plugincore.Instance.Encoding.GetString(bytes, 0, bytes.Length));
                            SendKeys.SendWait("^V");
                        }
                    }
                    catch
                    {
                        Log.Error("Key Convert Error!Some special characters are not supported!");

                        SendKeys.Send("Some special characters are not supported!{ENTER}");
                    }
                }else
                {
                    //Log.Debug("receive data lenth too long!pass");
                }
            }
        }
    }
}
