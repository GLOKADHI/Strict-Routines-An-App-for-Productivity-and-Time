"""Native browser toolbar and loading/offline/error surfaces; no session authority."""
import time


def toolbar_geometry(width):
    width=max(0,int(width))
    return dict(tab=(8,4,106,28),back=(120,5,26,26),forward=(148,5,26,26),reload=(176,5,26,26),
                address=(10,36,max(0,width-20),23))


def load_error_message(error):
    value=str(error)
    if 'Disconnected' in value or 'NameNotResolved' in value:return 'Check your internet connection and try again.'
    if 'Certificate' in value:return 'The site’s secure connection could not be verified. Check the connection and system clock, then retry.'
    if 'Timeout' in value:return 'The website is taking longer than expected. Check the connection, then retry.'
    return 'The approved website could not finish loading. You can retry without leaving your session.'


def placeholder_geometry(width,height,state='offline'):
    """One compact recovery card; never consumes or clips the browser toolbar."""
    width=max(0,int(width));height=max(0,int(height))
    card_width=max(0,min(340 if state=='offline' else 440,width-32));card_height=180 if state=='offline' else 280 if state=='error' else 244
    # Short viewports scroll the placeholder instead of overlapping Retry/text.
    return ((width-card_width)//2,max(12,(height-card_height)//2),card_width,card_height)


class StudyBrowserPane:
    def __init__(self,key,title,url,view,select,swap,mode,*,offline=False):
        from System.Drawing import Color,Font,FontStyle,Rectangle,ContentAlignment
        from System.Windows.Forms import Panel,Label,Button,DockStyle,FlatStyle,ToolTip,Cursors,Timer,BorderStyle,ProgressBar,ProgressBarStyle
        self.key,self.title,self.url,self.view=key,title,url,view
        self.offline=offline;self.state='offline' if offline else 'loading';self.retry_url=url
        self._navigation_id=None;self._ignored_navigation_ids=set();self._had_ready_page=False;self._load_started=None
        self.tips=ToolTip();self._color=Color;self._rect=Rectangle;self._disposed=False
        self._border_style=BorderStyle
        host=Panel();host.BackColor=Color.FromArgb(22,24,27);self.host=host
        toolbar=Panel();toolbar.Height=64;toolbar.Dock=DockStyle.Top;toolbar.BackColor=Color.FromArgb(35,38,42);self.toolbar=toolbar
        content=Panel();content.Dock=DockStyle.Fill;content.BackColor=host.BackColor;self.content=content
        view.Dock=DockStyle.Fill;content.Controls.Add(view)
        surface=Panel();surface.Dock=DockStyle.Fill;surface.BackColor=Color.FromArgb(22,24,27);surface.AutoScroll=True;self.surface=surface
        content.Controls.Add(surface);surface.BringToFront()
        card=Panel();card.BackColor=Color.FromArgb(31,34,38);card.BorderStyle=BorderStyle.FixedSingle;surface.Controls.Add(card);self.card=card
        host.Controls.Add(content);host.Controls.Add(toolbar)
        self.buttons={}
        def button(name,text,hint,action):
            item=Button();item.Text=text;item.FlatStyle=FlatStyle.Flat;item.FlatAppearance.BorderSize=0
            item.UseCompatibleTextRendering=False
            item.BackColor=Color.FromArgb(43,47,52);item.ForeColor=Color.FromArgb(218,223,228)
            item.Font=Font('Segoe UI',9);item.Cursor=Cursors.Hand;item.AccessibleName=hint
            item.Click+=action;toolbar.Controls.Add(item);self.tips.SetToolTip(item,hint);self.buttons[name]=item;return item
        self.tab_button=button('tab',title,'Select '+title,lambda *_:select(key))
        def navigate(operation):
            core=view.CoreWebView2
            if core is None or offline:return
            if operation=='back' and core.CanGoBack:core.GoBack()
            elif operation=='forward' and core.CanGoForward:core.GoForward()
            elif operation=='reload':self.retry()
        button('back','‹','Back',lambda *_:navigate('back'))
        button('forward','›','Forward',lambda *_:navigate('forward'))
        button('reload','↻','Reload approved website',lambda *_:navigate('reload'))
        address=Label();address.ForeColor=Color.FromArgb(180,189,197);address.BackColor=Color.FromArgb(25,28,31)
        address.UseCompatibleTextRendering=False
        address.Font=Font('Segoe UI',8.5);address.AutoEllipsis=True;address.TextAlign=ContentAlignment.MiddleLeft
        address.Text='  '+url;address.AccessibleName=title+' approved website address';toolbar.Controls.Add(address);self.address=address
        self.tips.SetToolTip(address,url)
        self.nodes={}
        def text(name,value,size,bold=False):
            label=Label();label.Text=value;label.Font=Font('Segoe UI',size,FontStyle.Bold if bold else FontStyle.Regular)
            label.UseCompatibleTextRendering=False
            label.ForeColor=Color.FromArgb(220,225,230);label.TextAlign=ContentAlignment.MiddleCenter
            label.AutoSize=False;card.Controls.Add(label);self.nodes[name]=label;return label
        text('icon','◇' if key=='chatgpt' else '</>',11.5,True).ForeColor=Color.FromArgb(163,179,169)
        text('title',title,12.5,True);text('url',url,9).ForeColor=Color.FromArgb(162,175,184)
        text('badge','OFFLINE DEMO',8,True).ForeColor=Color.FromArgb(206,179,129)
        text('message','',10);text('detail','',8.5).ForeColor=Color.FromArgb(155,164,173)
        retry=Button();retry.Text='↻  Retry '+title;retry.Font=Font('Segoe UI',9,FontStyle.Bold)
        retry.UseCompatibleTextRendering=False
        retry.ForeColor=Color.FromArgb(207,229,215);retry.BackColor=Color.FromArgb(35,54,43)
        retry.FlatStyle=FlatStyle.Flat;retry.FlatAppearance.BorderColor=Color.FromArgb(68,89,76)
        retry.Cursor=Cursors.Hand;retry.Click+=lambda *_:self.retry();card.Controls.Add(retry);self.retry_button=retry
        loading=ProgressBar();loading.Style=ProgressBarStyle.Marquee;loading.MarqueeAnimationSpeed=30;card.Controls.Add(loading);self.loading_indicator=loading
        timer=Timer();timer.Interval=15000;self.timer=timer
        timer.Tick+=lambda *_:self.on_timeout()
        toolbar.Resize+=self.arrange;surface.Resize+=self.arrange
        self.show_state('offline' if offline else 'loading');self.arrange()

    def arrange(self,*_):
        layout=toolbar_geometry(self.toolbar.ClientSize.Width)
        for name,control in self.buttons.items():control.Bounds=self._rect(*layout[name])
        self.address.Bounds=self._rect(*layout['address'])
        self.card.Bounds=self._rect(*placeholder_geometry(self.surface.ClientSize.Width,self.surface.ClientSize.Height,self.state))
        width=self.card.ClientSize.Width;height=self.card.ClientSize.Height;inner=max(0,width-28);left=14
        if self.state=='offline':
            self.card.BorderStyle=getattr(self._border_style,'None')
            for name,y,h in [('icon',6,30),('title',36,36),('url',74,26),('badge',104,24),('message',140,26)]:
                self.nodes[name].Bounds=self._rect(left,y,inner,h);self.nodes[name].Visible=True
            self.nodes['detail'].Visible=False;self.retry_button.Visible=False;self.loading_indicator.Visible=False
            return
        self.card.BorderStyle=self._border_style.FixedSingle
        compact=height<230;self.nodes['icon'].Visible=not compact
        positions=[('title',8,30),('url',40,23),('badge',68,22),('message',101,52),('detail',157,35)] if compact else [('icon',10,32),('title',44,32),('url',78,23),('badge',107,22),('message',143,48),('detail',195,34)]
        if self.state=='error':self.nodes['detail'].Visible=False
        else:self.nodes['detail'].Visible=True
        for name,y,h in positions:self.nodes[name].Bounds=self._rect(left,y,inner,h)
        self.retry_button.Bounds=self._rect(max(0,(width-180)//2),max(0,height-42),min(180,width),30)
        self.loading_indicator.Bounds=self._rect(28,135,max(0,width-56),3)

    def set_split(self,value):
        self.split=value

    def show_state(self,state,message=None):
        if self._disposed:return
        self.timer.Stop();self.state=state;self._load_started=time.monotonic() if state=='loading' else None
        if state=='ready':
            self.surface.Visible=False;self.view.Visible=True;return
        self.view.Visible=False;self.surface.Visible=True;self.surface.BringToFront()
        self.nodes['badge'].Text={'offline':'OFFLINE DEMO','loading':'CONNECTING','error':'CONNECTION NEEDS ATTENTION'}[state]
        self.nodes['title'].Text=self.title if state=='offline' else 'Loading '+self.title if state=='loading' else 'Unable to load '+self.title
        self.nodes['message'].Text=message or ('Live content unavailable in demo.' if state=='offline' else 'Opening your approved study website…')
        self.nodes['message'].AccessibleName='Live website content is unavailable in this offline preview; this is not an authenticated website.' if state=='offline' else self.nodes['message'].Text
        self.nodes['detail'].Text='This is a layout preview, not an authenticated website.' if state=='offline' else 'Your session and the other browser pane stay open.'
        self.retry_button.Visible=state=='error'
        self.loading_indicator.Visible=state=='loading'
        for name in ('back','forward','reload'):self.buttons[name].Enabled=not self.offline
        if state=='loading':self.timer.Start()
        self.arrange()

    def attach_navigation(self,allow):
        core=self.view.CoreWebView2
        def starting(sender,args):
            uri=str(args.Uri)
            if self.offline or self._disposed:return
            identity=getattr(args,'NavigationId',None)
            if getattr(args,'Cancel',False) or not allow(uri):
                self._ignored_navigation_ids.add(identity)
                if identity==self._navigation_id:
                    self.show_state('ready' if self._had_ready_page else 'error','Navigation outside the approved websites was blocked.')
                return
            self._navigation_id=identity;self.retry_url=uri;self.show_state('loading')
        def completed(sender,args):
            if self.offline or self._disposed:return
            identity=getattr(args,'NavigationId',None)
            if identity in self._ignored_navigation_ids:
                self._ignored_navigation_ids.discard(identity);return
            if identity!=self._navigation_id:return
            if args.IsSuccess:self._had_ready_page=True
            self.show_state('ready' if args.IsSuccess else 'error',None if args.IsSuccess else load_error_message(args.WebErrorStatus))
        def source_changed(sender,args):
            if self.offline:return
            self.address.Text='  '+str(core.Source);self.tips.SetToolTip(self.address,str(core.Source))
        core.NavigationStarting+=starting;core.NavigationCompleted+=completed;core.SourceChanged+=source_changed
        def failed(*_):
            if not self.offline:self.show_state('error','The embedded browser stopped responding. Retry the website to reconnect.')
        core.ProcessFailed+=failed
        self._events=(starting,completed,source_changed,failed)

    def on_timeout(self):
        if self.state=='loading' and self._load_started is not None and time.monotonic()-self._load_started>=15:
            self.show_state('error','The website is taking longer than expected. Check your connection, then retry.')

    def retry(self):
        if self.offline or self.view.CoreWebView2 is None:return
        self.show_state('loading')
        try:self.view.CoreWebView2.Navigate(self.retry_url)
        except Exception:self.show_state('error','The browser could not reconnect. Reopen the application if retry continues to fail.')

    def dispose(self):
        self._disposed=True;self.timer.Stop();self.timer.Dispose();self.tips.Dispose()
