using System;
using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Threading;

namespace StrictBotNative {
public static class NativePreview {
    [STAThread] public static void Main(string[] args) {
        try {
            File.AppendAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-progress.txt"),"start "+String.Join("|",args)+Environment.NewLine);
            if(args.Length>=2 && args[0]=="--render") { RenderViews(args[1]);return; }
            if(args.Length>=2 && args[0]=="--logo") {
                var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(NativeHost.RenderSettingsLogo(new Rig())));
                using(var stream=File.Create(args[1]))encoder.Save(stream);return;
            }
            if(args.Length>=2 && args[0]=="--verify-board") {
                string report=NativeHost.VerifyBoardLayouts(args[1]);File.WriteAllText(Path.Combine(args[1],"layout-report.json"),report);return;
            }
            if(args.Length>=2 && args[0]=="--verify") { VerifyMotion(args[1]);return; }
            var serializer=new JavaScriptSerializer();var stopped=new ManualResetEvent(false);NativeHost host=null;
            string log=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-sequence.jsonl");
            host=new NativeHost(delegate(string command) {
                File.AppendAllText(log,command+Environment.NewLine);
                var item=serializer.Deserialize<Dictionary<string,object>>(command);
                string action=Convert.ToString(item["action"]);
                if(action=="home")stopped.Set();
                else if(action=="settings_closed"&&!host.Metrics().Contains("\"visible\":true"))stopped.Set();
                else if(action=="close")host.Result(-1,false,"PREVIEW — No application is attached. Back to Home exits this preview.",false);
                else if(action=="save_settings") {
                    File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-preferences.json"),serializer.Serialize(item["values"]));
                    host.SettingsSaved(true,"Settings saved");
                }
            });
            host.Start();
            var display=new Win.MonitorInfo {Size=System.Runtime.InteropServices.Marshal.SizeOf(typeof(Win.MonitorInfo))};
            var monitor=Win.MonitorFromWindow(IntPtr.Zero,2);Win.GetMonitorInfo(monitor,ref display);
            uint dpiX=96,dpiY;try{Win.GetDpiForMonitor(monitor,0,out dpiX,out dpiY);}catch{}
            var prefs=new Dictionary<string,object>{{"robot_size",100},{"popup_size",100},{"walking",true},{"speed","normal"},{"dim",22}};
            string preferenceFile=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-preferences.json");
            if(File.Exists(preferenceFile))prefs=serializer.Deserialize<Dictionary<string,object>>(File.ReadAllText(preferenceFile));
            var payload=new {session=-1,preview=true,target_app="StrictBot Preview",preferences=prefs,
                work=new[]{display.Work.Left,display.Work.Top,display.Work.Right,display.Work.Bottom},dpi=dpiX};
            if(args.Length>0&&args[0]=="--settings")host.ShowSettings();else host.Present(serializer.Serialize(payload));
            int duration=0;for(int i=0;i<args.Length-1;i++)if(args[i]=="--duration")Int32.TryParse(args[i+1],out duration);
            stopped.WaitOne(duration>0?duration*1000:Timeout.Infinite);
            File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-metrics.json"),host.Metrics());
            host.Shutdown();
        } catch(Exception error) {File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-error.txt"),error.ToString());}
    }
    public static void VerifyMotion(string path) {
        var reports=new List<object>();bool passed=true;
        foreach(double speed in new[]{.7,1.0,1.4}) {
            var motion=new Motion(-2.7,3.0);motion.Speed=speed;
            double slip=0,error=0;int bothAirborne=0,contacts=0;
            var previous=new System.Windows.Media.Media3D.Point3D[2];var planted=new bool[2];
            for(int frame=0;frame<6000;frame++) {
                motion.Update(1.0/120);
                if(motion.Feet[0].Lift>.001&&motion.Feet[1].Lift>.001)bothAirborne++;
                for(int i=0;i<2;i++) {
                    var foot=motion.Feet[i];
                    if(Array.IndexOf(Motion.Stages,motion.Stage)<3&&foot.Planted&&planted[i]) {
                        slip=Math.Max(slip,(foot.Ground-previous[i]).Length);contacts++;
                    }
                    previous[i]=foot.Ground;planted[i]=foot.Planted;
                    var hip=motion.Hip(i);var knee=motion.Knee(i);var ankle=motion.Ankle(i);
                    error=Math.Max(error,Math.Abs((knee-hip).Length-.65));error=Math.Max(error,Math.Abs((ankle-knee).Length-.65));
                }
            }
            bool all=String.Join(",",motion.Visited.ToArray())==String.Join(",",Motion.Stages);
            bool ok=all&&slip<1e-6&&error<.005&&bothAirborne==0&&motion.Stage=="idle";
            passed=passed&&ok;reports.Add(new{speed=speed,passed=ok,stages=motion.Visited,stance_samples=contacts,
                maximum_planted_slide=slip,maximum_bone_length_error=error,both_feet_airborne_frames=bothAirborne});
        }
        File.WriteAllText(path,new JavaScriptSerializer().Serialize(new{passed=passed,runs=reports}));
        if(!passed)Environment.ExitCode=2;
    }
    public static void RenderViews(string folder) {
        Directory.CreateDirectory(folder);
        File.AppendAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-progress.txt"),"render directory "+folder+Environment.NewLine);
        var rig=new Rig();
        File.AppendAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-progress.txt"),"rig constructed"+Environment.NewLine);
        var window=new Window {Width=600,Height=900,Left=-10000,Top=-10000,ShowActivated=false,ShowInTaskbar=false,
            WindowStyle=WindowStyle.None,AllowsTransparency=true,Background=Brushes.Transparent,Content=rig.Viewport};
        window.Show();
        string[] names={"front","three-quarter","side","walking","stop","point","speak","idle"};
        foreach(string name in names) {
            var motion=new Motion(-1.5,0);
            string stage=name=="walking"?"walking":name=="stop"?"stop":name=="point"?"point":name=="speak"?"speak":"idle";
            motion.SetStage(stage);
            if(stage=="idle") {motion.Debug=false;for(int i=0;i<120;i++)motion.Update(1.0/60);motion.Debug=true;}
            rig.PresentationYaw=name=="three-quarter"?35:name=="side"?85:0;
            rig.Update(motion);
            rig.Viewport.Measure(new Size(600,900));rig.Viewport.Arrange(new Rect(0,0,600,900));rig.Viewport.UpdateLayout();
            Dispatcher.CurrentDispatcher.Invoke(DispatcherPriority.Render,new Action(delegate{}));
            var target=new RenderTargetBitmap(1200,1800,192,192,PixelFormats.Pbgra32);target.Render(rig.Viewport);
            var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(target));
            using(var stream=File.Create(Path.Combine(folder,name+".png")))encoder.Save(stream);
        }
        window.Close();
    }
}
}
