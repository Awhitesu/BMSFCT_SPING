using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

namespace DecodeAndGetImageDemo
{
    public static class Utils
    {
        public static Encoding UtilsEncoding = Encoding.Default;

        public static string GetString(byte[] buffer, int index, int count)
        {
            //0x06/0x15......0x0d  Command Return Data Format
            //For a more comprehensive analysis, please refer to:  ReceiveMessageConvert
            if (count > 1 && count < 20 && buffer[index + count - 1] == 0x0d && (buffer[index] == 0x06 || buffer[index] == 0x15))
            {
                return $"Command {(buffer[index] == 0x06 ? "Success" : "Fail")},Content: {UtilsEncoding.GetString(buffer, index + 1, count - 2)}";
            }
            else
            {
                return UtilsEncoding.GetString(buffer, index, count);
            }
        }


        public static byte[] HexToBytes(string hexString)
        {
            hexString = hexString.Replace(" ", ""); // 去除空格
            byte[] byteArray = new byte[hexString.Length / 2];
            for (int i = 0; i < byteArray.Length; i++)
            {
                byteArray[i] = Convert.ToByte(hexString.Substring(i * 2, 2), 16);
            }
            return byteArray;

        }



        /// <summary>
        /// 将字节数组转换为16进制表示形式的字符串（空格分隔）。
        /// </summary>
        /// <param name="bytes">包括待转换的字节序列的字节数组。</param>
        /// <returns>16进制表示形式的字符串（空格分隔）。</returns>
        public static string BytesToHex(byte[] bytes)
        {
            int index = 0;
            int count = bytes.Length;
            string strHex;
            char[] chars = new char[count * 3];
            for (int i = 0, j = 0; i < count; i++, j = j + 3)
            {
                string strTmp = bytes[index + i].ToString("X2");
                chars[j] = strTmp[0];
                chars[j + 1] = strTmp[1];
                chars[j + 2] = ' ';
            }
            strHex = new string(chars);

            return strHex.Trim();
        }

        /// <summary>
        /// 将字符串转为字节数组，支持扩展ASCII码
        /// </summary>
        /// <param name="str">带转换的字符串</param>
        /// <returns>字节数组</returns>
        public static byte[] Getbytes(string str)
        {
            if (str.Length == 0)
            {
                byte[] errorBytes = new byte[] { 0 };
                return errorBytes;
            }
            byte[] bytes = new byte[str.Length];

            int icount = 0;
            foreach (char item in str)
            {
                if (item <= 255)
                {
                    bytes[icount++] = Convert.ToByte(item);
                }
                else
                {
                    bytes[icount++] = 255;
                }
            }

            return bytes;
        }

        public static string BytesToCharString(byte[] bytes, int index, int count)
        {
            string answer = string.Empty;
            for (int i = index; i < count; i++)
            {
                answer += ((char)bytes[i]).ToString();
            }
            return answer;
        }


        public static byte[] CreateCmd(string cmdStr, string cmdval = "", string type = "M")
        {


            string suffix = "\x03";

            string prefixinfo;
            string cmdtyp = type.ToString().Substring(0, 1).ToUpper();
            prefixinfo = "\x02" + cmdtyp + "\x0D";

            cmdStr = cmdStr + cmdval + suffix;

            byte[] lenbytes = GetCmdHexLength(cmdStr.Length);
            cmdStr = prefixinfo + BytesToCharString(lenbytes, 0, lenbytes.Length) + cmdStr;
            return Getbytes(cmdStr);
        }

        static byte[] GetCmdHexLength(int cmdStrlen)
        {
            if (cmdStrlen < 2)
            {
                throw new ArgumentOutOfRangeException("args length too low,length:" + cmdStrlen);
            }
            if (cmdStrlen > 0xffff)
            {
                throw new ArgumentOutOfRangeException("args length too high,length:" + cmdStrlen);
            }
            byte[] lenbytes;
            if (cmdStrlen <= 127)
            {
                lenbytes = new byte[1];
                lenbytes[0] = (byte)cmdStrlen;
            }
            else
            {
                lenbytes = new byte[3];
                lenbytes[0] = 0x82;
                lenbytes[1] = (byte)(cmdStrlen >> 8);
                lenbytes[2] = (byte)(cmdStrlen >> 0);
            }

            return lenbytes;
        }

        public static bool IsBytesInIndex(byte[] longbytes, int index, byte[] smallbytes)
        {
            if (index + smallbytes.Length > longbytes.Length)
            {
                return false;
            }

            for (int i = 0; i < smallbytes.Length; i++)
            {
                if (longbytes[index + i] != smallbytes[i])
                {
                    return false;
                }
            }
            return true;

        }


        public static Bitmap Raw8BitToBitmap(MemoryStream ms, int width, int height)
        {
            byte[] rawData = ms.ToArray();

            
            Bitmap bmp = new Bitmap(width, height, PixelFormat.Format8bppIndexed);

           
            ColorPalette palette = bmp.Palette;
            for (int i = 0; i < 256; i++)
            {
                palette.Entries[i] = Color.FromArgb(i, i, i);
            }
            bmp.Palette = palette;

            
            BitmapData bmpData = bmp.LockBits(
                new Rectangle(0, 0, width, height),
                ImageLockMode.WriteOnly,
                PixelFormat.Format8bppIndexed);

            
            int stride = bmpData.Stride;
            IntPtr ptr = bmpData.Scan0;

            for (int y = 0; y < height; y++)
            {
                Marshal.Copy(rawData, y * width, ptr + y * stride, width);
            }

            bmp.UnlockBits(bmpData);

            return bmp;
        }
    }
}
