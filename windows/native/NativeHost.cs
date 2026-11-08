using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Shapes;
using System.Windows.Threading;
using System.Speech.Synthesis;

namespace StrictBotNative {
public static class Win {
    [StructLayout(LayoutKind.Sequential)] public struct Rect {public int Left,Top,Right,Bottom;}
    [StructLayout(LayoutKind.Sequential)] public struct MonitorInfo {public int Size;public Rect Monitor,Work;public int Flags;}
    [StructLayout(LayoutKind.Sequential)] public struct Margins {public int Left,Right,Top,Bottom;}
    [DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr h,int attribute,ref int value,int size);
    [DllImport("dwmapi.dll")] public static extern int DwmExtendFrameIntoClientArea(IntPtr h,ref Margins margins);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] public static extern IntPtr GetWindowLongPtr(IntPtr h,int index);
    [DllImport("user32.dll")] public static extern IntPtr SetWindowLongPtr(IntPtr h,int index,IntPtr value);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int width,int height,uint flags);
    [DllImport("user32.dll")] public static extern IntPtr MonitorFromWindow(IntPtr h,int flags);
    [DllImport("user32.dll")] public static extern bool GetMonitorInfo(IntPtr h,ref MonitorInfo info);
    [DllImport("shcore.dll")] public static extern int GetDpiForMonitor(IntPtr h,int type,out uint x,out uint y);
    public static IntPtr Handle(Window window){return new WindowInteropHelper(window).EnsureHandle();}
    public static void PassThrough(Window window) {
        IntPtr h=Handle(window);long style=GetWindowLongPtr(h,-20).ToInt64();
        SetWindowLongPtr(h,-20,new IntPtr(style|0x20|0x80|0x08000000));
    }
    public static void Dark(Window window) {int value=1;DwmSetWindowAttribute(Handle(window),20,ref value,4);}
}

public sealed partial class NativeHost {
    readonly Action<string> callback;
    readonly JavaScriptSerializer json=new JavaScriptSerializer();
    Dispatcher dispatcher;Thread thread;
    readonly ManualResetEvent ready=new ManualResetEvent(false);
    Exception startupError;
    Window backdrop,actor,popup,settings;
    ImageSource windowBrand;
    readonly Dictionary<Window,int[]> positioned=new Dictionary<Window,int[]>();
    Canvas actorCanvas;
    Border speech;
    TextBlock reason,status,points,required,application,access,expiry;
    Button closeButton;
    Rig rig;Motion motion;
    Dictionary<string,object> data=new Dictionary<string,object>(),prefs=new Dictionary<string,object>();
    bool allowClose,visible,subscribed;
    long session;
    double[] work={0,0,1920,1040};
    double dpi=96,baseScale=1,popupW,popupH,popupX,popupY,robotW,robotH,anchor,floor,meters;
    bool rightSide,robotShown;
    long lastTimestamp,lastDisplayStamp;
    TimeSpan lastRenderingTime=TimeSpan.MinValue;
    readonly List<double> movingIntervals=new List<double>(),idleIntervals=new List<double>();
    double renderSeconds,maxFrame;
    long frames;
    string backdropMode="dim fallback";
    int backdropHResult=-1;
    SolidColorBrush backdropTint;
    SpeechSynthesizer voice;
    DispatcherTimer layoutWatch;
    string lastStage="";
    long voiceSession;
    public NativeHost(Action<string> action){callback=action;}
    public void Start() {
        thread=new Thread(delegate() {
            try {
                try {Win.SetThreadDpiAwarenessContext(new IntPtr(-4));}catch(EntryPointNotFoundException){}
                dispatcher=Dispatcher.CurrentDispatcher;
                dispatcher.UnhandledException+=delegate(object sender,DispatcherUnhandledExceptionEventArgs error) {
                    error.Handled=true;
                    try{CloseCore();}catch{}
                    Emit("native_error",error.Exception.ToString());
                };
                rig=new Rig();windowBrand=RenderSettingsLogo(rig);ready.Set();Dispatcher.Run();
            } catch(Exception ex){startupError=ex;ready.Set();}
        });
        thread.IsBackground=true;thread.Name="StrictBotNative3D";thread.SetApartmentState(ApartmentState.STA);thread.Start();
        if(!ready.WaitOne(30000))throw new TimeoutException("Native StrictBot renderer startup timed out");
        if(startupError!=null)throw new InvalidOperationException("Native StrictBot startup failed",startupError);
    }
    void Post(Action action){if(dispatcher!=null&&!dispatcher.HasShutdownStarted)dispatcher.BeginInvoke(action);}
    void Emit(string action,object values=null){if(callback!=null)callback(json.Serialize(new{action=action,session=session,values=values}));}
    static double Number(Dictionary<string,object> source,string key,double fallback) {
        object value;if(!source.TryGetValue(key,out value)||value==null)return fallback;
        try{return Convert.ToDouble(value);}catch{return fallback;}
    }
    static string Text(Dictionary<string,object> source,string key,string fallback="") {
        object value;return source.TryGetValue(key,out value)&&value!=null?Convert.ToString(value):fallback;
    }
    static bool Flag(Dictionary<string,object> source,string key,bool fallback=false) {
        object value;return source.TryGetValue(key,out value)&&value is bool?(bool)value:fallback;
    }
    public void Present(string payload) {Post(delegate {PresentCore(json.Deserialize<Dictionary<string,object>>(payload));});}
    public void Configure(string payload){Post(delegate {
        var values=json.Deserialize<Dictionary<string,object>>(payload);
        object value;if(values.TryGetValue("preferences",out value)&&value is Dictionary<string,object>)prefs=(Dictionary<string,object>)value;
        if(values.TryGetValue("work",out value)&&value is object[]){var a=(object[])value;for(int i=0;i<4;i++)work[i]=Convert.ToDouble(a[i]);}
        dpi=Number(values,"dpi",96);if(visible)Reflow(true);
        if(settingsPage!=null)ShowSettingsSection(settingsSection);
    });}
    void PresentCore(Dictionary<string,object> values) {
        long next=(long)Number(values,"session",0);
        bool fresh=!visible||next!=session;
        data=values;session=next;
        object preference;if(values.TryGetValue("preferences",out preference)&&preference is Dictionary<string,object>)prefs=(Dictionary<string,object>)preference;
        object bounds;if(values.TryGetValue("work",out bounds)&&bounds is object[]){object[] a=(object[])bounds;for(int i=0;i<4;i++)work[i]=Convert.ToDouble(a[i]);}
        else if(bounds is ArrayList){var a=(ArrayList)bounds;for(int i=0;i<4;i++)work[i]=Convert.ToDouble(a[i]);}
        dpi=Number(values,"dpi",96);
        if(fresh) {
            CloseCore();data=values;session=next;
            backdrop=MakeBackdrop();
            actor=Window("StrictBot PC — Articulated 3D robot",true,false);
            actorCanvas=new Canvas {ClipToBounds=true,Background=Brushes.Transparent};actor.Content=actorCanvas;
            actorCanvas.Children.Add(rig.Viewport);
            speech=BuildSpeech();actorCanvas.Children.Add(speech);speech.Visibility=Visibility.Hidden;
            popup=Window("StrictBot PC — Restricted application",true,true);
            popup.SourceInitialized+=delegate {
                IntPtr handle=Win.Handle(popup);var source=HwndSource.FromHwnd(handle);
                source.AddHook(PopupChrome);
                long style=Win.GetWindowLongPtr(handle,-16).ToInt64();
                Win.SetWindowLongPtr(handle,-16,new IntPtr(style|0x00c00000|0x00080000));
                Win.SetWindowPos(handle,IntPtr.Zero,0,0,0,0,0x0037);
            };
            popup.Content=BuildCard();
            popup.Closing+=delegate(object sender,System.ComponentModel.CancelEventArgs e){if(!allowClose)e.Cancel=true;};
            popup.PreviewKeyDown+=delegate(object sender,KeyEventArgs e){if(e.Key==Key.Escape||(e.Key==Key.F4&&Keyboard.Modifiers==ModifierKeys.Alt))e.Handled=true;};
            UpdateText();motion=null;Reflow(false);
            motion=new Motion(-robotW/meters,anchor/meters,Flag(prefs,"walking",true));
            motion.Speed=Text(prefs,"speed","normal")=="slow"?.7:Text(prefs,"speed","normal")=="fast"?1.4:1;
            rig.Update(motion,rightSide);Reflow(false);
            // Establish the existing ownership chain without activating a robot
            // that arrived while the hourly panel already owns the foreground.
            if(!robotSuspended)backdrop.Show();
            actor.Owner=backdrop;popup.Owner=backdrop;
            if(!robotSuspended) {
                if(robotShown)actor.Show();Win.PassThrough(backdrop);Win.PassThrough(actor);
                popup.Show();Reflow(false);popup.Activate();
            }
            lastStage="";
            lastTimestamp=Stopwatch.GetTimestamp();lastRenderingTime=TimeSpan.MinValue;
            movingIntervals.Clear();idleIntervals.Clear();frames=0;renderSeconds=0;maxFrame=0;
            visible=true;lastDisplayStamp=lastTimestamp;
            if(Flag(data,"robot_preview",true)&&!subscribed){CompositionTarget.Rendering+=Frame;subscribed=true;}
            if(layoutWatch==null){layoutWatch=new DispatcherTimer {Interval=TimeSpan.FromSeconds(1)};layoutWatch.Tick+=delegate{if(visible)RefreshDisplay();};}layoutWatch.Start();
        }
        UpdateText();
        Reflow(false);
        UpdateHourlyReady();
    }
    Window Window(string title,bool transparent,bool input) {
        var w=new Window {Title=title,WindowStyle=WindowStyle.None,ResizeMode=ResizeMode.NoResize,
            AllowsTransparency=transparent,Background=Brushes.Transparent,Topmost=true,ShowInTaskbar=input,
            ShowActivated=input,Focusable=input,Icon=windowBrand,WindowStartupLocation=WindowStartupLocation.Manual};
        return w;
    }
    IntPtr PopupChrome(IntPtr hwnd,int message,IntPtr wParam,IntPtr lParam,ref bool handled) {
        if(message==0x0083||message==0x0085){handled=true;return IntPtr.Zero;}
        if(message==0x0086){handled=true;return new IntPtr(1);}
        if(message==0x0112&&((wParam.ToInt64()&0xfff0)==0xf060)){handled=true;return IntPtr.Zero;}
        return IntPtr.Zero;
    }
    Window MakeBackdrop() {
        var glass=Window("StrictBot PC — Live desktop backdrop",false,false);
        glass.SourceInitialized+=delegate {
            var h=Win.Handle(glass);var source=HwndSource.FromHwnd(h);
            if(source!=null)source.CompositionTarget.BackgroundColor=Colors.Transparent;
            var margins=new Win.Margins {Left=-1,Right=-1,Top=-1,Bottom=-1};
            Win.DwmExtendFrameIntoClientArea(h,ref margins);
            int dark=1;Win.DwmSetWindowAttribute(h,20,ref dark,4);
            int type=3;backdropHResult=Win.DwmSetWindowAttribute(h,38,ref type,4);
        };
        glass.Width=1;glass.Height=1;new WindowInteropHelper(glass).EnsureHandle();
        if(backdropHResult<0) {
            glass.Close();glass=Window("StrictBot PC — Dimmed desktop fallback",true,false);
            backdropMode="dim fallback";
        } else backdropMode="Windows Desktop Acrylic";
        backdropTint=new SolidColorBrush(Color.FromArgb(0,2,10,24));
        glass.Content=new Border {Background=backdropTint};
        return glass;
    }
    static TextBlock Label(string text,double size,string color="#e8f7ff",bool bold=false) {
        return new TextBlock {Text=text,FontFamily=new FontFamily("Segoe UI"),FontSize=size,
            Foreground=G.B(color),FontWeight=bold?FontWeights.Bold:FontWeights.Normal,TextWrapping=TextWrapping.Wrap,
            TextAlignment=TextAlignment.Center,VerticalAlignment=VerticalAlignment.Center};
    }
    static LinearGradientBrush Gradient(string top,string bottom) {
        return new LinearGradientBrush(G.C(top),G.C(bottom),new Point(0,0),new Point(1,1));
    }
    static Border Panel(UIElement child,double radius=16,bool lit=false) {
        return new Border {Child=child,CornerRadius=new CornerRadius(radius),BorderThickness=new Thickness(lit?1.5:1),
            BorderBrush=G.B(lit?"#37dcea":"#286b84"),Background=Gradient("#12394f","#081d32"),Padding=new Thickness(12)};
    }
    static Button ActionButton(string title,string subtitle,bool primary,Action click,string iconName=null) {
        var copy=new StackPanel {VerticalAlignment=VerticalAlignment.Center};
        var titleRow=new StackPanel {Orientation=Orientation.Horizontal,HorizontalAlignment=HorizontalAlignment.Center};
        var icon=BoardIcon(iconName??(primary?"close":"home"),20,primary?"#FFFFFF":"#60DEF5");
        titleRow.Children.Add(icon);var name=BoardText(title,20,true,primary?"#FFFFFF":"#E3F3FD");name.Margin=new Thickness(8,0,0,0);titleRow.Children.Add(name);
        copy.Children.Add(titleRow);var subtitleLabel=BoardText(subtitle,13,false,primary?"#FFFFFF":"#E0ECF5");subtitleLabel.TextAlignment=TextAlignment.Center;subtitleLabel.Margin=new Thickness(0,4,0,0);copy.Children.Add(subtitleLabel);
        copy.Margin=new Thickness(10,8,10,8);
        var button=new Button {Content=copy,HorizontalContentAlignment=HorizontalAlignment.Stretch,Cursor=Cursors.Hand,Background=G.B(primary?"#D93B4E":"#10334B"),
            BorderBrush=G.B(primary?"#FF8993":"#52DBF5"),BorderThickness=new Thickness(2),Padding=new Thickness(8),Margin=new Thickness(0)};
        var border=new FrameworkElementFactory(typeof(Border));border.Name="actionSurface";border.SetValue(Border.CornerRadiusProperty,new CornerRadius(20));
        border.SetValue(Border.BackgroundProperty,new TemplateBindingExtension(Control.BackgroundProperty));
        border.SetValue(Border.BorderBrushProperty,new TemplateBindingExtension(Control.BorderBrushProperty));
        border.SetValue(Border.BorderThicknessProperty,new TemplateBindingExtension(Control.BorderThicknessProperty));
        var content=new FrameworkElementFactory(typeof(ContentPresenter));content.SetValue(ContentPresenter.HorizontalAlignmentProperty,HorizontalAlignment.Stretch);
        content.SetValue(ContentPresenter.VerticalAlignmentProperty,VerticalAlignment.Center);border.AppendChild(content);
        var template=new ControlTemplate(typeof(Button)){VisualTree=border};
        foreach(var pair in new[]{new object[]{UIElement.IsMouseOverProperty,true,.90},new object[]{Button.IsPressedProperty,true,.72},new object[]{UIElement.IsEnabledProperty,false,.52}}) {
            var trigger=new Trigger {Property=(DependencyProperty)pair[0],Value=pair[1]};trigger.Setters.Add(new Setter(UIElement.OpacityProperty,pair[2],"actionSurface"));template.Triggers.Add(trigger);
        }
        var focus=new Trigger {Property=UIElement.IsKeyboardFocusedProperty,Value=true};focus.Setters.Add(new Setter(Border.BorderBrushProperty,Brushes.White,"actionSurface"));template.Triggers.Add(focus);
        button.Template=template;
        button.Click+=delegate{click();};AutomationProperties.SetName(button,title);return button;
    }
    Border BuildSpeech() {
        var copy=new StackPanel();copy.Children.Add(Label("Come on, let's",17,"#effbff",true));copy.Children.Add(Label("concentrate!",23,"#00e6f5",true));
        var bubble=new Grid {ClipToBounds=false};
        bubble.Children.Add(new System.Windows.Shapes.Path {Data=Geometry.Parse("M126,65 L148,65 L126,98 Z"),Fill=G.B("#071b2d"),Stroke=G.B("#31e6ff"),StrokeThickness=1.8});
        bubble.Children.Add(copy);
        return new Border {Child=bubble,Width=250,Height=100,Padding=new Thickness(14),CornerRadius=new CornerRadius(90),
            BorderThickness=new Thickness(1.8),BorderBrush=G.B("#31e6ff"),Background=Gradient("#ed0a2b43","#f0031328"),
            Effect=new DropShadowEffect {Color=G.C("#00cfea"),BlurRadius=14,ShadowDepth=0,Opacity=.60}};
    }
    void UpdateText() {
        if(application==null)return;
        string app=Text(data,"target_app","Application");
        bool preview=Flag(data,"preview");application.Text=preview?"Notification preview":app;
        object score,threshold;bool hasScore=data.TryGetValue("score",out score)&&score!=null,hasThreshold=data.TryGetValue("required_score",out threshold)&&threshold!=null;
        points.Text=hasScore?Convert.ToString(score):"Not loaded";
        required.Text=hasThreshold?Convert.ToString(threshold):"Not loaded";
        access.Text="Blocked";
        reason.Text=Text(data,"reason","");
        string deadline=Text(data,"access_expiry");if(deadline!="")reason.Text+="  Approved access expiry: "+deadline;
        boardMetrics.Visibility=preview?Visibility.Collapsed:Visibility.Visible;boardPreview.Visibility=preview?Visibility.Visible:Visibility.Collapsed;
        boardDescription.Text=preview?"Review the appearance of your focus reminder.":"This application is currently blocked. Return to your planned task.";
        boardGuidance.Visibility=preview?Visibility.Collapsed:Visibility.Visible;
        if(!preview&&(!hasScore||!hasThreshold))reason.Text="Live points could not be loaded. Open StrictRecords to check your balance and required points.";
        if(preview)reason.Text="Live points are hidden in preview mode.";
        reason.Visibility=reason.Text==""?Visibility.Collapsed:Visibility.Visible;
        application.ToolTip=app;
    }
    void Position(Window window,double x,double y,double width,double height) {
        double factor=dpi/96;int px=(int)Math.Round(x*factor),py=(int)Math.Round(y*factor),pw=Math.Max(1,(int)Math.Round(width*factor)),ph=Math.Max(1,(int)Math.Round(height*factor));
        IntPtr currentHandle=new WindowInteropHelper(window).Handle;int[] previous;
        if(currentHandle!=IntPtr.Zero&&positioned.TryGetValue(window,out previous)&&previous[0]==px&&previous[1]==py&&previous[2]==pw&&previous[3]==ph)return;
        window.Left=x;window.Top=y;window.Width=Math.Max(1,width);window.Height=Math.Max(1,height);
        IntPtr handle=new WindowInteropHelper(window).Handle;
        if(handle!=IntPtr.Zero) {
            Win.SetWindowPos(handle,IntPtr.Zero,px,py,pw,ph,0x0014);
            positioned[window]=new[]{px,py,pw,ph};
        }
    }
    void Reflow(bool preserve) {
        double scale=dpi/96,width=(work[2]-work[0])/scale,height=(work[3]-work[1])/scale;
        baseScale=Math.Min(1,Math.Min(width/1120,height/720));
        double popupScale=Number(prefs,"popup_size",100)/100;
        popupW=Math.Max(1,Math.Min(width-24,Math.Max(520,720*popupScale)));
        double robotScale=baseScale*Number(prefs,"robot_size",100)/100;
        robotH=Math.Min(height*.72,465*robotScale);robotW=robotH*.66;
        double gap=12*baseScale;
        robotShown=Flag(data,"robot_preview",true)&&width>=520+robotW+gap+32&&height>=520;
        if(robotShown)popupW=Math.Min(popupW,width-robotW-gap-32);
        var board=popup.Content as FrameworkElement;
        board.Measure(new Size(popupW,double.PositiveInfinity));popupH=Math.Max(1,Math.Min(height-24,board.DesiredSize.Height));
        rightSide=Text(prefs,"robot_position","left")=="right";
        double composition=Math.Min(width,popupW+robotW+gap);
        double start=(width-composition)/2;
        popupX=robotShown?(rightSide?start:start+robotW+gap):(width-popupW)/2;
        popupY=(height-popupH)*(Text(prefs,"popup_position","center")=="upper-center"?.20:.50);
        anchor=rightSide?popupX+popupW+robotW/2:popupX-robotW/2-gap;
        floor=Math.Min(height-20*baseScale,Math.Max(popupY+popupH-18*baseScale,robotH+45*baseScale));
        meters=robotH/4.10;
        Position(backdrop,work[0]/scale,work[1]/scale,width,height);
        double actorRoot=motion==null?anchor:motion.X*meters;
        Position(actor,work[0]/scale+actorRoot-robotW/2,work[1]/scale+floor-robotH,robotW,robotH);
        Position(popup,work[0]/scale+popupX,work[1]/scale+popupY,popupW,popupH);
        popup.Opacity=1;
        if(boardSurface!=null)boardSurface.Background=new SolidColorBrush(Color.FromArgb((byte)(255*Number(prefs,"transparency",96)/100),12,29,45));
        if(backdropTint!=null){backdropTint.BeginAnimation(SolidColorBrush.ColorProperty,null);backdropTint.Color=Color.FromArgb((byte)(255*Number(prefs,"dim",22)/100),2,10,24);}
        rig.Viewport.Width=robotW;rig.Viewport.Height=robotH;
        if(visible&&!robotSuspended&&actor!=null){if(robotShown&&!actor.IsVisible)actor.Show();else if(!robotShown&&actor.IsVisible)actor.Hide();}
        if(preserve&&motion!=null) {
            motion.Reanchor(anchor/meters);
            motion.Speed=Text(prefs,"speed","normal")=="slow"?.7:Text(prefs,"speed","normal")=="fast"?1.4:1;
        }
        UpdateHourlyReady();
    }
    void Frame(object sender,EventArgs args) {
        if(!visible)return;
        if(robotSuspended){lastTimestamp=Stopwatch.GetTimestamp();return;}
        var rendering=args as RenderingEventArgs;
        if(rendering!=null&&rendering.RenderingTime==lastRenderingTime)return;
        if(rendering!=null)lastRenderingTime=rendering.RenderingTime;
        long stamp=Stopwatch.GetTimestamp();double dt=(double)(stamp-lastTimestamp)/Stopwatch.Frequency;
        double interval=motion.Stage=="idle"?1.0/30:(RenderCapability.Tier>>16)>=2?1.0/60:1.0/30;
        if(dt<interval*.75)return;
        lastTimestamp=stamp;
        var cost=Stopwatch.StartNew();motion.Update(dt);rig.Update(motion,rightSide);
        if(frames>2){var samples=motion.Stage=="idle"?idleIntervals:movingIntervals;samples.Add(dt);if(samples.Count>1800)samples.RemoveAt(0);}
        if(motion.Stage!=lastStage) {
            lastStage=motion.Stage;Emit("stage",lastStage);
            if(lastStage=="speak"&&Flag(prefs,"voice")) {
                try {
                    if(voice==null) {
                        voice=new SpeechSynthesizer();
                        voice.SpeakCompleted+=delegate(object voiceSender,SpeakCompletedEventArgs completed) {
                            if(!completed.Cancelled)Post(delegate {if(session==voiceSession&&motion!=null)motion.SpeechHold=false;});
                        };
                    }
                    voiceSession=session;motion.SpeechHold=true;
                    voice.SpeakAsync("Come on, let's concentrate! "+Text(data,"target_app","This application")+" is currently restricted. Let's get back to your planned task.");
                } catch(Exception error){motion.SpeechHold=false;Emit("voice_unavailable",error.Message);}
            }
        }
        double root=motion.X*meters;
        // Keep the transparent 3D surface tightly bounded instead of repainting
        // an entire monitor for a small moving character.
        Position(actor,work[0]/(dpi/96)+root-robotW/2,work[1]/(dpi/96)+floor-robotH,robotW,robotH);
        Canvas.SetLeft(rig.Viewport,0);Canvas.SetTop(rig.Viewport,0);
        speech.Visibility=Visibility.Hidden;
        Canvas.SetLeft(speech,Math.Max(8,Math.Min(actor.Width-speech.Width-8,root-speech.Width*.52)));
        Canvas.SetTop(speech,Math.Max(10,floor-robotH-speech.Height+35*baseScale));
        if((double)(stamp-lastDisplayStamp)/Stopwatch.Frequency>1){lastDisplayStamp=stamp;RefreshDisplay();}
        cost.Stop();frames++;renderSeconds+=cost.Elapsed.TotalSeconds;maxFrame=Math.Max(maxFrame,cost.Elapsed.TotalMilliseconds);
    }
    void RefreshDisplay() {
        if(popup==null)return;
        var monitor=Win.MonitorFromWindow(Win.Handle(popup),2);var info=new Win.MonitorInfo {Size=Marshal.SizeOf(typeof(Win.MonitorInfo))};
        if(Win.GetMonitorInfo(monitor,ref info)) {
            uint x,y;double newDpi=dpi;try{if(Win.GetDpiForMonitor(monitor,0,out x,out y)==0)newDpi=x;}catch{}
            if(work[0]!=info.Work.Left||work[1]!=info.Work.Top||work[2]!=info.Work.Right||work[3]!=info.Work.Bottom||dpi!=newDpi){
                work=new double[]{info.Work.Left,info.Work.Top,info.Work.Right,info.Work.Bottom};dpi=newDpi;Reflow(true);
            }
        }
    }
    public void Result(long targetSession,bool verified,string message,bool dismiss) {Post(delegate {
        if(targetSession!=session||!visible)return;
        closeButton.IsEnabled=true;status.Text=message;
        if(verified&&dismiss)CloseCore();
    });}
    public void Progress(long targetSession,string message){Post(delegate {if(targetSession==session&&visible){status.Text=message;closeButton.IsEnabled=false;}});}
    public void ShowSettings(){Post(SettingsCore);}
    public void DebugStage(string stage){Post(delegate {
        if(!visible||!Flag(data,"preview"))return;
        motion=new Motion(-robotW/meters,anchor/meters,true);motion.SetStage(stage);
    });}
    public void Hide(){Post(CloseCore);}
    void CloseCore() {
        visible=false;allowClose=true;
        if(hourlyReady!=null)hourlyReady.Owner=null;
        if(layoutWatch!=null)layoutWatch.Stop();
        if(voice!=null)voice.SpeakAsyncCancelAll();
        if(subscribed){CompositionTarget.Rendering-=Frame;subscribed=false;}
        if(actorCanvas!=null)actorCanvas.Children.Clear();
        foreach(Window w in new[]{popup,actor,backdrop})if(w!=null)w.Close();
        positioned.Clear();
        popup=actor=backdrop=null;allowClose=false;
    }
    public void Shutdown(){Post(delegate{CloseCore();if(studyWindow!=null)studyWindow.Close();if(hourlyReady!=null)hourlyReady.Close();if(nightWindow!=null)nightWindow.Close();if(settings!=null)settings.Close();if(voice!=null){voice.Dispose();voice=null;}dispatcher.InvokeShutdown();});if(thread!=null&&thread!=Thread.CurrentThread)thread.Join(2000);}
    public string Metrics() {
        if(dispatcher!=null&&!dispatcher.CheckAccess()&&!dispatcher.HasShutdownStarted)
            return (string)dispatcher.Invoke(new Func<string>(Metrics));
        return json.Serialize(new{frames=frames,cpu_update_ms=frames==0?0:renderSeconds*1000/frames,max_cpu_update_ms=maxFrame,
            stage=motion==null?"hidden":motion.Stage,stages=motion==null?new string[0]:motion.Visited.ToArray(),
            render_tier=RenderCapability.Tier>>16,moving=Timing(movingIntervals),idle=Timing(idleIntervals),
            backdrop=backdropMode,backdrop_hresult=backdropHResult,visible=visible,
            robot_suspended=robotSuspended, hourly_alert_visible=hourlyReady!=null&&hourlyReady.IsVisible,
            popup_visible=popup!=null&&popup.IsVisible,actor_visible=actor!=null&&actor.IsVisible,
            popup_handle=popup==null?0:Win.Handle(popup).ToInt64(),actor_handle=actor==null?0:Win.Handle(actor).ToInt64(),
            popup_bounds=popup==null?null:new double[]{popup.Left,popup.Top,popup.Width,popup.Height},
            hourly_alert_bounds=hourlyReady==null?null:new double[]{hourlyReady.Left,hourlyReady.Top,hourlyReady.ActualWidth,hourlyReady.ActualHeight}});
    }
    static object Timing(List<double> intervals) {
        if(intervals.Count==0)return new{samples=0,measured_fps=0.0,p95_gap_ms=0.0,max_gap_ms=0.0};
        double sum=0;foreach(double value in intervals)sum+=value;
        var sorted=intervals.ToArray();Array.Sort(sorted);
        return new{samples=sorted.Length,measured_fps=sorted.Length/sum,
            p95_gap_ms=sorted[(int)((sorted.Length-1)*.95)]*1000,max_gap_ms=sorted[sorted.Length-1]*1000};
    }
}
}
