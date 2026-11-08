using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
namespace StrictBotNative {
public sealed partial class NativeHost {
    Window nightWindow;TextBlock nightClock,nightMessage,nightSafety;
    public void NightCountdown(string payload){Post(delegate{
        var item=json.Deserialize<Dictionary<string,object>>(payload);
        if(Flag(item,"dismiss")){if(nightWindow!=null)nightWindow.Close();nightWindow=null;return;}
        if(nightWindow==null){
            nightWindow=new Window{Title="StrictRecords · Night Shutdown",Width=580,Height=370,
                WindowStartupLocation=WindowStartupLocation.CenterScreen,ResizeMode=ResizeMode.NoResize,
                Background=G.B("#101923"),Foreground=Brushes.White,Topmost=true,ShowInTaskbar=true};
            var stack=new StackPanel{Margin=new Thickness(30)};
            stack.Children.Add(new TextBlock{Text="NIGHT SHUTDOWN MODE ACTIVE",FontSize=20,FontWeight=FontWeights.SemiBold,Foreground=G.B("#59dfb3")});
            nightMessage=new TextBlock{TextWrapping=TextWrapping.Wrap,FontSize=15,Margin=new Thickness(0,18,0,12)};stack.Children.Add(nightMessage);
            nightClock=new TextBlock{FontSize=48,FontWeight=FontWeights.SemiBold,HorizontalAlignment=HorizontalAlignment.Center};stack.Children.Add(nightClock);
            nightSafety=new TextBlock{FontSize=12,TextWrapping=TextWrapping.Wrap,Foreground=G.B("#b5c4d4"),Margin=new Thickness(0,16,0,0)};stack.Children.Add(nightSafety);
            nightWindow.Content=stack;nightWindow.Closed+=delegate{nightWindow=null;};nightWindow.Show();Win.Dark(nightWindow);
        }
        int seconds=(int)Number(item,"remaining_seconds",0);
        nightClock.Text=String.Format("{0:00}:{1:00}",seconds/60,seconds%60);
        nightMessage.Text=Flag(item,"records_saved")?"Your YouTube session has been saved.\nBrowsers and entertainment apps are now blocked.":"Waiting for a durable session backup. Shutdown will be blocked if saving fails.";
        if(Flag(item,"records_saved")&&!Flag(item,"video_saved"))nightMessage.Text=Flag(item,"capture_failed")?"YouTube capture was unavailable. The failure record is saved.\nNight Lock is active.":"No active YouTube video. Your Night record has been saved.\nBrowsers and entertainment apps are now blocked.";
        object failures;if(item.TryGetValue("close_failed",out failures)&&Convert.ToBoolean(failures))nightMessage.Text="Session saved. Some applications could not be closed; Night Lock is retrying.";
        nightSafety.Text=Flag(item,"controlled")?"CONTROLLED LIVE VALIDATION · Actual PC shutdown is suppressed.":"Windows shutdown is requested at the saved deadline. Unrelated unsaved applications will not be force-closed.";
    });}
}}
