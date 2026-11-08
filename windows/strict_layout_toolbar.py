"""One shared layout toolbar; rearranges existing browser controls only."""
LABELS=(('split','Side by Side'),('swap','Swap Panes'),('chatgpt','Focus ChatGPT'),
        ('leetcode','Focus LeetCode'),('restore','Restore Split View'))
SHORT_LABELS={'split':'Split','swap':'Swap','chatgpt':'ChatGPT','leetcode':'LeetCode','restore':'Reset split'}


def shared_toolbar_geometry(width,widths=(98,92,116,122,132)):
    total=sum(widths)+6*(len(widths)-1)
    x=max(8,(int(width)-total)//2)
    result={}
    for (key,_),size in zip(LABELS,widths):
        result[key]=(x,4,size,28);x+=size+6
    return result


class SharedStudyToolbar:
    def __init__(self,callbacks):
        from System.Drawing import Color,Font,FontStyle,Rectangle,Size
        from System.Windows.Forms import Panel,Button,DockStyle,FlatStyle,Cursors,ToolTip,TextRenderer
        self._color=Color;self._rect=Rectangle;self.tips=ToolTip();self.buttons={}
        host=Panel();host.Dock=DockStyle.Top;host.Height=36;host.BackColor=Color.FromArgb(27,29,32);self.host=host
        self._widths=[];self._short_widths=[]
        for key,text in LABELS:
            button=Button();button.Text=text;button.Font=Font('Segoe UI',9.5,FontStyle.Bold);button.ForeColor=Color.FromArgb(240,244,248)
            button.UseCompatibleTextRendering=False
            button.ForeColor=Color.FromArgb(216,222,227);button.BackColor=Color.FromArgb(40,44,49)
            button.FlatStyle=FlatStyle.Flat;button.FlatAppearance.BorderColor=Color.FromArgb(63,69,75)
            button.FlatAppearance.BorderSize=1;button.Cursor=Cursors.Hand;button.AccessibleName=text
            def click(*_,action=callbacks[key]):action()
            button.Click+=click;host.Controls.Add(button);self.buttons[key]=button
            self.tips.SetToolTip(button,text)
            self._widths.append(max(72,TextRenderer.MeasureText(text,button.Font).Width+18))
            self._short_widths.append(max(60,TextRenderer.MeasureText(SHORT_LABELS[key],button.Font).Width+18))
        host.Resize+=self.arrange;self.arrange()

    def arrange(self,*_):
        short=sum(self._widths)+24>self.host.ClientSize.Width-16
        for key,bounds in shared_toolbar_geometry(self.host.ClientSize.Width,self._short_widths if short else self._widths).items():
            self.buttons[key].Bounds=self._rect(*bounds)
            self.buttons[key].Text=SHORT_LABELS[key] if short else dict(LABELS)[key]

    def update(self,layout):
        for key,button in self.buttons.items():
            selected=(key=='split' and layout.side_by_side) or (key in ('chatgpt','leetcode') and not layout.side_by_side and layout.active==key)
            button.BackColor=self._color.FromArgb(55,62,66) if selected else self._color.FromArgb(40,44,49)

    def dispose(self):self.tips.Dispose()
