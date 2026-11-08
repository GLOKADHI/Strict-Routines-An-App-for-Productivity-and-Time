"""Compact native study chrome. Presentation only; never changes session state."""


def monitoring_details(status,hardware,browser_ready=False):
    def observed(key,alternate=None):
        value=hardware.get(key,status.get(key))
        if value is None and alternate:value=hardware.get(alternate,status.get(alternate))
        return 'ACTIVE' if value is True else 'OFF' if value is False else 'UNVERIFIED'
    known=hardware.get('audio_detection_known') is True
    connected=hardware.get('bluetooth_headphone_connected') is True
    wired_known=hardware.get('wired_state_known') is True
    try:left=float(hardware.get('leftward_seconds') or 0)
    except (TypeError,ValueError,OverflowError):left=0
    tracking=hardware.get('attention_monitoring')=='ACTIVE' and hardware.get('face_tracking')=='VERIFIED'
    return {
        'Webcam':str(hardware.get('camera') or 'UNVERIFIED'),
        'StrictGuard':str(hardware.get('attention_monitoring') or 'UNVERIFIED'),
        'Head direction':'LEFT' if tracking and left>0 else 'FORWARD' if tracking and hardware.get('proctor_calibrated') is True else 'UNVERIFIED',
        'Face':str(hardware.get('face_tracking') or 'UNVERIFIED'),
        'Violations':f"{hardware.get('leftward_violations','—')}/{hardware.get('max_leftward_violations',15)}",
        'Bluetooth audio':'CONNECTED' if connected else 'CLEAR' if known else 'UNVERIFIED',
        'Wired audio':'CONNECTED' if hardware.get('wired_headphone_connected') is True else 'CLEAR' if known and wired_known else 'UNVERIFIED',
        'Speaker':'MUTED' if hardware.get('speaker_muted') is True else 'NOT MUTED' if hardware.get('speaker_muted') is False else 'UNVERIFIED',
        'Screen sharing':observed('screen_sharing_active','screen_capture_active'),
        'Screen monitoring':observed('screen_proctoring_active','screen_monitoring_active'),
        'Browser allowlist':'ACTIVE' if browser_ready else 'UNVERIFIED',
        'Notifications':observed('notification_filter_active'),
    }


class FocusChrome:
    COMPACT_HEIGHT=60
    EXPANDED_HEIGHT=176

    def __init__(self,focus):
        from System.Drawing import Color,Font,FontStyle,ContentAlignment
        from System.Windows.Forms import Panel,Label,Button,ProgressBar,DockStyle,FlatStyle,Cursors,ToolTip
        self.focus=focus
        self.expanded=False
        self.tips=ToolTip()
        self.nodes={}
        self._color=Color
        header=Panel();header.Dock=DockStyle.Top;header.Height=self.COMPACT_HEIGHT
        header.BackColor=Color.FromArgb(25,26,28);self.header=header
        self.nodes['header']=header
        def label(key,text,size=9,bold=False,parent=header):
            item=Label();item.Text=text;item.AutoSize=False;item.AutoEllipsis=True
            item.UseCompatibleTextRendering=False
            item.ForeColor=Color.FromArgb(218,220,223);item.Font=Font('Segoe UI',size,FontStyle.Bold if bold else FontStyle.Regular)
            item.TextAlign=ContentAlignment.MiddleLeft;parent.Controls.Add(item);self.nodes[key]=item;return item
        label('title','StrictRecords · Strict Session',10,True)
        label('devices','PC · APK proof — · iPad —',8)
        label('timer','— / —',12,True)
        label('battery','Battery —',9)
        label('monitor','Waiting for the current monitoring report',8)
        progress=ProgressBar();progress.Minimum=0;progress.Maximum=1000;progress.Height=3;header.Controls.Add(progress);self.nodes['progress']=progress
        toggle=Button();toggle.Text='⌄';toggle.FlatStyle=FlatStyle.Flat;toggle.FlatAppearance.BorderSize=0
        toggle.UseCompatibleTextRendering=False
        toggle.ForeColor=Color.FromArgb(203,205,209);toggle.BackColor=header.BackColor;toggle.Cursor=Cursors.Hand
        toggle.AccessibleName='Expand monitoring details';toggle.Click+=lambda *_:self.toggle();header.Controls.Add(toggle);self.nodes['toggle']=toggle
        camera=Button();camera.Text='Show camera';camera.FlatStyle=FlatStyle.Flat;camera.FlatAppearance.BorderSize=0
        camera.UseCompatibleTextRendering=False
        camera.ForeColor=Color.FromArgb(203,205,209);camera.BackColor=header.BackColor;camera.Cursor=Cursors.Hand
        camera.AccessibleName='Hide or show camera preview; monitoring continues'
        camera.Click+=lambda *_:focus._handle_camera_badge_click();header.Controls.Add(camera);self.nodes['camera']=camera
        diagnostics=Panel();diagnostics.BackColor=Color.FromArgb(20,21,23);header.Controls.Add(diagnostics);self.nodes['details']=diagnostics
        for key in monitoring_details({},{}):label(key,key+': UNVERIFIED',8,parent=diagnostics)
        self.nodes['details'].Visible=False
        header.Resize+=self.arrange
        self.arrange()

    def toggle(self):
        self.expanded=not self.expanded
        self.header.Height=self.EXPANDED_HEIGHT if self.expanded else self.COMPACT_HEIGHT
        self.nodes['details'].Visible=self.expanded
        self.nodes['toggle'].Text='⌃' if self.expanded else '⌄'
        self.nodes['toggle'].AccessibleName='Collapse monitoring details' if self.expanded else 'Expand monitoring details'
        self.arrange()
        if self.focus._arrange:self.focus._arrange()

    def arrange(self,*_):
        from System.Drawing import Rectangle
        width=max(0,self.header.ClientSize.Width)
        # Fixed compact rows; secondary diagnostics do not compete for browser width.
        remaining=max(0,width-40);battery=min(110,remaining//5);timer=min(185,remaining//3)
        left=max(100,remaining-battery-timer)
        self.nodes['title'].Bounds=Rectangle(12,3,left,27)
        self.nodes['timer'].Bounds=Rectangle(left+12,0,timer,27)
        self.nodes['battery'].Bounds=Rectangle(left+timer+12,0,battery,27)
        self.nodes['toggle'].Bounds=Rectangle(max(0,width-32),2,28,26)
        self.nodes['progress'].Bounds=Rectangle(left+12,28,max(0,timer-12),3)
        self.nodes['devices'].Bounds=Rectangle(12,31,min(275,max(100,width//3)),26)
        start=self.nodes['devices'].Right+8
        self.nodes['monitor'].Bounds=Rectangle(start,31,max(0,width-start-140),26)
        self.nodes['camera'].Bounds=Rectangle(max(0,width-136),33,128,23)
        details=self.nodes['details'];details.Bounds=Rectangle(8,64,max(0,width-16),108)
        fields=list(monitoring_details({},{}));columns=4 if width>=1000 else 3 if width>=700 else 2
        rows=(len(fields)+columns-1)//columns
        self.EXPANDED_HEIGHT=68+rows*27
        if self.expanded:self.header.Height=self.EXPANDED_HEIGHT
        for index,key in enumerate(fields):
            col=index%columns;row=index//columns;cell=max(1,details.Width//columns)
            self.nodes[key].Bounds=Rectangle(col*cell+8,row*27,max(0,cell-14),25)
        details.Height=rows*27

    def update(self,status,hardware,timer_text,target,seconds):
        details=monitoring_details(status,hardware,self.focus.ready)
        green=self._color.FromArgb(91,201,143);amber=self._color.FromArgb(228,177,83)
        grey=self._color.FromArgb(149,153,160);red=self._color.FromArgb(237,108,111)
        state=str(status.get('state') or 'Waiting').replace('_',' ').title()
        demo=status.get('mode')=='DEMO'
        self.nodes['title'].Text=('Demo' if demo else 'Strict Session')+' · '+state
        self.nodes['timer'].Text=timer_text
        self.nodes['progress'].Value=max(0,min(1000,round(float(status.get('elapsed_seconds') or 0)/max(1,target)*1000)))
        percent=hardware.get('battery_percent');charging=hardware.get('charging')
        self.nodes['battery'].Text=('Battery —' if percent is None else f'{percent}%')+(' · charging' if charging is True else '')
        def connection(value):return 'Connected' if value is True else 'Offline' if value is False else 'Unverified'
        pc_state = 'Active' if status.get('windows_connected') or status.get('pc_connected') else 'Standby'; self.nodes['devices'].Text='PC: '+pc_state+' · APK proof: '+connection(status.get('android_connected'))+' · iPad: '+connection(status.get('ios_connected'))
        all_good=hardware.get('monitoring_ok') is True
        self.nodes['monitor'].Text=('Monitoring active' if all_good else 'Monitoring check required')+' · Camera '+details['Webcam']+' · '+details['Speaker']
        self.nodes['monitor'].ForeColor=green if all_good else amber
        overlay=getattr(self.focus, 'camera_overlay', None)
        hidden=bool(overlay and overlay.is_user_hidden)
        self.nodes['camera'].Text='◉ Show preview' if hidden else '◉ Hide preview'
        self.tips.SetToolTip(self.nodes['camera'],'Camera: '+details['Webcam']+'. Hiding this picture does not stop monitoring.')
        for key,value in details.items():
            self.nodes[key].Text=key+': '+value
            self.nodes[key].ForeColor=(green if value in ('ACTIVE','VERIFIED','CLEAR','MUTED','FORWARD') else
                red if value in ('ERROR','FAILED','UNAVAILABLE','NO_FRAME','MODEL_MISSING') else
                amber if value in ('OFF','STOPPED','NOT_STARTED','LEFT','CONNECTED','NOT MUTED') else grey)
            self.tips.SetToolTip(self.nodes[key],self.nodes[key].Text)
        for key in ('title','devices','monitor','battery'):self.tips.SetToolTip(self.nodes[key],self.nodes[key].Text)

    def dispose(self):self.tips.Dispose()
