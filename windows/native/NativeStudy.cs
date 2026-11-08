using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace StrictBotNative {
public sealed partial class NativeHost {
    Window studyWindow;
    TextBlock studyTimer,studyPhase,studyHardware,studyMessage;
    Button studyEnd;
    string studySession;

    // A small non-activating HUD remains visible over the dedicated browser.
    // No backdrop, input interception or periodic focus grab is used here.
    public void StudyStatus(string payload) {Post(delegate {
        var data=json.Deserialize<Dictionary<string,object>>(payload);
        if(!Flag(data,"visible")) {if(studyWindow!=null)studyWindow.Hide();return;}
        studySession=Text(data,"session_id");
        if(studyWindow==null) {
            studyWindow=Window("StrictRecords — Study session",true,true);
            studyWindow.ShowInTaskbar=false;studyWindow.ShowActivated=false;
            var stack=new StackPanel();
            stack.Children.Add(BoardText("STRICT SESSION",12,true,"#53D7BC"));
            studyPhase=BoardText("STUDY",12,true,"#B3C4D7");studyPhase.Margin=new Thickness(0,8,0,0);stack.Children.Add(studyPhase);
            studyTimer=BoardText("35:00",30,true,"#EFF5FA");stack.Children.Add(studyTimer);
            studyHardware=BoardText("Camera · Bluetooth · Battery",12,false,"#B3C4D7");stack.Children.Add(studyHardware);
            studyMessage=BoardText("",12,false,"#B3C4D7");studyMessage.Margin=new Thickness(0,10,0,12);stack.Children.Add(studyMessage);
            var controls=new WrapPanel();
            var dashboard=ActionButton("SESSION DETAILS","Return to Strict Session",false,delegate{Emit("study_details",studySession);},"app");
            controls.Children.Add(dashboard);
            studyEnd=ActionButton("END SESSION","Complete after verified Android proof",true,delegate{Emit("study_complete",studySession);},"app");
            studyEnd.Margin=new Thickness(8,0,0,0);controls.Children.Add(studyEnd);stack.Children.Add(controls);
            studyWindow.Content=new Border {Background=G.B("#101D2A"),BorderBrush=G.B("#35536A"),BorderThickness=new Thickness(1),
                CornerRadius=new CornerRadius(16),Padding=new Thickness(18),Child=stack};
            studyWindow.SizeToContent=SizeToContent.Height;
        }
        studyPhase.Text=Text(data,"phase");studyTimer.Text=Text(data,"timer");
        studyHardware.Text=Text(data,"hardware");studyMessage.Text=Text(data,"message");
        studyEnd.IsEnabled=Flag(data,"end_enabled");
        double scale=dpi/96,width=(work[2]-work[0])/scale;
        studyWindow.Width=Math.Max(240,Math.Min(380,width-24));
        studyWindow.MaxHeight=Math.Max(200,(work[3]-work[1])/scale-24);
        studyWindow.Left=work[2]/scale-studyWindow.Width-12;
        studyWindow.Top=work[1]/scale+14;
        if(!studyWindow.IsVisible)studyWindow.Show();
    });}
}
}
