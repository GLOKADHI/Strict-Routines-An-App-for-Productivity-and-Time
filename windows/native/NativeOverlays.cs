using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Speech.Synthesis;

namespace StrictBotNative {
public sealed partial class NativeHost {
    bool robotSuspended;
    bool hourlyAlertWanted;
    Window hourlyReady;

    // Synchronous dispatcher barrier, called from the existing Python UI worker.
    // The WebView is granted visibility only after these topmost surfaces are hidden.
    public void CoordinateOverlays(string payload) {
        Action apply=delegate {
            var state=json.Deserialize<Dictionary<string,object>>(payload);
            bool suspend=Flag(state,"ROBOT_MINIMIZED");
            hourlyAlertWanted=Flag(state,"HOURLY_ALERT_VISIBLE");
            if(robotSuspended!=suspend) {
                robotSuspended=suspend;
                if(suspend) {
                    // Hide retains HWNDs, rig, motion, entry/session and speech state.
                    foreach(Window w in new[]{popup,actor,backdrop})if(w!=null)w.Hide();
                    if(voice!=null&&voice.State==SynthesizerState.Speaking)voice.Pause();
                } else if(visible) {
                    lastTimestamp=System.Diagnostics.Stopwatch.GetTimestamp();
                    ShowRobotWithoutActivation();
                    if(voice!=null&&voice.State==SynthesizerState.Paused)voice.Resume();
                }
            }
            UpdateHourlyReady();
        };
        if(dispatcher==null||dispatcher.HasShutdownStarted)return;
        if(dispatcher.CheckAccess())apply();else dispatcher.Invoke(apply);
    }

    void ShowRobotWithoutActivation() {
        foreach(Window w in new[]{backdrop,actor,popup})if(w!=null) {
            w.ShowActivated=false;
            if(w!=actor||robotShown)w.Show();
        }
        if(backdrop!=null)Win.PassThrough(backdrop);
        if(actor!=null)Win.PassThrough(actor);
    }

    void UpdateHourlyReady() {
        if(!hourlyAlertWanted) {if(hourlyReady!=null)hourlyReady.Hide();return;}
        if(hourlyReady==null) {
            hourlyReady=Window("StrictRecords — Hourly submission ready",true,true);
            hourlyReady.ShowInTaskbar=false;hourlyReady.ShowActivated=false;
            var content=new StackPanel();
            var title=BoardText("⏰  HOURLY SUBMISSION READY",16,true,"#52D7EB");content.Children.Add(title);
            var body=BoardText("Your hourly activity submission window is ready to open.\n\nAnother desktop restriction notification is currently active.",14,false,"#C7D7E4");
            body.Margin=new Thickness(0,12,0,16);content.Children.Add(body);
            var open=ActionButton("OPEN SUBMISSION","Open the hourly activity form",false,delegate{Emit("open_submission");},"app");
            open.MinHeight=48;AutomationProperties.SetName(open,"Open Submission");content.Children.Add(open);
            hourlyReady.Content=new Border {Background=G.B("#0C1D2D"),BorderBrush=G.B("#53B7DF"),BorderThickness=new Thickness(1),
                CornerRadius=new CornerRadius(14),Padding=new Thickness(18),Child=content};
            hourlyReady.SizeToContent=SizeToContent.Height;
        }
        // Keep the notice above the dim backdrop even when a robot was created
        // after the scheduler event. Ownership establishes order without focus.
        if(popup!=null&&hourlyReady.Owner!=popup)hourlyReady.Owner=popup;
        double scale=dpi/96, width=(work[2]-work[0])/scale, height=(work[3]-work[1])/scale;
        hourlyReady.Width=Math.Max(180,Math.Min(322,width-24));
        hourlyReady.MaxHeight=Math.Max(180,height-24);
        hourlyReady.Left=work[0]/scale+12;
        hourlyReady.Top=work[1]/scale+Math.Max(12,(height-290)/2);
        if(!hourlyReady.IsVisible) {
            hourlyReady.Opacity=0;hourlyReady.Show();
            hourlyReady.BeginAnimation(UIElement.OpacityProperty,new DoubleAnimation(0,1,TimeSpan.FromMilliseconds(140)));
        }
        hourlyReady.UpdateLayout();
        hourlyReady.Top=work[1]/scale+Math.Max(12,(height-hourlyReady.ActualHeight)/2);
    }
}
}
