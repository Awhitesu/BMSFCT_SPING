using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace Tcp2HttpServer
{
    internal static class Utils
    {
        public static byte[] HexStringToByteArray(string hexString)
        {
            hexString = hexString.Replace(" ", ""); 
            int byteCount = hexString.Length / 2; 
            byte[] byteArray = new byte[byteCount];

            for (int i = 0; i < byteCount; i++)
            {
                byteArray[i] = Convert.ToByte(hexString.Substring(i * 2, 2), 16);
            }

            return byteArray;
        }

        public static string BytesToHexString(byte[] bytes, int index, int count)
        {
            string strHex = "";
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
    }
}
