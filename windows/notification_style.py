"""Shared desktop notification presentation. No scheduling or eligibility decisions."""
from pathlib import Path
import sys

BG = "#171E2C"
HERO = "#101927"
SURFACE = "#202B3C"
BORDER = "#304056"
TEXT = "#F5F8FD"
MUTED = "#AEBACB"
GREEN = "#22D69B"
CYAN = "#77BEFF"
AMBER = "#FFBC78"


def mascot_path():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "assets" / "strictbot_full.png"


def popup_geometry(bounds, scale=1.0):
    left, top, right, bottom = bounds
    gap = max(8, round(16 * scale))
    width = min(round(900 * scale), right - left - gap * 2)
    height = min(round(570 * scale), bottom - top - gap * 2)
    return width, height, left + (right-left-width)//2, top + (bottom-top-height)//2


def stacked_layout(width, scale=1.0):
    return width < 670 * scale


def toast_geometry(bounds, scale=1.0, content_height=250):
    """Physical pixels, bounded to the display's work area above its taskbar."""
    left, top, right, bottom = bounds
    gap = max(8, round(18 * scale))
    width = min(round(560 * scale), right - left - gap * 2)
    height = min(round(content_height * scale), bottom - top - gap * 2)
    return width, height, right - width - gap, bottom - height - gap


def category_presentation(category, warning=False):
    category = str(category or "SYSTEM").upper()
    if warning:
        return category.replace("_", " "), AMBER
    if category in ("PROOF", "AI"):
        return "PROOF & REVIEW", "#BDABFF"
    labels = {"TASKS": "YOUR ROUTINE", "POINTS": "POINTS UPDATE", "PRIVILEGES": "PRIVILEGES",
              "FOCUS": "FOCUS TIME", "STRICT": "FOCUS TIME", "ANDROID": "FROM YOUR PHONE",
              "SYNC": "DEVICE SYNC", "SYSTEM": "STRICTROUTINES"}
    return labels.get(category, category.replace("_", " ")), CYAN if category in ("SYNC", "ANDROID") else GREEN


def style_titlebar(window):
    """Use the supported Windows dark caption when available; keep native controls."""
    if sys.platform != "win32": return
    try:
        import ctypes
        window.update_idletasks()
        user = ctypes.windll.user32
        user.GetParent.restype = ctypes.c_void_p
        hwnd = user.GetParent(ctypes.c_void_p(window.winfo_id())) or window.winfo_id()
        dark = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd),20,ctypes.byref(dark),ctypes.sizeof(dark))
    except (OSError, AttributeError):
        pass


class ToastClock:
    """Monotonic active time; hover pauses dismissal as well as the progress bar."""
    def __init__(self, duration, now):
        self.duration = duration
        self.last = now
        self.elapsed = 0.0

    def tick(self, now, paused=False):
        delta = max(0.0, now - self.last)
        self.last = now
        if not paused:
            self.elapsed += delta
        return max(0.0, 1.0 - self.elapsed / self.duration)


def score_presentation(score, threshold):
    if score is None:
        return "Not synced", f"{threshold} points to unlock", 0.0
    # Only the painted progress fraction is bounded; the ledger value stays negative.
    return str(score), f"Unlocks at {threshold} points", max(0.0, min(1.0, score / max(1, threshold)))


class RestrictionCard:
    """Tk view with a responsive artwork pane and a scrollable text pane."""
    def __init__(self, window, on_home, scale=1.0):
        import tkinter as tk
        self.tk, self.scale = tk, scale
        self.frame = tk.Frame(window, bg=BG)
        self.frame.pack(fill="both", expand=True)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)
        header = tk.Frame(self.frame, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=self.px(28), pady=(self.px(20), self.px(12)))
        tk.Label(header, text="StrictRoutines", font=("Segoe UI", 12, "bold"), bg=BG, fg=TEXT).pack(side="left")
        tk.Label(header, text="STRICTBOT", font=("Segoe UI", 9, "bold"), bg=BG, fg=CYAN).pack(side="right")
        self.body = tk.Frame(self.frame, bg=BG)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.rowconfigure(0, weight=1)
        self.art = tk.Canvas(self.body, bg=HERO, bd=0, highlightthickness=0)
        self.right = tk.Frame(self.body, bg=BG)
        self.right.rowconfigure(0, weight=1)
        self.right.columnconfigure(0, weight=1)
        self.right.columnconfigure(1, minsize=self.px(8))
        self.canvas = tk.Canvas(self.right, bg=BG, bd=0, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        from tkinter import ttk
        scrollbar_style = ttk.Style(window)
        scrollbar_style.theme_use("clam")
        scrollbar_style.configure("Strict.Vertical.TScrollbar", background=BORDER,
            troughcolor=BG, bordercolor=BG, arrowcolor=MUTED, width=self.px(6))
        scrollbar_style.layout("Strict.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky":"ns", "children":[
                ("Vertical.Scrollbar.thumb", {"expand":"1", "sticky":"nswe"})]})])
        self.scroll = ttk.Scrollbar(self.right, command=self.canvas.yview, style="Strict.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.copy = tk.Frame(self.canvas, bg=BG)
        self.item = self.canvas.create_window(0, 0, window=self.copy, anchor="nw")
        self.copy.bind("<Configure>", self._fit_copy)
        self.canvas.bind("<Configure>", self._fit_copy)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-int(e.delta / 120), "units"))
        self.labels = []
        self.badge = self.label("APP RESTRICTED", 9, AMBER, bold=True)
        self.badge.configure(bg="#3A2B26", padx=self.px(10), pady=self.px(6))
        self.badge.pack_configure(anchor="w", fill="none", pady=(0,self.px(18)))
        self.heading = self.label("Time for something\nproductive!", 21, TEXT, bold=True)
        self.subtitle = self.label("Return to your routine. Your access unlocks when your confirmed score reaches the required points.", 11, MUTED)
        self.subtitle.pack_configure(pady=(self.px(8),self.px(18)))
        self.status = tk.Frame(self.copy, bg=SURFACE, padx=self.px(16), pady=self.px(14))
        self.status.pack(fill="x")
        tk.Label(self.status, text="CURRENT STATUS", bg=SURFACE, fg=CYAN, font=("Segoe UI",8,"bold"), anchor="w").pack(fill="x")
        self.score_line = tk.Frame(self.status, bg=SURFACE)
        self.score_line.pack(fill="x", pady=(self.px(5),self.px(3)))
        self.score = tk.Label(self.score_line, text="", bg=SURFACE, fg=TEXT, font=("Segoe UI",24,"bold"))
        self.score.pack(side="left")
        self.required = tk.Label(self.status, text="", bg=SURFACE, fg=MUTED, font=("Segoe UI",10), anchor="w", justify="left")
        self.required.pack(fill="x")
        self.progress = tk.Canvas(self.status, height=self.px(4), bg=BORDER, bd=0, highlightthickness=0)
        self.progress.pack(fill="x", pady=(self.px(12),0))
        self.progress.bind("<Configure>", self._draw_progress)
        self.reason = self.label("", 10, MUTED)
        self.reason.pack_configure(pady=(self.px(12),0))
        footer = tk.Frame(self.right, bg=BG)
        footer.grid(row=1, column=0, sticky="ew", pady=(self.px(20),self.px(24)))
        self.button = tk.Button(footer, text="BACK TO HOME", command=on_home, font=("Segoe UI",11,"bold"),
            bg=GREEN, fg="#062D22", activebackground="#5EE9BB", activeforeground="#062D22", disabledforeground=MUTED,
            bd=0, relief="flat", cursor="hand2", pady=self.px(12), takefocus=True)
        self.button.pack(fill="x")
        self.button.bind("<Enter>", lambda _: self.button.configure(bg="#5EE9BB") if self.button.cget("state") != "disabled" else None)
        self.button.bind("<Leave>", lambda _: self.button.configure(bg=GREEN))
        self.image = None
        self.original_image = None
        self.fraction = 0.0
        self._stacked = None
        try:
            from PIL import Image
            with Image.open(mascot_path()) as source:
                self.original_image = source.convert("RGBA")
        except (OSError, ImportError):
            pass
        self.art.bind("<Configure>", self._draw_art)
        self.frame.bind("<Configure>", self._layout)

    def px(self, value): return max(1, round(value * self.scale))

    def label(self, text, size, color, bold=False):
        label = self.tk.Label(self.copy, text=text, font=("Segoe UI",size,"bold" if bold else "normal"),
            bg=BG, fg=color, justify="left", anchor="w", wraplength=self.px(410))
        label.pack(fill="x")
        self.labels.append(label)
        return label

    def _layout(self, event):
        if event.widget is not self.frame: return
        stack = stacked_layout(event.width, self.scale)
        if stack == self._stacked: return
        self._stacked = stack
        self.art.grid_forget(); self.right.grid_forget()
        if stack:
            self.body.columnconfigure(0,weight=1); self.body.columnconfigure(1,weight=0)
            # Artwork becomes a compact banner; text/action keep the available height.
            self.art.configure(height=self.px(130))
            self.art.grid(row=0,column=0,sticky="ew")
            self.body.rowconfigure(0,weight=0); self.body.rowconfigure(1,weight=1)
            self.right.grid(row=1,column=0,sticky="nsew",padx=self.px(24),pady=(self.px(16),0))
        else:
            self.body.rowconfigure(0,weight=1); self.body.rowconfigure(1,weight=0)
            self.body.columnconfigure(0,weight=4,uniform="content"); self.body.columnconfigure(1,weight=6,uniform="content")
            self.art.grid(row=0,column=0,sticky="nsew")
            self.right.grid(row=0,column=1,sticky="nsew",padx=self.px(32),pady=(self.px(18),0))

    def _fit_copy(self, event=None):
        width=max(1,self.canvas.winfo_width())
        self.canvas.itemconfigure(self.item,width=width)
        for label in self.labels: label.configure(wraplength=max(100,width))
        self.required.configure(wraplength=max(80,width-self.px(32)))
        content_height=self.copy.winfo_reqheight()
        height=self.canvas.winfo_height()
        self.canvas.coords(self.item,0,max(0,(height-content_height)//2))
        self.canvas.configure(scrollregion=(0,0,width,max(height,content_height)))
        if content_height > height+2: self.scroll.grid(row=0,column=1,sticky="ns")
        else: self.scroll.grid_remove()

    def _draw_progress(self,event=None):
        self.progress.delete("all")
        self.progress.create_rectangle(0,0,self.progress.winfo_width()*self.fraction,self.px(5),fill=GREEN,outline="")

    def _draw_art(self,event=None):
        w,h=self.art.winfo_width(),self.art.winfo_height()
        self.art.delete("all")
        cx,cy=w*.50,h*.48
        radius=min(w*.43,h*.44)
        for factor,color in ((1.02,"#1E3448"),(.84,"#1A2C3F"),(.67,"#183247")):
            self.art.create_oval(cx-radius*factor,cy-radius*factor,cx+radius*factor,cy+radius*factor,outline=color,width=self.px(1))
        self.art.create_oval(cx+radius*.80,cy-radius*.57,cx+radius*.80+self.px(8),cy-radius*.57+self.px(8),fill=GREEN,outline="")
        if self.original_image is not None:
            from PIL import Image,ImageTk,ImageOps
            size=max(32,round(min(w*.94,h*.88)))
            scaled=ImageOps.contain(self.original_image,(size,size),Image.Resampling.LANCZOS)
            self.image=ImageTk.PhotoImage(scaled,master=self.art)
            self.art.create_image(cx,cy,image=self.image)
        else:
            self.art.create_text(cx,cy,text="StrictBot",fill=CYAN,font=("Segoe UI",18,"bold"))

    def update(self,data):
        value,required,self.fraction=score_presentation(data.get("score"),data.get("required_score",80))
        self.score.configure(text=value + (" / 100" if data.get("score") is not None else ""))
        self.required.configure(text=required)
        target=data.get("target_app") or "This app"
        self.badge.configure(text=f"{target.upper()} RESTRICTED")
        self.subtitle.configure(text=f"{target} is currently restricted. Return to your routine to build your confirmed score.")
        self.reason.configure(text=data.get("reason") or "Your score does not meet the required unlock threshold.")
        self._draw_progress()
