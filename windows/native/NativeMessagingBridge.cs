using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;

class NativeMessagingBridge {
    [STAThread] static int Main(string[] args){
        const string origin="chrome-extension://iincmekgilffpjmmmcgloliadppioajc/";
        if(args.Length==0||args[0]!=origin)return 2;
        string root=AppDomain.CurrentDomain.BaseDirectory;
        string pyScript=Path.Combine(root,"strictrecords_native_host.py");
        string host=Path.Combine(root,"native_host_runtime","StrictRecordsNative.exe");
        ProcessStartInfo options;
        if(File.Exists(pyScript)){
            string pythonExe = @"C:\Program Files\Python314\python.exe";
            if(!File.Exists(pythonExe)) pythonExe = "python.exe";
            options=new ProcessStartInfo(pythonExe, "\"" + pyScript + "\" " + origin){
                UseShellExecute=false,CreateNoWindow=true,
                RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,WorkingDirectory=root
            };
        } else if(File.Exists(host)){
            options=new ProcessStartInfo(host,origin){
                UseShellExecute=false,CreateNoWindow=true,
                RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,WorkingDirectory=root
            };
        } else {
            return 3;
        }
        using(var child=Process.Start(options)){
            var input=Console.OpenStandardInput();var output=Console.OpenStandardOutput();
            var send=Task.Factory.StartNew(delegate{try{Relay(input,child.StandardInput.BaseStream);child.StandardInput.Close();}catch(IOException){}});
            var errors=child.StandardError.ReadToEndAsync();
            try{Relay(child.StandardOutput.BaseStream,output);}catch(IOException){}
            child.WaitForExit();return child.ExitCode;
        }
    }
    static void Relay(Stream source,Stream destination){
        byte[] buffer=new byte[8192];int count;
        while((count=source.Read(buffer,0,buffer.Length))>0){destination.Write(buffer,0,count);destination.Flush();}
    }
}
