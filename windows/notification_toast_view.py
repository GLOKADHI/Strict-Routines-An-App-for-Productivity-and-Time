"""WinForms presentation for every ordinary StrictRoutines desktop alert."""
import threading
import time

from windows.notification_style import (
    BG, HERO, BORDER, TEXT, MUTED, GREEN, ToastClock,
    category_presentation, mascot_path, toast_geometry,
)


def display_toast(WF, SD, SD2D, item, stop_event, *, preview=False):
    def color(value):
        return SD.ColorTranslator.FromHtml(value)

    form = WF.Form()
    form.Text = "StrictRoutines — Notification"
    form.FormBorderStyle = getattr(WF.FormBorderStyle, "None")
    form.AutoScaleMode = getattr(WF.AutoScaleMode, "None")
    # An isolated developer preview is targetable by desktop inspection tools.
    form.ShowInTaskbar = preview
    form.TopMost = True
    form.StartPosition = WF.FormStartPosition.Manual
    form.BackColor = color(BG)
    form.Opacity = 0.0
    resources = []
    graphic = form.CreateGraphics()
    scale = max(.75, graphic.DpiX / 96.0)
    graphic.Dispose()
    def px(n): return max(1, round(n * scale))
    screen = WF.Screen.FromPoint(WF.Cursor.Position).WorkingArea
    bounds = (screen.Left, screen.Top, screen.Right, screen.Bottom)
    width, _, _, _ = toast_geometry(bounds, scale)
    art_width = min(px(164), round(width * .30))
    content_left = art_width + px(24)
    content_width = max(px(100), width - content_left - px(24))
    tag, accent = category_presentation(item["category"], item["warning"])

    def label(text, size, fg, bold=False):
        control = WF.Label()
        control.Text = text
        control.Font = SD.Font("Segoe UI", size, SD.FontStyle.Bold if bold else SD.FontStyle.Regular)
        resources.append(control.Font)
        control.ForeColor = color(fg)
        control.BackColor = color(BG)
        control.UseMnemonic = False
        return control

    brand = label("StrictRoutines", 8.5, MUTED, True)
    brand.SetBounds(content_left, px(18), max(1, content_width-px(28)), px(20))
    form.Controls.Add(brand)
    close = label("×", 17, MUTED)
    close.TextAlign = SD.ContentAlignment.MiddleCenter
    close.SetBounds(width-px(38), px(8), px(30), px(30))
    close.Cursor = WF.Cursors.Hand
    close.AccessibleName = "Dismiss notification"
    form.Controls.Add(close)
    badge = label(tag, 8, accent, True)
    badge.SetBounds(content_left, px(47), content_width, px(20))
    form.Controls.Add(badge)
    title = label(item["title"], 16, TEXT, True)
    flags = WF.TextFormatFlags.WordBreak | WF.TextFormatFlags.NoPadding
    title_height = WF.TextRenderer.MeasureText(title.Text, title.Font, SD.Size(content_width, 10000), flags).Height + px(4)
    title.SetBounds(0, 0, content_width-px(18), title_height)
    message = label(item["message"], 10, MUTED)
    message_height = WF.TextRenderer.MeasureText(message.Text or " ", message.Font, SD.Size(content_width-px(18),10000), flags).Height + px(5)
    body_top = px(73)
    message_top = title_height + px(10)
    wanted_height = max(px(252), body_top + message_top + message_height + px(86))
    width, height, x, y = toast_geometry(bounds, scale, wanted_height / scale)
    form.ClientSize = SD.Size(width, height)
    form.Location = SD.Point(x,y)
    # A long message scrolls inside its pane; the action and close button stay visible.
    body = WF.Panel()
    body.BackColor = color(BG)
    body.AutoScroll = True
    body.SetBounds(content_left, body_top, content_width, max(px(24),height-body_top-px(82)))
    title.Width=max(1,content_width-px(18))
    body.Controls.Add(title)
    message.SetBounds(0,message_top,max(1,content_width-px(18)),message_height)
    body.Controls.Add(message)
    form.Controls.Add(body)
    action = WF.Button()
    action.Text = "Open StrictRoutines  →"
    action.AccessibleName = "Open StrictRoutines"
    action.Font = SD.Font("Segoe UI", 10, SD.FontStyle.Bold)
    resources.append(action.Font)
    action.SetBounds(content_left, height-px(62), content_width, px(39))
    action.FlatStyle = WF.FlatStyle.Flat
    action.FlatAppearance.BorderSize = 0
    action.FlatAppearance.MouseOverBackColor = color("#5EE9BB")
    action.BackColor = color(GREEN)
    action.ForeColor = color("#062D22")
    action.Cursor = WF.Cursors.Hand
    form.Controls.Add(action)

    art = WF.Panel()
    art.BackColor = color(HERO)
    art.SetBounds(px(1),px(1),art_width-px(1),height-px(2))
    art.Cursor = WF.Cursors.Hand
    form.Controls.Add(art)
    image = None
    if mascot_path().is_file():
        source = SD.Image.FromFile(str(mascot_path()))
        try:
            image = SD.Bitmap(source)
            resources.append(image)
        finally:
            source.Dispose()
    def paint_art(sender,e):
        g=e.Graphics
        g.SmoothingMode=SD2D.SmoothingMode.AntiAlias
        g.InterpolationMode=SD2D.InterpolationMode.HighQualityBicubic
        size=min(art.Width-px(12),art.Height-px(38))
        left=(art.Width-size)//2; top=(art.Height-size)//2
        pen=SD.Pen(color("#294256"),1.0)
        try:
            g.DrawEllipse(pen,left,top,size,size)
            g.DrawEllipse(pen,left+px(12),top+px(12),max(1,size-px(24)),max(1,size-px(24)))
        finally: pen.Dispose()
        if image is not None:
            g.DrawImage(image,SD.Rectangle(left,top,size,size))
    art.Paint += paint_art

    # Regular reminders remain non-activating, including their fade/slide animation.
    try:
        import ctypes
        hwnd=form.Handle.ToInt64()
        style=ctypes.windll.user32.GetWindowLongW(hwnd,-20)
        if not preview:
            ctypes.windll.user32.SetWindowLongW(hwnd,-20,style|0x08000000|0x00000080|0x00000008)
    except (AttributeError,OSError):
        pass
    callback=item.get("on_click")
    if callback is None:
        def callback():
            from windows.tray_manager import StrictRecordsTrayManager
            tray=StrictRecordsTrayManager.get_existing_instance()
            if tray: tray.restore_app_window()
    dismissed=[False]
    opened=threading.Event()
    def open_app(sender,e):
        if opened.is_set(): return
        opened.set(); dismissed[0]=True
        def invoke():
            try: callback()
            except Exception:
                import logging
                logging.getLogger("ModernToast").exception("Notification action failed")
        threading.Thread(target=invoke,name="NotificationAction",daemon=True).start()
    def dismiss(sender,e): dismissed[0]=True
    for control in (form,art,brand,badge,title,message,action): control.Click += open_app
    close.Click += dismiss
    close.MouseEnter += lambda s,e: setattr(close,"ForeColor",color(TEXT))
    close.MouseLeave += lambda s,e: setattr(close,"ForeColor",color(MUTED))

    start=time.monotonic()
    clock=ToastClock(item["duration_sec"],start)
    fraction=[1.0]
    def paint(sender,e):
        pen=SD.Pen(color(BORDER),1.0)
        try: e.Graphics.DrawRectangle(pen,0,0,width-1,height-1)
        finally: pen.Dispose()
        pen=SD.Pen(color(accent),px(3))
        try: e.Graphics.DrawLine(pen,1,height-2,max(1,round((width-2)*fraction[0])),height-2)
        finally: pen.Dispose()
    form.Paint += paint
    timer=WF.Timer()
    timer.Interval=25
    def tick(sender,e):
        if dismissed[0] or stop_event.is_set():
            form.Opacity=max(0.0,form.Opacity-.14)
        else:
            now=time.monotonic()
            hovered=form.ClientRectangle.Contains(form.PointToClient(WF.Cursor.Position))
            fraction[0]=clock.tick(now,paused=hovered)
            age=now-start
            if clock.elapsed >= clock.duration:
                dismissed[0]=True
            else:
                form.Opacity=min(.99,age/.18)
                form.Top=y+round(px(8)*max(0,1-age/.18))
            form.Invalidate()
        if (dismissed[0] or stop_event.is_set()) and form.Opacity <= .01:
            timer.Stop(); form.Close()
    timer.Tick += tick
    try:
        form.Show()
        timer.Start()
        WF.Application.Run(form)
    finally:
        timer.Stop(); timer.Dispose(); form.Dispose()
        for resource in resources: resource.Dispose()
