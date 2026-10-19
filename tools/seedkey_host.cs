// Local Seed/Key worker. Native vendor code never runs in the Qt process.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

class SeedKeyHost {
    [DllImport("kernel32", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr LoadLibraryEx(string file, IntPtr reserved, uint flags);
    [DllImport("kernel32", CharSet=CharSet.Ansi)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);
    [DllImport("kernel32")] static extern bool FreeLibrary(IntPtr module);
    [DllImport("kernel32")] static extern uint SetErrorMode(uint mode);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int ExC([In] byte[] seed, ushort length, uint level, [MarshalAs(UnmanagedType.LPStr)] string variant, [Out] byte[] key, uint capacity, ref uint actual);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    delegate int ExS([In] byte[] seed, ushort length, uint level, [MarshalAs(UnmanagedType.LPStr)] string variant, [Out] byte[] key, uint capacity, ref uint actual);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int ZC([In] byte[] seed, ushort length, uint level, [MarshalAs(UnmanagedType.LPStr)] string variant, [Out] byte[] key, ref ushort actual);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    delegate int ZS([In] byte[] seed, ushort length, uint level, [MarshalAs(UnmanagedType.LPStr)] string variant, [Out] byte[] key, ref ushort actual);
    static byte[] Hex(string text) {
        if(text.Length % 2 != 0) throw new Exception("Invalid seed HEX length");
        byte[] data=new byte[text.Length/2];
        for(int i=0;i<data.Length;i++) data[i]=Convert.ToByte(text.Substring(i*2,2),16);
        return data;
    }
    static IntPtr Export(IntPtr lib, string name, bool stdcall, int stack) {
        IntPtr p=GetProcAddress(lib,name);
        if(p==IntPtr.Zero) p=GetProcAddress(lib,"_"+name+(stdcall?"@"+stack:""));
        if(p==IntPtr.Zero && stdcall) p=GetProcAddress(lib,name+"@"+stack);
        return p;
    }
    static int Main() {
        SetErrorMode(0x8007); // no native crash/missing-DLL modal dialogs
        IntPtr lib=IntPtr.Zero;
        try {
            string file=Encoding.UTF8.GetString(Convert.FromBase64String(Console.ReadLine()));
            string api=Console.ReadLine(); bool stdcall=Console.ReadLine()=="stdcall";
            byte[] seed=Hex(Console.ReadLine()); uint level=UInt32.Parse(Console.ReadLine());
            string variant=Encoding.UTF8.GetString(Convert.FromBase64String(Console.ReadLine()));
            int capacity=Int32.Parse(Console.ReadLine());
            if(seed.Length==0 || seed.Length>4096 || capacity<1 || capacity>4096) throw new Exception("Invalid seed/key length");
            file=Path.GetFullPath(file);
            lib=LoadLibraryEx(file,IntPtr.Zero,0x1100); // DLL folder + default safe search directories
            if(lib==IntPtr.Zero) throw new Exception("LoadLibrary failed: "+Marshal.GetLastWin32Error());
            if(api=="auto") api=Export(lib,"GenerateKeyEx",stdcall,28)!=IntPtr.Zero?"GenerateKeyEx":"ZLGKey";
            if(api!="GenerateKeyEx" && api!="ZLGKey") throw new Exception("Unsupported SeedKey API");
            IntPtr function=Export(lib,api,stdcall,api=="GenerateKeyEx"?28:24);
            if(function==IntPtr.Zero) throw new Exception("Export not found: "+api);
            byte[] key=new byte[capacity]; uint actual=(uint)capacity; int status;
            if(api=="GenerateKeyEx") {
                actual=0;
                if(stdcall) status=((ExS)Marshal.GetDelegateForFunctionPointer(function,typeof(ExS)))(seed,(ushort)seed.Length,level,variant,key,(uint)capacity,ref actual);
                else status=((ExC)Marshal.GetDelegateForFunctionPointer(function,typeof(ExC)))(seed,(ushort)seed.Length,level,variant,key,(uint)capacity,ref actual);
            } else {
                ushort size=(ushort)capacity;
                if(stdcall) status=((ZS)Marshal.GetDelegateForFunctionPointer(function,typeof(ZS)))(seed,(ushort)seed.Length,level,variant,key,ref size);
                else status=((ZC)Marshal.GetDelegateForFunctionPointer(function,typeof(ZC)))(seed,(ushort)seed.Length,level,variant,key,ref size);
                actual=size;
            }
            if(status!=0) throw new Exception("SeedKey returned error "+status);
            if(actual==0 || actual>(uint)capacity) throw new Exception("Invalid returned key length: "+actual);
            Console.WriteLine("BMS_SEEDKEY_OK:"+BitConverter.ToString(key,0,(int)actual).Replace("-",""));
            return 0;
        } catch(Exception e) {
            Console.WriteLine("BMS_SEEDKEY_ERROR:"+Convert.ToBase64String(Encoding.UTF8.GetBytes(e.Message)));
            return 1;
        } finally { if(lib!=IntPtr.Zero) FreeLibrary(lib); }
    }
}
