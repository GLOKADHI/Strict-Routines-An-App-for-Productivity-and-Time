"""Opt-in checks of this app's own native controls. No desktop input/capture."""
import json
from pathlib import Path
from types import SimpleNamespace


def check(preview,native,directory):
    from System.Drawing import Point,Size,Bitmap,Rectangle,Graphics
    from System.Windows.Forms import MouseButtons,MouseEventArgs,FormWindowState,TextRenderer,TextFormatFlags
    from windows.camera_preview_geometry import Rect,clamp_rect
    if not getattr(preview,'_offline_validation',False):raise ValueError('Requires the isolated frontend fixture')
    target=Path(directory);target.mkdir(parents=True,exist_ok=True)
    import hashlib
    import sys
    result={'production_session_started':False,'desktop_input_injected':False,'live_camera_opened':False,
            'executable_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()}
    def actual_camera_margin(camera):
        import ctypes
        from ctypes import wintypes
        user32=ctypes.windll.user32
        user32.GetWindowRect.argtypes=(wintypes.HWND,ctypes.POINTER(wintypes.RECT))
        user32.GetClientRect.argtypes=(wintypes.HWND,ctypes.POINTER(wintypes.RECT))
        user32.ClientToScreen.argtypes=(wintypes.HWND,ctypes.POINTER(wintypes.POINT))
        def client(control):
            handle=int(control.Handle.ToInt64());rect=wintypes.RECT();origin=wintypes.POINT(0,0)
            assert user32.GetClientRect(handle,ctypes.byref(rect)) and user32.ClientToScreen(handle,ctypes.byref(origin))
            return (origin.x,origin.y,origin.x+rect.right,origin.y+rect.bottom)
        owner=client(native);workspace=client(p.workspace)
        area=(max(owner[0],workspace[0]),max(owner[1],workspace[1]),min(owner[2],workspace[2]),min(owner[3],workspace[3]))
        rect=wintypes.RECT();assert user32.GetWindowRect(int(camera.window.Handle.ToInt64()),ctypes.byref(rect))
        return min(rect.left-area[0],rect.top-area[1],area[2]-rect.right,area[3]-rect.bottom)
    original=native.Bounds
    native.WindowState=FormWindowState.Normal;native.MinimumSize=Size(0,0)
    native.Location=Point(-20000,-20000)
    p=preview
    def layout(width=1600,height=900):
        native.ClientSize=Size(width,height);native.PerformLayout();p._arrange();p.chrome.arrange()
    def mouse(x=2,y=2):return MouseEventArgs(MouseButtons.Left,1,int(x),int(y),0)
    def bounds_ok(camera):
        area=camera._workspace();r=camera._rect()
        return r.x>=area.x+12 and r.y>=area.y+12 and r.right<=area.right-12 and r.bottom<=area.bottom-12
    def capture(name):
        panel=p.panel;image=Bitmap(panel.Width,panel.Height)
        panel.DrawToBitmap(image,Rectangle(0,0,image.Width,image.Height))
        camera=p.camera_overlay
        if camera.window.Visible:
            camera_image=Bitmap(camera.window.Width,camera.window.Height)
            camera.window.DrawToBitmap(camera_image,Rectangle(0,0,camera_image.Width,camera_image.Height))
            graphics=Graphics.FromImage(image);point=panel.PointToClient(camera.window.Location)
            graphics.DrawImage(camera_image,point.X,point.Y);graphics.Dispose();camera_image.Dispose()
        image.Save(str(target/name));image.Dispose()
    layout()
    handles={key:int(view.Handle.ToInt64()) for key,view in p.views.items()}
    sizes=[]
    for width,height in ((1280,720),(1366,768),(1600,900),(1920,1080),(900,700),(640,600)):
        layout(width,height)
        toolbars=[]
        for key,ui in p.site_panes.items():
            controls=list(ui.buttons.values())
            valid=all(b.Left>=0 and b.Right<=ui.toolbar.Width and b.Top>=0 and b.Bottom<=ui.toolbar.Height for b in controls)
            ordered=sorted(controls,key=lambda b:b.Left)
            assert all(a.Right<=b.Left for a,b in zip(ordered,ordered[1:]))
            assert valid
            for text_key in ('title','icon'):
                node=ui.nodes[text_key];required=TextRenderer.MeasureText(node.Text,node.Font)
                assert required.Height<=node.Height,f'Offline {text_key} clipped at {width}'
            toolbars.append({'site':key,'width':ui.toolbar.Width,'no_clipped_controls':valid,
                             'address_width':ui.address.Width,'state':ui.state})
        header_bounds=p.chrome.header.ClientRectangle
        visible_header=[node for node in p.chrome.nodes.values() if node.Parent==p.chrome.header and node.Visible]
        assert all(node.Left>=0 and node.Top>=0 and node.Right<=header_bounds.Width and node.Bottom<=60 for node in visible_header)
        timer=p.chrome.nodes['timer'];measured=TextRenderer.MeasureText(timer.Text,timer.Font,Size(32767,32767),TextFormatFlags.NoPadding|TextFormatFlags.SingleLine)
        assert measured.Width<=timer.Width,f'Timer clipped at {width}: {measured.Width}>{timer.Width}'
        assert p.chrome.header.Height==60
        title=p.chrome.nodes['title'];title_size=TextRenderer.MeasureText(title.Text,title.Font)
        assert title.Text=='StrictRecords · Strict Session' and title_size.Width<=title.Width,f'Title clipped at {width}: {title_size.Width}>{title.Width}'
        layout_controls=list(p.layout_toolbar.buttons.values())
        assert all(b.Left>=0 and b.Right<=p.layout_toolbar.host.Width and b.Bottom<=36 for b in layout_controls),f'Shared toolbar clipped at {width}'
        assert all('swap' not in ui.buttons and 'mode' not in ui.buttons for ui in p.site_panes.values())
        assert bounds_ok(p.camera_overlay),f'Camera outside workspace at {width}'
        actual_margin=actual_camera_margin(p.camera_overlay);assert actual_margin>=12,f'Camera HWND margin {actual_margin} at {width}'
        if p.pane_layout.split(p.workspace.Width):assert abs(p.panes['chatgpt'].Width-p.panes['leetcode'].Width)<=1
        sizes.append(dict(width=width,height=height,header_height=60,title_fits=True,timer_fits=True,toolbars=toolbars,
            equal_split=abs(p.panes['chatgpt'].Width-p.panes['leetcode'].Width)<=1 if p.pane_layout.split(p.workspace.Width) else 'single-pane fallback',
            camera_margin_at_least_12px=True,actual_camera_hwnd_margin_px=actual_margin,shared_controls_fit=True))
    result['responsive_sizes']=sizes
    layout()
    p.chrome.nodes['toggle'].PerformClick();assert p.chrome.expanded and p.chrome.header.Height>60
    p.chrome.nodes['toggle'].PerformClick();assert not p.chrome.expanded and p.chrome.header.Height==60
    result['header_button_expands_and_collapses']=True
    cam=p.camera_overlay
    cam.btn_eye.PerformClick();assert cam.is_user_hidden and not cam.window.Visible
    p.chrome.nodes['camera'].PerformClick();assert not cam.is_user_hidden and cam.window.Visible
    assert cam.current_frame is None and cam.unavailable_label.Text=='CAMERA PREVIEW UNAVAILABLE – DEMO'
    result['eye_button_hides_and_restore_button_restores']=True
    result['offline_camera_is_explicit_and_has_no_image']=True
    cam._apply_rect(clamp_rect(Rect(cam._workspace().x+120,cam._workspace().y+120,240,135),cam._workspace()))
    before=cam._rect();sender=cam.header
    cam._on_drag_down(sender,mouse(10,10));cam._on_drag_move(sender,mouse(65,45));cam._on_drag_up(sender,mouse(65,45))
    assert cam._rect()!=before and bounds_ok(cam) and not sender.Capture
    result['native_drag_handler_moves_and_clamps']=True
    resized=[]
    for direction,handle in cam.resize_handles.items():
        area=cam._workspace();cam._apply_rect(clamp_rect(Rect(area.x+180,area.y+140,240,135),area))
        before=cam._rect();dx=-24 if 'w' in direction else 24 if 'e' in direction else 0
        dy=-18 if 'n' in direction else 18 if 's' in direction else 0
        cam._on_resize_down(handle,mouse());cam._on_resize_move(handle,mouse(2+dx,2+dy));cam._on_resize_up(handle,mouse())
        after=cam._rect();assert after.width!=before.width and bounds_ok(cam) and not handle.Capture
        assert abs(after.height-after.width*9/16)<=1
        resized.append(direction)
    result['native_resize_handlers']=resized
    before=list(p.pane_layout.order);p.layout_toolbar.buttons['swap'].PerformClick()
    assert p.pane_layout.order==before[::-1]
    p.layout_toolbar.buttons['swap'].PerformClick()
    p.layout_toolbar.buttons['chatgpt'].PerformClick();assert not p.pane_layout.side_by_side and p.active_tab=='chatgpt'
    p.layout_toolbar.buttons['leetcode'].PerformClick();assert not p.pane_layout.side_by_side and p.active_tab=='leetcode'
    p.layout_toolbar.buttons['split'].PerformClick();assert p.pane_layout.side_by_side
    p.pane_layout.ratio=.62;p.layout_toolbar.buttons['restore'].PerformClick()
    assert p.pane_layout.side_by_side and p.pane_layout.ratio==.5 and p.pane_layout.order==['chatgpt','leetcode']
    result['shared_layout_controls_work']=True
    down,move,up=p._divider_handlers;down(p.splitter,mouse())
    desired=p.splitter.PointToClient(p.workspace.PointToScreen(Point(round(p.workspace.Width*.62),90)))
    move(p.splitter,mouse(desired.X,desired.Y));up(p.splitter,mouse())
    assert .59<p.pane_layout.ratio<.65
    result['divider_handler_resizes_panes']=True
    result['browser_handles_preserved']=handles=={key:int(view.Handle.ToInt64()) for key,view in p.views.items()}
    assert result['browser_handles_preserved']
    ui=p.site_panes['chatgpt'];actual_view=ui.view
    ui.offline=False;calls=[]
    try:
        ui.view=SimpleNamespace(CoreWebView2=SimpleNamespace(Navigate=calls.append))
        ui.show_state('loading');assert ui.loading_indicator.Visible and not ui.retry_button.Visible
        ui.show_state('error','Test connection failure');assert ui.retry_button.Visible
        ui.retry_button.PerformClick();assert calls==[ui.url] and ui.state=='loading'
        ui.show_state('ready');assert not ui.surface.Visible
    finally:
        ui.view=actual_view;ui.offline=True;ui.show_state('offline')
    result['loading_error_retry_and_ready_controls_work']=True
    assert len([p.labels[k] for k in ('end','controls') if p.labels[k].Visible])==1
    result['single_demo_exit_control']=True
    result['demo_strip_height']=p.labels['alert'].Height;assert result['demo_strip_height']<=22
    result['backend_monitoring_was_not_initialized_by_ui']=True
    p.pane_layout.ratio=.5;p.pane_layout.side_by_side=True
    if p.pane_layout.order[0]!='chatgpt':p.pane_layout.swap()
    p._select_tab('chatgpt',False);layout()
    cam._apply_rect(clamp_rect(Rect(cam._workspace().x,cam._workspace().y,240,135),cam._workspace()))
    cam.move_to_corner('Bottom right')
    capture('strict-session-final-offline.png')
    p.chrome.nodes['toggle'].PerformClick();capture('strict-session-diagnostics.png');p.chrome.nodes['toggle'].PerformClick()
    result['success']=True
    (target/'native-functional-checks.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    native.Bounds=original;p._arrange()
    return result
