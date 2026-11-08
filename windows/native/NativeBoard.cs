using System;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;
using System.Windows.Documents;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;

namespace StrictBotNative {
public sealed partial class NativeHost {
    Border boardSurface;
    FrameworkElement boardMetrics,boardPreview;
    TextBlock boardDescription,boardGuidance;
    static UIElement BoardIcon(string kind,double size,string color) {
        string path;
        switch(kind) {
            case "crown":path="M2,6 L6,10 L12,3 L18,10 L22,6 L19,19 L5,19 Z M6,22 L18,22";break;
            case "app":path="M3,3 L21,3 Q23,3 23,5 L23,21 Q23,23 21,23 L3,23 Q1,23 1,21 L1,5 Q1,3 3,3 Z M1,8 L23,8 M5,5.5 L6,5.5 M9,5.5 L10,5.5";break;
            case "shield":path="M12,1 L22,5 L21,15 Q19,21 12,25 Q5,21 3,15 L2,5 Z M12,7 L12,14 M12,18 L12,19";break;
            case "home":path="M1,11 L12,1 L23,11 M5,9 L5,23 L10,23 L10,15 L15,15 L15,23 L20,23 L20,9 M17,5 L17,2 L21,2 L21,8";break;
            case "close":path="M5,5 L19,19 M19,5 L5,19";break;
            default:path="M12,1 A11,11 0 1 1 11.99,1 M12,11 L12,19 M12,6 L12,7";break;
        }
        var shape=new System.Windows.Shapes.Path {Data=Geometry.Parse(path),Stroke=G.B(color),StrokeThickness=1.8,StrokeStartLineCap=PenLineCap.Round,StrokeEndLineCap=PenLineCap.Round,StrokeLineJoin=PenLineJoin.Round,Stretch=Stretch.Uniform};
        return new Viewbox {Child=shape,Width=size,Height=size,Stretch=Stretch.Uniform,Margin=new Thickness(2)};
    }
    static TextBlock BoardText(string text,double size,bool emphasized=false,string color="#E6EDF5") {
        return new TextBlock {Text=text,FontFamily=new FontFamily("Segoe UI"),FontSize=size,
            FontWeight=emphasized?FontWeights.SemiBold:FontWeights.Normal,Foreground=G.B(color),
            TextWrapping=TextWrapping.Wrap,VerticalAlignment=VerticalAlignment.Center};
    }
    static Border BoardPanel(UIElement child) {
        return new Border {Child=child,CornerRadius=new CornerRadius(10),BorderThickness=new Thickness(1),BorderBrush=G.B("#33516A"),Background=G.B("#142B3D"),Padding=new Thickness(14,12,14,12)};
    }
    UIElement BuildCard() {
        var root=new Grid {Margin=new Thickness(8),UseLayoutRounding=true,SnapsToDevicePixels=true};
        TextOptions.SetTextFormattingMode(root,TextFormattingMode.Display);TextOptions.SetTextRenderingMode(root,TextRenderingMode.Auto);
        boardSurface=new Border {CornerRadius=new CornerRadius(16),BorderThickness=new Thickness(1.4),BorderBrush=G.B("#53B7DF"),Background=G.B("#0C1D2D"),Padding=new Thickness(20,16,20,18),
            Effect=new DropShadowEffect {Color=Colors.Black,BlurRadius=12,ShadowDepth=3,Opacity=.25}};
        root.Children.Add(boardSurface);var layout=new Grid();boardSurface.Child=layout;
        layout.RowDefinitions.Add(new RowDefinition {Height=GridLength.Auto});layout.RowDefinitions.Add(new RowDefinition());layout.RowDefinitions.Add(new RowDefinition {Height=GridLength.Auto});
        var headerGrid=new Grid();headerGrid.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(36)});headerGrid.ColumnDefinitions.Add(new ColumnDefinition());headerGrid.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(32)});
        headerGrid.Children.Add(BoardIcon("crown",25,"#DCEAF5"));var brand=BoardText("StrictRecords PC",22,true);brand.Margin=new Thickness(8,0,0,0);Grid.SetColumn(brand,1);headerGrid.Children.Add(brand);
        var info=new Button {Content=BoardIcon("info",22,"#D7E7F3"),Background=Brushes.Transparent,BorderThickness=new Thickness(0),Cursor=Cursors.Hand,ToolTip="Restriction details"};AutomationProperties.SetName(info,"Restriction details");
        info.Click+=delegate {string details=Flag(data,"preview")?"Preview mode. No application is attached, and no access settings were changed.":Text(data,"reason","Restricted by the current access policy." )+"\n\nApplication closure must be verified before this warning is dismissed.";MessageBox.Show(popup,details,"StrictRecords — Restriction details",MessageBoxButton.OK,MessageBoxImage.Information);};Grid.SetColumn(info,2);headerGrid.Children.Add(info);
        var header=new Border {Child=headerGrid,BorderBrush=G.B("#2A4358"),BorderThickness=new Thickness(0,0,0,1),Padding=new Thickness(0,0,0,12)};layout.Children.Add(header);
        var content=new StackPanel {Margin=new Thickness(0,14,0,0)};
        var heading=BoardText("TIME TO CONCENTRATE",34,true,"#3BD2E9");heading.TextAlignment=TextAlignment.Center;heading.Margin=new Thickness(0,3,0,17);content.Children.Add(heading);
        var appRow=new Grid();appRow.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(46)});appRow.ColumnDefinitions.Add(new ColumnDefinition());appRow.Children.Add(BoardIcon("app",29,"#9DD8EC"));
        var appCopy=new StackPanel {VerticalAlignment=VerticalAlignment.Center};appCopy.Children.Add(BoardText("Restricted application",13,false,"#AEC4D5"));application=BoardText("",20,true);appCopy.Children.Add(application);
        boardDescription=BoardText("This application is currently blocked. Return to your planned task.",16,false,"#C7D7E4");boardDescription.Margin=new Thickness(0,4,0,0);appCopy.Children.Add(boardDescription);Grid.SetColumn(appCopy,1);appRow.Children.Add(appCopy);
        content.Children.Add(BoardPanel(appRow));
        var blocked=new Grid {Margin=new Thickness(0,12,0,12)};blocked.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(32)});blocked.ColumnDefinitions.Add(new ColumnDefinition());blocked.Children.Add(BoardIcon("shield",23,"#F16577"));
        status=BoardText("ENTERTAINMENT ACCESS BLOCKED",18,true,"#F16577");status.Margin=new Thickness(8,0,0,0);Grid.SetColumn(status,1);blocked.Children.Add(status);content.Children.Add(blocked);
        var metrics=new Grid();for(int i=0;i<3;i++)metrics.ColumnDefinitions.Add(new ColumnDefinition());
        string[] titles={"CURRENT POINTS","REQUIRED POINTS","ACCESS STATUS"};var values=new TextBlock[3];
        for(int i=0;i<3;i++) {
            var text=new StackPanel {Margin=new Thickness(10,0,10,0),VerticalAlignment=VerticalAlignment.Center};var label=BoardText(titles[i],13,false,"#B9CDDD");label.TextAlignment=TextAlignment.Center;text.Children.Add(label);
            values[i]=BoardText("",20,true,i==2?"#F16577":"#F0F5FA");values[i].TextAlignment=TextAlignment.Center;values[i].Margin=new Thickness(0,4,0,0);text.Children.Add(values[i]);
            var divider=new Border {Child=text,BorderBrush=G.B("#34516A"),BorderThickness=new Thickness(i==0?0:1,0,0,0)};Grid.SetColumn(divider,i);metrics.Children.Add(divider);
        }
        points=values[0];required=values[1];access=values[2];boardMetrics=BoardPanel(metrics);content.Children.Add(boardMetrics);
        var preview=BoardText("PREVIEW MODE — No application was closed and no access settings were changed.",14,true,"#B8E6F4");boardPreview=BoardPanel(preview);content.Children.Add(boardPreview);
        boardGuidance=BoardText("Access will be restored when you meet the required points or receive an eligible reward.",14,false,"#C7D7E4");boardGuidance.Margin=new Thickness(0,12,0,0);content.Children.Add(boardGuidance);
        reason=BoardText("",14,false,"#AFC6D8");reason.Margin=new Thickness(0,8,0,0);expiry=reason;content.Children.Add(reason);
        var scroll=new ScrollViewer {Content=content,VerticalScrollBarVisibility=ScrollBarVisibility.Auto,HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled,Padding=new Thickness(0,0,4,0)};
        Grid.SetRow(scroll,1);layout.Children.Add(scroll);
        var actions=new Grid {Margin=new Thickness(0,16,0,0)};actions.ColumnDefinitions.Add(new ColumnDefinition());actions.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(14)});actions.ColumnDefinitions.Add(new ColumnDefinition());
        closeButton=ActionButton("CLOSE IT","Close the restricted application",true,delegate{closeButton.IsEnabled=false;status.Text="VERIFYING APPLICATION CLOSURE…";Emit("close");});closeButton.MinHeight=76;actions.Children.Add(closeButton);
        var home=ActionButton("BACK TO HOME","Open StrictRecords",false,delegate{Emit("home");});home.MinHeight=76;Grid.SetColumn(home,2);actions.Children.Add(home);Grid.SetRow(actions,2);layout.Children.Add(actions);
        return root;
    }
    static void WalkBoard(DependencyObject node,List<TextBlock> texts,List<Button> buttons) {
        var text=node as TextBlock;if(text!=null)texts.Add(text);
        var button=node as Button;if(button!=null)buttons.Add(button);
        for(int i=0;i<VisualTreeHelper.GetChildrenCount(node);i++)WalkBoard(VisualTreeHelper.GetChild(node,i),texts,buttons);
    }
    public static string VerifyBoardLayouts(string folder) {
        Directory.CreateDirectory(folder);var reports=new List<object>();bool passed=true;
        foreach(var resolution in new[]{new[]{1366,768},new[]{1920,1080},new[]{2560,1440}})foreach(double scale in new[]{1.0,1.25,1.5,2.0})foreach(bool preview in new[]{false,true}) {
            var host=new NativeHost(null);host.data=new Dictionary<string,object>{{"preview",preview},{"target_app","A restricted media application with a longer display name"},{"score",4},{"required_score",80}};
            var root=(FrameworkElement)host.BuildCard();host.UpdateText();
            double width=Math.Min(resolution[0]/scale-24,720),available=resolution[1]/scale-64;
            root.Measure(new Size(width,double.PositiveInfinity));double height=Math.Min(available,root.DesiredSize.Height);
            root.Measure(new Size(width,height));root.Arrange(new Rect(0,0,width,height));root.UpdateLayout();
            var texts=new List<TextBlock>();var buttons=new List<Button>();WalkBoard(root,texts,buttons);
            var issues=new List<string>();
            foreach(var text in texts) {
                if(text.ActualHeight<1||text.ActualWidth<1||text.Text.Length==0)continue;
                Rect last=text.ContentEnd.GetCharacterRect(LogicalDirection.Backward);
                if(!last.IsEmpty&&last.Bottom>text.ActualHeight+1)issues.Add("Text height: "+text.Text);
                if(!last.IsEmpty&&last.Right>text.ActualWidth+2)issues.Add("Text width: "+text.Text);
            }
            foreach(var button in buttons) {
                Rect bounds=button.TransformToAncestor(root).TransformBounds(new Rect(0,0,button.ActualWidth,button.ActualHeight));
                if(bounds.Left<0||bounds.Right>width+1||bounds.Top<0||bounds.Bottom>height+1)issues.Add("Button outside window: "+AutomationProperties.GetName(button));
            }
            if(preview&&host.boardMetrics.Visibility!=Visibility.Collapsed)issues.Add("Preview must hide points");
            bool ok=issues.Count==0;passed=passed&&ok;
            string name=resolution[0]+"x"+resolution[1]+"-"+(int)(scale*100)+(preview?"-preview":"-restricted");
            var bitmap=new RenderTargetBitmap((int)Math.Ceiling(width*scale),(int)Math.Ceiling(height*scale),96*scale,96*scale,PixelFormats.Pbgra32);bitmap.Render(root);
            var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bitmap));using(var stream=File.Create(Path.Combine(folder,name+".png")))encoder.Save(stream);
            reports.Add(new{name=name,passed=ok,logical_width=width,logical_height=height,issues=issues});
        }
        return new JavaScriptSerializer().Serialize(new{passed=passed,kind="WPF native layout simulation; physical monitor settings unchanged",cases=reports});
    }
}
}
