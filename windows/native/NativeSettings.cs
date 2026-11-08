using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Media.Media3D;
using System.Windows.Shell;

namespace StrictBotNative {
public sealed partial class NativeHost {
    ContentControl settingsPage;
    TextBlock settingsHeading,settingsSubtitle,settingsFeedback;
    readonly List<Button> settingsNav=new List<Button>();
    int settingsSection;
    string previewStage="complete sequence";

    public static BitmapSource RenderSettingsLogo(Rig source) {
        // Render the actual character's helmet, face and crown at icon resolution.
        var scene=new Model3DGroup();
        foreach(Model3D model in source.Scene.Children)if(model is Light)scene.Children.Add(model.Clone());
        scene.Children.Add(source.CopyLogoHead());
        var view=new Viewport3D {Width=128,Height=128,Camera=new OrthographicCamera(new Point3D(0,.14,5),new Vector3D(0,0,-5),new Vector3D(0,1,0),1.55)};
        view.Children.Add(new ModelVisual3D {Content=scene});
        view.Measure(new Size(128,128));view.Arrange(new Rect(0,0,128,128));view.UpdateLayout();
        var bitmap=new RenderTargetBitmap(128,128,96,96,PixelFormats.Pbgra32);bitmap.Render(view);bitmap.Freeze();return bitmap;
    }

    static TextBlock SettingsText(string value,double size=14,bool bold=false,string color="#172337") {
        return new TextBlock {Text=value,FontSize=size,FontFamily=new FontFamily("Segoe UI"),
            Foreground=G.B(color),FontWeight=bold?FontWeights.SemiBold:FontWeights.Normal,
            TextWrapping=TextWrapping.Wrap,VerticalAlignment=VerticalAlignment.Center};
    }
    static TextBlock SettingsIcon(string value,double size=22) {
        var icon=SettingsText(value,size);icon.FontFamily=new FontFamily("Segoe MDL2 Assets");return icon;
    }
    static ResourceDictionary SettingsResources() {
        return (ResourceDictionary)XamlReader.Parse(@"
<ResourceDictionary xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml'>
 <Style TargetType='Button'>
  <Setter Property='FontFamily' Value='Segoe UI'/><Setter Property='FontSize' Value='14'/>
  <Setter Property='Foreground' Value='#172337'/><Setter Property='Background' Value='#FFFFFF'/>
  <Setter Property='BorderBrush' Value='#9DAEC5'/><Setter Property='BorderThickness' Value='1'/>
  <Setter Property='Padding' Value='18,9'/><Setter Property='Cursor' Value='Hand'/>
  <Setter Property='Template'><Setter.Value><ControlTemplate TargetType='Button'>
   <Border x:Name='surface' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='{TemplateBinding BorderThickness}' CornerRadius='6' Padding='{TemplateBinding Padding}'>
    <ContentPresenter HorizontalAlignment='{TemplateBinding HorizontalContentAlignment}' VerticalAlignment='Center'/>
   </Border><ControlTemplate.Triggers>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='surface' Property='Opacity' Value='.82'/></Trigger>
    <Trigger Property='IsPressed' Value='True'><Setter TargetName='surface' Property='Opacity' Value='.65'/></Trigger>
    <Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='surface' Property='BorderBrush' Value='#0E64E7'/><Setter TargetName='surface' Property='BorderThickness' Value='2'/></Trigger>
    <Trigger Property='IsEnabled' Value='False'><Setter TargetName='surface' Property='Opacity' Value='.45'/></Trigger>
   </ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter>
 </Style>
 <Style TargetType='ComboBox'><Setter Property='FontFamily' Value='Segoe UI'/><Setter Property='FontSize' Value='14'/>
  <Setter Property='Foreground' Value='#172337'/><Setter Property='Background' Value='White'/>
  <Setter Property='BorderBrush' Value='#B8C5D7'/><Setter Property='Padding' Value='10,5'/><Setter Property='MinHeight' Value='34'/>
  <Setter Property='HorizontalContentAlignment' Value='Left'/><Setter Property='VerticalContentAlignment' Value='Center'/>
  <Setter Property='Template'><Setter.Value><ControlTemplate TargetType='ComboBox'>
   <Grid><ToggleButton Focusable='False' IsChecked='{Binding IsDropDownOpen, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}'>
    <ToggleButton.Template><ControlTemplate TargetType='ToggleButton'><Border x:Name='outline' CornerRadius='5' BorderThickness='1' BorderBrush='#B8C5D7' Background='White'>
     <Path Data='M 0,0 L 5,5 L 10,0' Stroke='#506177' StrokeThickness='1.4' HorizontalAlignment='Right' VerticalAlignment='Center' Margin='0,0,12,0'/>
    </Border><ControlTemplate.Triggers><Trigger Property='IsMouseOver' Value='True'><Setter TargetName='outline' Property='BorderBrush' Value='#1261DE'/></Trigger></ControlTemplate.Triggers></ControlTemplate></ToggleButton.Template>
   </ToggleButton><ContentPresenter Margin='12,5,34,5' VerticalAlignment='Center' IsHitTestVisible='False' Content='{TemplateBinding SelectionBoxItem}' ContentTemplate='{TemplateBinding SelectionBoxItemTemplate}'/>
   <Popup x:Name='PART_Popup' Placement='Bottom' IsOpen='{TemplateBinding IsDropDownOpen}' AllowsTransparency='True' Focusable='False' PopupAnimation='Fade'>
    <Border MinWidth='{Binding ActualWidth, RelativeSource={RelativeSource TemplatedParent}}' MaxHeight='280' Background='White' BorderBrush='#B8C5D7' BorderThickness='1' CornerRadius='5' Padding='4' Margin='0,3,0,0'>
     <ScrollViewer CanContentScroll='True'><ItemsPresenter KeyboardNavigation.DirectionalNavigation='Contained'/></ScrollViewer>
    </Border></Popup></Grid>
   <ControlTemplate.Triggers><Trigger Property='IsKeyboardFocusWithin' Value='True'><Setter Property='BorderBrush' Value='#1261DE'/></Trigger></ControlTemplate.Triggers>
  </ControlTemplate></Setter.Value></Setter>
 </Style>
 <Style TargetType='ComboBoxItem'><Setter Property='Padding' Value='9,7'/><Setter Property='HorizontalContentAlignment' Value='Stretch'/><Setter Property='Template'><Setter.Value>
  <ControlTemplate TargetType='ComboBoxItem'><Border x:Name='item' CornerRadius='3' Background='White' Padding='{TemplateBinding Padding}'><ContentPresenter/></Border>
   <ControlTemplate.Triggers><Trigger Property='IsHighlighted' Value='True'><Setter TargetName='item' Property='Background' Value='#E7F0FF'/></Trigger><Trigger Property='IsSelected' Value='True'><Setter TargetName='item' Property='Background' Value='#D0E1FF'/></Trigger></ControlTemplate.Triggers>
  </ControlTemplate></Setter.Value></Setter></Style>
 <Style TargetType='CheckBox'><Setter Property='FontFamily' Value='Segoe UI'/><Setter Property='FontSize' Value='14'/>
  <Setter Property='Foreground' Value='#172337'/><Setter Property='VerticalContentAlignment' Value='Center'/>
  <Setter Property='Template'><Setter.Value><ControlTemplate TargetType='CheckBox'>
   <Grid Background='Transparent'><Grid.ColumnDefinitions><ColumnDefinition Width='22'/><ColumnDefinition Width='*'/></Grid.ColumnDefinitions>
    <Border x:Name='box' Width='18' Height='18' BorderThickness='1' BorderBrush='#8696AC' Background='White' CornerRadius='3'/>
    <Path x:Name='tick' Data='M 4,9 L 8,13 L 15,5' Stroke='White' StrokeThickness='2' Visibility='Collapsed' Margin='1,0,0,0' VerticalAlignment='Center'/>
    <ContentPresenter Grid.Column='1' Margin='10,0,0,0' VerticalAlignment='Center'/>
   </Grid><ControlTemplate.Triggers>
    <Trigger Property='IsChecked' Value='True'><Setter TargetName='box' Property='Background' Value='#1261DE'/><Setter TargetName='box' Property='BorderBrush' Value='#1261DE'/><Setter TargetName='tick' Property='Visibility' Value='Visible'/></Trigger>
    <Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='box' Property='BorderThickness' Value='2'/><Setter TargetName='box' Property='BorderBrush' Value='#1261DE'/></Trigger>
   </ControlTemplate.Triggers></ControlTemplate></Setter.Value></Setter>
 </Style>
 <Style TargetType='Slider'><Setter Property='MinHeight' Value='30'/><Setter Property='VerticalAlignment' Value='Center'/>
  <Setter Property='Template'><Setter.Value><ControlTemplate TargetType='Slider'>
   <Grid Margin='9,0'><Track x:Name='PART_Track' VerticalAlignment='Center'>
    <Track.DecreaseRepeatButton><RepeatButton Command='Slider.DecreaseLarge' Focusable='False'><RepeatButton.Template><ControlTemplate><Border Height='5' CornerRadius='2.5' Background='#1261DE'/></ControlTemplate></RepeatButton.Template></RepeatButton></Track.DecreaseRepeatButton>
    <Track.Thumb><Thumb Width='21' Height='21'><Thumb.Template><ControlTemplate><Ellipse x:Name='knob' Fill='White' Stroke='#597FAB' StrokeThickness='1.2'/><ControlTemplate.Triggers><Trigger Property='IsMouseOver' Value='True'><Setter TargetName='knob' Property='Stroke' Value='#1261DE'/><Setter TargetName='knob' Property='StrokeThickness' Value='2'/></Trigger></ControlTemplate.Triggers></ControlTemplate></Thumb.Template></Thumb></Track.Thumb>
    <Track.IncreaseRepeatButton><RepeatButton Command='Slider.IncreaseLarge' Focusable='False'><RepeatButton.Template><ControlTemplate><Border Height='5' CornerRadius='2.5' Background='#C3CBD6'/></ControlTemplate></RepeatButton.Template></RepeatButton></Track.IncreaseRepeatButton>
   </Track></Grid><ControlTemplate.Triggers><Trigger Property='IsKeyboardFocused' Value='True'><Setter Property='Opacity' Value='.75'/></Trigger></ControlTemplate.Triggers>
  </ControlTemplate></Setter.Value></Setter></Style>
</ResourceDictionary>");
    }
    void SettingsCore() {
        if(settings!=null){if(settings.WindowState==WindowState.Minimized)settings.WindowState=WindowState.Normal;settings.Activate();return;}
        double availableW=(work[2]-work[0])*96/dpi,availableH=(work[3]-work[1])*96/dpi;
        settings=new Window {Title="StrictBot PC – Settings",Width=Math.Min(1040,availableW-32),Height=Math.Min(1000,availableH-32),
            MinWidth=Math.Min(760,availableW-32),MinHeight=Math.Min(540,availableH-32),WindowStyle=WindowStyle.None,
            ResizeMode=ResizeMode.CanResize,Background=G.B("#F6F8FC"),Foreground=G.B("#172337"),
            WindowStartupLocation=WindowStartupLocation.CenterScreen,FontFamily=new FontFamily("Segoe UI")};
        WindowChrome.SetWindowChrome(settings,new WindowChrome {CaptionHeight=52,ResizeBorderThickness=new Thickness(6),CornerRadius=new CornerRadius(10),GlassFrameThickness=new Thickness(0),UseAeroCaptionButtons=false});
        var shell=new Grid();shell.Resources=SettingsResources();
        shell.RowDefinitions.Add(new RowDefinition {Height=new GridLength(52)});shell.RowDefinitions.Add(new RowDefinition());
        settings.Content=new Border {BorderBrush=G.B("#7D8B9E"),BorderThickness=new Thickness(1),Child=shell};
        var title=new Grid {Background=new LinearGradientBrush(G.C("#173D5D"),G.C("#102C46"),0)};
        title.ColumnDefinitions.Add(new ColumnDefinition());title.ColumnDefinitions.Add(new ColumnDefinition {Width=GridLength.Auto});shell.Children.Add(title);
        var caption=new StackPanel {Orientation=Orientation.Horizontal,Margin=new Thickness(20,0,0,0)};
        var logo=RenderSettingsLogo(rig);settings.Icon=logo;
        var badge=new Image {Source=logo,Width=34,Height=34,Margin=new Thickness(0,0,12,0),Stretch=Stretch.Uniform};
        RenderOptions.SetBitmapScalingMode(badge,BitmapScalingMode.HighQuality);
        caption.Children.Add(badge);caption.Children.Add(SettingsText("StrictBot PC – Settings",19,true,"#FFFFFF"));title.Children.Add(caption);
        var chrome=new StackPanel {Orientation=Orientation.Horizontal};Grid.SetColumn(chrome,1);title.Children.Add(chrome);
        string[] glyphs={"\uE921","\uE922","\uE8BB"};
        for(int i=0;i<3;i++) {
            int command=i;var button=new Button {Content=SettingsIcon(glyphs[i],16),Width=48,Padding=new Thickness(0),Background=Brushes.Transparent,BorderThickness=new Thickness(0)};
            ((TextBlock)button.Content).Foreground=Brushes.White;WindowChrome.SetIsHitTestVisibleInChrome(button,true);
            AutomationProperties.SetName(button,i==0?"Minimize":i==1?"Maximize or restore":"Close settings");
            button.Click+=delegate{if(command==0)settings.WindowState=WindowState.Minimized;else if(command==1)settings.WindowState=settings.WindowState==WindowState.Maximized?WindowState.Normal:WindowState.Maximized;else settings.Close();};chrome.Children.Add(button);
        }
        var body=new Grid();Grid.SetRow(body,1);shell.Children.Add(body);
        body.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(218)});body.ColumnDefinitions.Add(new ColumnDefinition());
        var sidebar=new Border {Background=G.B("#F1F5FB"),BorderBrush=G.B("#D8E0EB"),BorderThickness=new Thickness(0,0,1,0)};
        var navigation=new StackPanel {Margin=new Thickness(12,28,12,0)};sidebar.Child=navigation;body.Children.Add(sidebar);
        string[] names={"General","Animation","Popup & Display","Monitoring","About"};string[] icons={"\uE713","\uE768","\uE737","\uE9D9","\uE946"};
        settingsNav.Clear();
        for(int i=0;i<names.Length;i++) {
            int index=i;var row=new StackPanel {Orientation=Orientation.Horizontal};var icon=SettingsIcon(icons[i]);icon.Width=36;row.Children.Add(icon);row.Children.Add(SettingsText(names[i],16));
            var item=new Button {Content=row,HorizontalContentAlignment=HorizontalAlignment.Left,BorderThickness=new Thickness(0),Padding=new Thickness(12,14,8,14),Margin=new Thickness(0,3,0,3)};
            AutomationProperties.SetName(item,names[i]);item.Click+=delegate{ShowSettingsSection(index);};navigation.Children.Add(item);settingsNav.Add(item);
        }
        var right=new Grid();Grid.SetColumn(right,1);body.Children.Add(right);
        right.RowDefinitions.Add(new RowDefinition());right.RowDefinitions.Add(new RowDefinition {Height=GridLength.Auto});
        var content=new StackPanel {Margin=new Thickness(26,30,26,22)};
        settingsHeading=SettingsText("",28,true,"#101820");settingsSubtitle=SettingsText("",15,false,"#344459");settingsSubtitle.Margin=new Thickness(0,4,0,26);
        content.Children.Add(settingsHeading);content.Children.Add(settingsSubtitle);settingsPage=new ContentControl();content.Children.Add(settingsPage);
        right.Children.Add(new ScrollViewer {Content=content,VerticalScrollBarVisibility=ScrollBarVisibility.Auto,HorizontalScrollBarVisibility=ScrollBarVisibility.Disabled});
        var footer=new Grid {Margin=new Thickness(26,0,26,0)};footer.ColumnDefinitions.Add(new ColumnDefinition {Width=GridLength.Auto});footer.ColumnDefinitions.Add(new ColumnDefinition());footer.ColumnDefinitions.Add(new ColumnDefinition {Width=GridLength.Auto});
        var reset=new Button {Content="Reset to Default",MinWidth=170,Height=42,Margin=new Thickness(0,16,0,16)};
        reset.Click+=delegate {ResetSettingsDraft();};footer.Children.Add(reset);
        settingsFeedback=SettingsText("",12,false,"#567087");settingsFeedback.Margin=new Thickness(16,0,12,0);Grid.SetColumn(settingsFeedback,1);footer.Children.Add(settingsFeedback);
        var save=new Button {Content="Save Settings",MinWidth=174,Height=42,FontWeight=FontWeights.SemiBold,Foreground=Brushes.White,Background=G.B("#0B61DC"),BorderBrush=G.B("#0B61DC")};
        save.Click+=delegate{settingsFeedback.Text="Saving…";Emit("save_settings",prefs);};Grid.SetColumn(save,2);footer.Children.Add(save);
        var footerBorder=new Border {Background=G.B("#F6F8FC"),BorderBrush=G.B("#D8E0EB"),BorderThickness=new Thickness(0,1,0,0),Child=footer};Grid.SetRow(footerBorder,1);right.Children.Add(footerBorder);
        settingsSection=0;ShowSettingsSection(0);
        settings.Closed+=delegate{settings=null;settingsPage=null;settingsFeedback=null;settingsNav.Clear();Emit("settings_closed");};
        settings.SourceInitialized+=delegate {int dark=0;Win.DwmSetWindowAttribute(Win.Handle(settings),20,ref dark,4);int corner=2;Win.DwmSetWindowAttribute(Win.Handle(settings),33,ref corner,4);};
        settings.Show();
    }
    void MarkSettingsChanged() {
        if(settingsFeedback!=null){settingsFeedback.Text="Unsaved changes";settingsFeedback.Foreground=G.B("#567087");}
        if(visible)Reflow(true);
    }
    public void SettingsSaved(bool success,string message) {Post(delegate {
        if(settingsFeedback==null)return;
        settingsFeedback.Text=message;settingsFeedback.Foreground=G.B(success?"#16734F":"#B52A3B");
    });}
    void ResetSettingsDraft() {
        prefs["robot_size"]=100;prefs["popup_size"]=100;prefs["dim"]=22;prefs["transparency"]=96;
        prefs["robot_position"]="left";prefs["popup_position"]="center";prefs["speed"]="normal";
        prefs["monitor"]="application";prefs["walking"]=true;prefs["voice"]=false;
        ShowSettingsSection(settingsSection);MarkSettingsChanged();settingsFeedback.Text="Defaults restored. Save to keep.";
    }
    Border SettingsCard(string title,string icon,out StackPanel fields) {
        var card=new Grid();card.RowDefinitions.Add(new RowDefinition {Height=GridLength.Auto});card.RowDefinitions.Add(new RowDefinition());
        var heading=new StackPanel {Orientation=Orientation.Horizontal,Margin=new Thickness(18,14,18,14)};
        var glyph=SettingsIcon(icon,23);glyph.Margin=new Thickness(0,0,14,0);heading.Children.Add(glyph);heading.Children.Add(SettingsText(title,18,true));card.Children.Add(heading);
        fields=new StackPanel {Margin=new Thickness(18,12,18,17)};
        var section=new Border {Child=fields,BorderBrush=G.B("#D6DEEA"),BorderThickness=new Thickness(0,1,0,0)};Grid.SetRow(section,1);card.Children.Add(section);
        return new Border {Child=card,CornerRadius=new CornerRadius(10),BorderBrush=G.B("#C8D4E4"),BorderThickness=new Thickness(1),Background=new LinearGradientBrush(G.C("#FFFFFF"),G.C("#F8FAFD"),90),Margin=new Thickness(0,0,0,16)};
    }
    void SettingsSlider(Panel panel,string name,string key,double min,double max,double fallback) {
        var row=new Grid {Margin=new Thickness(0,5,0,5)};row.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(170)});row.ColumnDefinitions.Add(new ColumnDefinition());row.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(66)});
        row.Children.Add(SettingsText(name+":",15));
        var value=SettingsText(Math.Round(Number(prefs,key,fallback))+"%",15);value.HorizontalAlignment=HorizontalAlignment.Right;Grid.SetColumn(value,2);row.Children.Add(value);
        var slider=new Slider {Minimum=min,Maximum=max,Value=Number(prefs,key,fallback),IsSnapToTickEnabled=true,TickFrequency=1,SmallChange=1,LargeChange=5,Margin=new Thickness(0,0,12,0)};
        AutomationProperties.SetName(slider,name);Grid.SetColumn(slider,1);row.Children.Add(slider);
        slider.ValueChanged+=delegate{prefs[key]=slider.Value;value.Text=Math.Round(slider.Value)+"%";MarkSettingsChanged();};panel.Children.Add(row);
    }
    void SettingsCombo(Panel panel,string name,string key,string[] values,string[] titles) {
        var row=new Grid {Margin=new Thickness(0,5,0,5)};row.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(190)});row.ColumnDefinitions.Add(new ColumnDefinition());
        row.Children.Add(SettingsText(name+":",15));var combo=new ComboBox {MaxWidth=340,HorizontalAlignment=HorizontalAlignment.Stretch};
        for(int i=0;i<values.Length;i++)combo.Items.Add(titles[i]);
        int selected=Array.IndexOf(values,Text(prefs,key,values[0]));combo.SelectedIndex=selected<0?0:selected;
        AutomationProperties.SetName(combo,name);Grid.SetColumn(combo,1);row.Children.Add(combo);
        combo.SelectionChanged+=delegate{if(combo.SelectedIndex>=0){prefs[key]=values[combo.SelectedIndex];MarkSettingsChanged();}};panel.Children.Add(row);
    }
    void SettingsCheck(Panel panel,string name,string key,bool fallback) {
        var check=new CheckBox {Content=name,IsChecked=Flag(prefs,key,fallback),Margin=new Thickness(0,8,0,4)};
        check.Checked+=delegate{prefs[key]=true;MarkSettingsChanged();};check.Unchecked+=delegate{prefs[key]=false;MarkSettingsChanged();};panel.Children.Add(check);
    }
    void AddSettingsSize(Panel page) {
        StackPanel fields;page.Children.Add(SettingsCard("Size & Display","\uE7F4",out fields));
        SettingsSlider(fields,"Robot Size","robot_size",40,160,100);SettingsSlider(fields,"Popup Size","popup_size",60,150,100);
        SettingsSlider(fields,"Desktop Dim","dim",0,65,22);SettingsSlider(fields,"Popup Opacity","transparency",65,100,96);
    }
    void AddSettingsBehavior(Panel page,bool general) {
        StackPanel fields;page.Children.Add(SettingsCard(general?"Position & Behavior":"Animation & Voice",general?"\uE81D":"\uE768",out fields));
        if(general) {
            SettingsCombo(fields,"Robot Position","robot_position",new[]{"left","right","automatic"},new[]{"Left","Right","Automatic"});
            SettingsCombo(fields,"Popup Position","popup_position",new[]{"center","upper-center"},new[]{"Center","Upper Center"});
        }
        SettingsCombo(fields,"Animation Speed","speed",new[]{"slow","normal","fast"},new[]{"Slow","Normal","Fast"});
        SettingsCheck(fields,"Show walking animation on new warnings","walking",true);SettingsCheck(fields,"Play voice message on new warnings","voice",false);
    }
    void AddSettingsPreview(Panel page,bool stages) {
        StackPanel fields;page.Children.Add(SettingsCard("Preview","\uE7F4",out fields));
        if(stages) {
            var stageCombo=new ComboBox {Margin=new Thickness(0,0,0,14)};stageCombo.Items.Add("Complete sequence");foreach(string stage in Motion.Stages)stageCombo.Items.Add(stage.Replace('_',' '));
            stageCombo.SelectedIndex=previewStage=="complete sequence"?0:Array.IndexOf(Motion.Stages,previewStage)+1;
            AutomationProperties.SetName(stageCombo,"Preview stage");stageCombo.SelectionChanged+=delegate{previewStage=stageCombo.SelectedIndex==0?"complete sequence":Motion.Stages[stageCombo.SelectedIndex-1];};fields.Children.Add(stageCombo);
        }
        var row=new Grid();row.ColumnDefinitions.Add(new ColumnDefinition());row.ColumnDefinitions.Add(new ColumnDefinition {Width=GridLength.Auto});
        var hint=SettingsText("See how StrictBot and the popup will look with current settings.",14);hint.Margin=new Thickness(0,0,18,0);row.Children.Add(hint);
        var label=new StackPanel {Orientation=Orientation.Horizontal};var play=SettingsIcon("\uE768",15);play.Margin=new Thickness(0,0,9,0);label.Children.Add(play);label.Children.Add(SettingsText("Preview Animation",14));
        var button=new Button {Content=label,Padding=new Thickness(14,10,14,10)};AutomationProperties.SetName(button,"Preview Animation");
        button.Click+=delegate {
            if(visible&&!Flag(data,"preview")){settingsFeedback.Text="Finish the active restriction before previewing.";return;}
            var values=new Dictionary<string,object>{{"session",-1},{"preview",true},{"robot_preview",stages},{"target_app","StrictBot Preview"},{"preferences",prefs},{"work",work},{"dpi",dpi}};
            PresentCore(values);if(previewStage!="complete sequence")motion.SetStage(previewStage);settings.WindowState=WindowState.Minimized;
        };Grid.SetColumn(button,1);row.Children.Add(button);fields.Children.Add(row);
    }
    void ShowSettingsSection(int index) {
        settingsSection=index;
        string[] titles={"General Settings","Animation Settings","Popup & Display","Monitoring","About StrictBot"};
        string[] subtitles={"Customize StrictBot appearance and behavior","Make StrictBot move and speak your way","Choose where and how warnings appear","Choose the display used for native warnings","Your desktop focus companion"};
        settingsHeading.Text=titles[index];settingsSubtitle.Text=subtitles[index];
        for(int i=0;i<settingsNav.Count;i++)settingsNav[i].Background=G.B(i==index?"#D0E1FF":"#F1F5FB");
        var page=new StackPanel();settingsPage.Content=page;
        if(index==0){AddSettingsSize(page);AddSettingsBehavior(page,true);AddSettingsPreview(page,false);}
        else if(index==1){AddSettingsBehavior(page,false);AddSettingsPreview(page,true);}
        else if(index==2) {
            AddSettingsSize(page);StackPanel fields;page.Children.Add(SettingsCard("Position","\uE81D",out fields));
            SettingsCombo(fields,"Robot Position","robot_position",new[]{"left","right","automatic"},new[]{"Left","Right","Automatic"});
            SettingsCombo(fields,"Popup Position","popup_position",new[]{"center","upper-center"},new[]{"Center","Upper Center"});AddSettingsPreview(page,false);
        } else if(index==3) {
            StackPanel fields;page.Children.Add(SettingsCard("Display Selection","\uE7F4",out fields));
            SettingsCombo(fields,"Warning Monitor","monitor",new[]{"application","primary","cursor"},new[]{"Restricted application's display","Primary display","Display under cursor"});
            var help=SettingsText("The selected display is used for new warnings. Save Settings to keep your preference.",14,false,"#52667D");help.Margin=new Thickness(0,14,0,0);fields.Children.Add(help);
            StackPanel info;page.Children.Add(SettingsCard("App Monitoring","\uE9D9",out info));info.Children.Add(SettingsText("StrictBot uses the restrictions already configured in StrictRecords. Appearance settings do not change points or unlock access.",14,false,"#52667D"));
        } else {
            StackPanel info;page.Children.Add(SettingsCard("StrictBot PC","\uE946",out info));info.Children.Add(SettingsText("A little encouragement to get back to your planned tasks.",16,true));
            var detail=SettingsText("Customize your robot, preview its animation, and choose how focus reminders appear on your desktop.",14,false,"#52667D");detail.Margin=new Thickness(0,14,0,16);info.Children.Add(detail);
            info.Children.Add(SettingsText("Part of StrictRecords for Windows",13,false,"#52667D"));AddSettingsPreview(page,false);
        }
    }
}
}
