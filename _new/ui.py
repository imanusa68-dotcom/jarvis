import os, json, time, math, random, threading
import tkinter as tk
from collections import deque
from queue import Queue, Empty
from PIL import Image, ImageTk, ImageDraw
import sys
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR   = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"

SYSTEM_NAME = "J.A.R.V.I.S"
MODEL_BADGE = "MARK XXXV"

C_BG     = "#000000"
C_PRI    = "#00d4ff"
C_MID    = "#007a99"
C_DIM    = "#003344"
C_DIMMER = "#001520"
C_ACC    = "#ff6600"
C_ACC2   = "#ffcc00"
C_TEXT   = "#8ffcff"
C_PANEL  = "#010c10"
C_GREEN  = "#00ff88"
C_RED    = "#ff3333"
C_MUTED  = "#ff3366"


class JarvisUI:
    def __init__(self, face_path, size=None):
        self.root = tk.Tk()
        self.root.title("J.A.R.V.I.S — MARK XXXV")
        self.root.resizable(False, False)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        W  = min(sw, 984)
        H  = min(sh, 816)
        self.root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")
        self.root.configure(bg=C_BG)

        self.W = W
        self.H = H

        self.FACE_SZ = min(int(H * 0.54), 400)
        self.FCX     = W // 2
        self.FCY     = int(H * 0.13) + self.FACE_SZ // 2

        # ── Durum ────────────────────────────────────────────────────────────
        self.speaking      = False
        self.muted         = False          # Mute flag — main.py okur
        self.screen_control = False         # Screen click mode flag
        self.scale        = 1.0
        self.target_scale = 1.0
        self.halo_a       = 60.0
        self.target_halo  = 60.0
        self.last_t       = time.time()
        self.tick         = 0
        self.scan_angle   = 0.0
        self.scan2_angle  = 180.0
        self.rings_spin   = [0.0, 120.0, 240.0]
        self.pulse_r      = [0.0, self.FACE_SZ * 0.26, self.FACE_SZ * 0.52]
        self.status_text  = "INITIALISING"
        self.status_blink = True

        # Dışarıdan set edilebilen durum (main.py çağırır)
        # Değerler: "LISTENING" | "SPEAKING" | "THINKING" | "MUTED" | "ONLINE"
        self._jarvis_state = "INITIALISING"

        self.typing_queue = deque()
        self.is_typing    = False

        # Klavye girişinden komutu iletmek için callback — main.py atar
        self.on_text_command = None

        # ── Thread-safe UI queue ─────────────────────────────────────────────────
        # All UI operations from background threads go through this queue.
        # The main UI thread polls it via root.after() and executes operations.
        self._ui_thread_id = threading.get_ident()
        self._ui_queue = Queue()
        self._ui_queue_poll_ms = 10  # Poll every 10ms

        self._face_pil         = None
        self._has_face         = False
        self._face_scale_cache = None
        self._load_face(face_path)

        # ── Canvas (arka plan animasyon) ─────────────────────────────────────
        self.bg = tk.Canvas(self.root, width=W, height=H,
                            bg=C_BG, highlightthickness=0)
        self.bg.place(x=0, y=0)

        # ── Log alanı ────────────────────────────────────────────────────────
        LW = int(W * 0.72)
        LH = 110
        LOG_Y = H - LH - 80   # klavye inputu için yukarı çektik
        self.log_frame = tk.Frame(self.root, bg=C_PANEL,
                                  highlightbackground=C_MID,
                                  highlightthickness=1)
        self.log_frame.place(x=(W - LW) // 2, y=LOG_Y, width=LW, height=LH)
        self.log_text = tk.Text(self.log_frame, fg=C_TEXT, bg=C_PANEL,
                                insertbackground=C_TEXT, borderwidth=0,
                                wrap="word", font=("Courier", 10), padx=10, pady=6)
        self.log_text.pack(fill="both", expand=True)
        # Read-only but SELECTABLE so the conversation can be copied. A disabled
        # Text can't be selected, so we keep it "normal" and just block typing.
        self._make_readonly(self.log_text)
        self._enable_clipboard(self.log_text, editable=False)
        self._attach_menu(self.log_text, editable=False)
        self.log_text.tag_config("you", foreground="#e8e8e8")
        self.log_text.tag_config("ai",  foreground=C_PRI)
        self.log_text.tag_config("sys", foreground=C_ACC2)
        self.log_text.tag_config("err", foreground=C_RED)

        # ── Klavye girişi ─────────────────────────────────────────────────────
        INPUT_Y = LOG_Y + LH + 6
        self._build_input_bar(LW, INPUT_Y)

        # ── Mute butonu ───────────────────────────────────────────────────────
        self._build_mute_button()
        self._build_screen_control_button()
        self._build_screen_view_button()

        # ── Screen View panel state (lazily created Toplevel) ─────────────
        self._sv_panel         = None   # Toplevel reference
        self._sv_win_var       = None   # StringVar for window selection
        self._sv_source_var    = None   # StringVar "full" | "window"
        self._sv_status_label  = None   # Label widget
        self._sv_startstop_btn = None   # Button widget
        self._sv_win_frame     = None   # Frame holding window selector row
        self._sv_win_menu      = None   # OptionMenu widget
        self._sv_windows       = []     # list[ScreenShareSource]


        # ── F4 kısayolu ───────────────────────────────────────────────────────
        self.root.bind("<F4>", lambda e: self._toggle_mute())

        # ── API key ───────────────────────────────────────────────────────────
        self._api_key_ready = self._api_keys_exist()
        if not self._api_key_ready:
            self._show_setup_ui()

        # ── Start UI queue polling loop ───────────────────────────────────────
        self._process_ui_queue()

        # ── Start Screen View poll loop (every 400ms) ──────────────────────────
        self.root.after(400, self._poll_sv_state)
        self._animate()
        self.root.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))

    # ── Mute butonu ───────────────────────────────────────────────────────────

    def _build_mute_button(self):
        """Sol alt köşeye mute butonu yerleştirir."""
        BTN_W, BTN_H = 110, 30
        BTN_X = 18
        BTN_Y = self.H - 40

        self._mute_canvas = tk.Canvas(
            self.root, width=BTN_W, height=BTN_H,
            bg=C_BG, highlightthickness=0, cursor="hand2"
        )
        self._mute_canvas.place(x=BTN_X, y=BTN_Y)
        self._mute_canvas.bind("<Button-1>", lambda e: self._toggle_mute())
        self._draw_mute_button()

    def _draw_mute_button(self):
        c = self._mute_canvas
        c.delete("all")
        if self.muted:
            border = C_MUTED
            fill   = "#1a0008"
            icon   = "🔇"
            label  = " MUTED"
            fg     = C_MUTED
        else:
            border = C_MID
            fill   = C_PANEL
            icon   = "🎙"
            label  = " LIVE"
            fg     = C_GREEN

        c.create_rectangle(0, 0, 110, 30, outline=border, fill=fill, width=1)
        c.create_text(55, 16, text=f"{icon}{label}",
                      fill=fg, font=("Courier", 10, "bold"))

    def _toggle_mute(self):
        self.muted = not self.muted
        self._draw_mute_button()
        if self.muted:
            self.set_state("MUTED")
            self.write_log("SYS: Microphone muted.")
        else:
            self.set_state("LISTENING")
            self.write_log("SYS: Microphone active.")

    # ── Thread-safe UI dispatch ───────────────────────────────────────────────

    def _is_ui_thread(self) -> bool:
        """Check if current thread is the main UI thread."""
        return threading.get_ident() == self._ui_thread_id

    def _process_ui_queue(self):
        """
        Poll the UI event queue and execute pending operations.
        This runs only in the main UI thread via root.after().
        """
        processed = 0
        max_per_tick = 100  # Prevent UI freeze if queue is flooded

        while processed < max_per_tick:
            try:
                event = self._ui_queue.get_nowait()
            except Empty:
                break

            event_type = event.get("type")
            if event_type == "set_state":
                self._set_state_impl(event.get("state", "ONLINE"))
            elif event_type == "write_log":
                self._write_log_impl(event.get("text", ""))

            processed += 1

        # Reschedule polling
        self.root.after(self._ui_queue_poll_ms, self._process_ui_queue)

    # ── Screen Control butonu ─────────────────────────────────────────────────

    def _build_screen_control_button(self):
        """Sağ alt köşeye Ekran Kontrol butonu."""
        BTN_W, BTN_H = 130, 30
        BTN_X = 18 + 110 + 8   # Mute butonunun sağına
        BTN_Y = self.H - 40

        self._sc_canvas = tk.Canvas(
            self.root, width=BTN_W, height=BTN_H,
            bg=C_BG, highlightthickness=0, cursor="hand2"
        )
        self._sc_canvas.place(x=BTN_X, y=BTN_Y)
        self._sc_canvas.bind("<Button-1>", lambda e: self._toggle_screen_control())
        self._draw_screen_control_button()

    def _draw_screen_control_button(self):
        c = self._sc_canvas
        c.delete("all")
        if self.screen_control:
            border = C_ACC2
            fill   = "#1a1200"
            icon   = "\u25cf"
            label  = " SCREEN ON"
            fg     = C_ACC2
        else:
            border = C_MID
            fill   = C_PANEL
            icon   = "\u25cb"
            label  = " SCREEN OFF"
            fg     = "#446677"

        c.create_rectangle(0, 0, 130, 30, outline=border, fill=fill, width=1)
        c.create_text(65, 15, text=f"{icon}{label}",
                      fill=fg, font=("Courier", 10, "bold"))

    def _toggle_screen_control(self):
        self.screen_control = not self.screen_control
        self._draw_screen_control_button()
        # Mirror into dialogue state immediately so the model sees the new value
        # on the very next turn (it must never have to guess the SCREEN state).
        try:
            from core.dialogue_state import set_screen_control as _ds_sc
            _ds_sc(self.screen_control)
        except Exception:
            pass
        if self.screen_control:
            self.write_log("SYS: Screen control ENABLED -- Jarvis can click on screen.")
        else:
            self.write_log("SYS: Screen control DISABLED.")

    # ── Klavye girişi ─────────────────────────────────────────────────────────

    # ── Clipboard / selection (layout-independent — works on Russian keyboard) ──
    def _enable_clipboard(self, widget, editable: bool = True):
        """
        Bind Ctrl+C / V / X / A by PHYSICAL key (keycode), so copy & paste work
        even on a non-Latin (Russian) layout where <Control-v> would otherwise not
        fire. `editable=False` allows only copy + select-all (for the read-only log).
        """
        def on_ctrl(event):
            kc = event.keycode
            ks = (event.keysym or "").lower()
            do_copy  = kc == 67 or ks in ("c", "с")
            do_paste = kc == 86 or ks in ("v", "м")
            do_cut   = kc == 88 or ks in ("x", "ч")
            do_all   = kc == 65 or ks in ("a", "ф")
            try:
                if do_copy:
                    widget.event_generate("<<Copy>>")
                elif do_all:
                    self._select_all(widget)
                elif editable and do_paste:
                    widget.event_generate("<<Paste>>")
                elif editable and do_cut:
                    widget.event_generate("<<Cut>>")
                else:
                    return
            except Exception:
                return
            return "break"
        widget.bind("<Control-KeyPress>", on_ctrl)

    def _select_all(self, widget):
        try:
            if isinstance(widget, tk.Entry):
                widget.select_range(0, "end")
                widget.icursor("end")
            else:
                widget.tag_add("sel", "1.0", "end-1c")
        except Exception:
            pass
        return "break"

    def _make_readonly(self, widget):
        """Keep a Text selectable/copyable but block character insertion."""
        def block(event):
            if event.state & 0x4:                 # Ctrl held → _enable_clipboard handles it
                return
            if event.char and event.char.isprintable():
                return "break"
        widget.bind("<KeyPress>", block)

    def _attach_menu(self, widget, editable: bool = True):
        """Right-click menu: Копировать (/ Вставить) / Выделить всё."""
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Копировать", command=lambda: widget.event_generate("<<Copy>>"))
        if editable:
            menu.add_command(label="Вставить", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_command(label="Выделить всё", command=lambda: self._select_all(widget))

        def popup(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        widget.bind("<Button-3>", popup)

    def _build_input_bar(self, lw: int, y: int):
        """Log'un hemen altına tek satır metin giriş alanı."""
        x0    = (self.W - lw) // 2
        BTN_W = 70
        INP_W = lw - BTN_W - 4

        self._input_var = tk.StringVar()

        self._input_entry = tk.Entry(
            self.root,
            textvariable=self._input_var,
            fg=C_TEXT, bg="#000d12",
            insertbackground=C_TEXT,
            borderwidth=0,
            font=("Courier", 10),
            highlightthickness=1,
            highlightbackground=C_DIM,
            highlightcolor=C_PRI,
        )
        self._input_entry.place(x=x0, y=y, width=INP_W, height=28)
        self._input_entry.bind("<Return>", self._on_input_submit)
        self._input_entry.bind("<KP_Enter>", self._on_input_submit)
        self._enable_clipboard(self._input_entry, editable=True)   # Ctrl+V on RU layout
        self._attach_menu(self._input_entry, editable=True)

        self._send_btn = tk.Button(
            self.root,
            text="SEND ▸",
            command=self._on_input_submit,
            fg=C_PRI, bg=C_PANEL,
            activeforeground=C_BG, activebackground=C_PRI,
            font=("Courier", 9, "bold"),
            borderwidth=0, cursor="hand2",
            highlightthickness=1,
            highlightbackground=C_MID,
        )
        self._send_btn.place(x=x0 + INP_W + 4, y=y, width=BTN_W, height=28)

    def _on_input_submit(self, event=None):
        text = self._input_var.get().strip()
        if not text:
            return
        self._input_var.set("")
        self.write_log(f"You: {text}")
        if self.on_text_command:
            threading.Thread(
                target=self.on_text_command,
                args=(text,),
                daemon=True
            ).start()

    # ── Durum yönetimi ────────────────────────────────────────────────────────

    def set_state(self, state: str):
        """
        Thread-safe state setter. Can be called from any thread.
        state: LISTENING | SPEAKING | THINKING | MUTED | ONLINE | PROCESSING
        """
        if self._is_ui_thread():
            self._set_state_impl(state)
        else:
            self._ui_queue.put({"type": "set_state", "state": state})

    def _set_state_impl(self, state: str):
        """
        Internal implementation — MUST only be called from UI thread.
        Actually updates Tkinter state variables.
        """
        self._jarvis_state = state
        if state == "MUTED":
            self.status_text = "MUTED"
            self.speaking    = False
        elif state == "SPEAKING":
            self.status_text = "SPEAKING"
            self.speaking    = True
        elif state == "THINKING":
            self.status_text = "THINKING"
            self.speaking    = False
        elif state == "LISTENING":
            self.status_text = "LISTENING"
            self.speaking    = False
        elif state == "PROCESSING":
            self.status_text = "PROCESSING"
            self.speaking    = False
        elif state == "OFFLINE":
            # Связи с моделью нет. Без этой ветки состояние уходило в else
            # и окно показывало «ONLINE» — прямую ложь владельцу.
            self.status_text = "OFFLINE"
            self.speaking    = False
        else:
            self.status_text = "ONLINE"
            self.speaking    = False

    # ── Yüz yükleme ───────────────────────────────────────────────────────────

    def _load_face(self, path):
        FW = self.FACE_SZ
        try:
            img  = Image.open(path).convert("RGBA").resize((FW, FW), Image.LANCZOS)
            mask = Image.new("L", (FW, FW), 0)
            ImageDraw.Draw(mask).ellipse((2, 2, FW - 2, FW - 2), fill=255)
            img.putalpha(mask)
            self._face_pil = img
            self._has_face = True
        except Exception:
            self._has_face = False

    @staticmethod
    def _ac(r, g, b, a):
        f = a / 255.0
        return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"

    # ── Animasyon döngüsü ─────────────────────────────────────────────────────

    def _animate(self):
        self.tick += 1
        t   = self.tick
        now = time.time()

        if now - self.last_t > (0.14 if self.speaking else 0.55):
            if self.speaking:
                self.target_scale = random.uniform(1.05, 1.11)
                self.target_halo  = random.uniform(138, 182)
            elif self.muted:
                self.target_scale = random.uniform(0.998, 1.001)
                self.target_halo  = random.uniform(20, 32)
            else:
                self.target_scale = random.uniform(1.001, 1.007)
                self.target_halo  = random.uniform(50, 68)
            self.last_t = now

        sp = 0.35 if self.speaking else 0.16
        self.scale  += (self.target_scale - self.scale) * sp
        self.halo_a += (self.target_halo  - self.halo_a) * sp

        for i, spd in enumerate([1.2, -0.8, 1.9] if self.speaking else [0.5, -0.3, 0.82]):
            self.rings_spin[i] = (self.rings_spin[i] + spd) % 360

        self.scan_angle  = (self.scan_angle  + (2.8 if self.speaking else 1.2)) % 360
        self.scan2_angle = (self.scan2_angle + (-1.7 if self.speaking else -0.68)) % 360

        pspd  = 3.8 if self.speaking else 1.8
        limit = self.FACE_SZ * 0.72
        new_p = [r + pspd for r in self.pulse_r if r + pspd < limit]
        if len(new_p) < 3 and random.random() < (0.06 if self.speaking else 0.022):
            new_p.append(0.0)
        self.pulse_r = new_p

        if t % 40 == 0:
            self.status_blink = not self.status_blink

        self._draw()
        self.root.after(16, self._animate)

    # ── Çizim ─────────────────────────────────────────────────────────────────

    def _draw(self):
        c    = self.bg
        W, H = self.W, self.H
        t    = self.tick
        FCX  = self.FCX
        FCY  = self.FCY
        FW   = self.FACE_SZ
        c.delete("all")

        # Arka plan grid
        for x in range(0, W, 44):
            for y in range(0, H, 44):
                c.create_rectangle(x, y, x+1, y+1, fill=C_DIMMER, outline="")

        # Halo halkaları
        for r in range(int(FW * 0.54), int(FW * 0.28), -22):
            frac = 1.0 - (r - FW * 0.28) / (FW * 0.26)
            ga   = max(0, min(255, int(self.halo_a * 0.09 * frac)))
            # Mute modda kırmızı halo
            if self.muted:
                gh = f"{ga:02x}"
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r,
                              outline=f"#{gh}0011", width=2)
            else:
                gh = f"{ga:02x}"
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r,
                              outline=f"#00{gh}ff", width=2)

        # Pulse dalgaları
        for pr in self.pulse_r:
            pa = max(0, int(220 * (1.0 - pr / (FW * 0.72))))
            r  = int(pr)
            if self.muted:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r,
                              outline=self._ac(255, 30, 80, pa // 3), width=2)
            else:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r,
                              outline=self._ac(0, 212, 255, pa), width=2)

        # Dönen halkalar
        for idx, (r_frac, w_ring, arc_l, gap) in enumerate([
                (0.47, 3, 110, 75), (0.39, 2, 75, 55), (0.31, 1, 55, 38)]):
            ring_r = int(FW * r_frac)
            base_a = self.rings_spin[idx]
            a_val  = max(0, min(255, int(self.halo_a * (1.0 - idx * 0.18))))
            col    = self._ac(255, 30, 80, a_val) if self.muted else self._ac(0, 212, 255, a_val)
            for s in range(360 // (arc_l + gap)):
                start = (base_a + s * (arc_l + gap)) % 360
                c.create_arc(FCX-ring_r, FCY-ring_r, FCX+ring_r, FCY+ring_r,
                             start=start, extent=arc_l,
                             outline=col, width=w_ring, style="arc")

        # Tarama yayları
        sr      = int(FW * 0.49)
        scan_a  = min(255, int(self.halo_a * 1.4))
        arc_ext = 70 if self.speaking else 42
        scan_col = self._ac(255, 30, 80, scan_a) if self.muted else self._ac(0, 212, 255, scan_a)
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr,
                     start=self.scan_angle, extent=arc_ext,
                     outline=scan_col, width=3, style="arc")
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr,
                     start=self.scan2_angle, extent=arc_ext,
                     outline=self._ac(255, 100, 0, scan_a // 2), width=2, style="arc")

        # Derecelendirme işaretleri
        t_out = int(FW * 0.495)
        t_in  = int(FW * 0.472)
        a_mk  = self._ac(0, 212, 255, 155)
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 5
            c.create_line(FCX + t_out * math.cos(rad), FCY - t_out * math.sin(rad),
                          FCX + inn  * math.cos(rad), FCY - inn  * math.sin(rad),
                          fill=a_mk, width=1)

        # Crosshair
        ch_r = int(FW * 0.50)
        gap  = int(FW * 0.15)
        ch_a = self._ac(0, 212, 255, int(self.halo_a * 0.55))
        for x1, y1, x2, y2 in [
                (FCX - ch_r, FCY, FCX - gap, FCY), (FCX + gap, FCY, FCX + ch_r, FCY),
                (FCX, FCY - ch_r, FCX, FCY - gap), (FCX, FCY + gap, FCX, FCY + ch_r)]:
            c.create_line(x1, y1, x2, y2, fill=ch_a, width=1)

        # Köşe braketleri
        blen = 22
        bc   = self._ac(0, 212, 255, 200)
        hl = FCX - FW // 2; hr = FCX + FW // 2
        ht = FCY - FW // 2; hb = FCY + FW // 2
        for bx, by, sdx, sdy in [(hl, ht, 1, 1), (hr, ht, -1, 1),
                                   (hl, hb, 1, -1), (hr, hb, -1, -1)]:
            c.create_line(bx, by, bx + sdx * blen, by,            fill=bc, width=2)
            c.create_line(bx, by, bx,               by + sdy * blen, fill=bc, width=2)

        # Yüz / orb
        if self._has_face:
            fw = int(FW * self.scale)
            if (self._face_scale_cache is None or
                    abs(self._face_scale_cache[0] - self.scale) > 0.004):
                scaled = self._face_pil.resize((fw, fw), Image.BILINEAR)
                # Mute'ta hafif kırmızı tint
                if self.muted:
                    tinted = scaled.copy()
                    r_ch, g_ch, b_ch, a_ch = tinted.split()
                    from PIL import ImageEnhance
                    g_ch = ImageEnhance.Brightness(
                        Image.fromarray(__import__('numpy').array(g_ch) // 2)
                    ).enhance(1.0) if False else g_ch   # basit: sadece önce cache
                tk_img = ImageTk.PhotoImage(scaled)
                self._face_scale_cache = (self.scale, tk_img)
            c.create_image(FCX, FCY, image=self._face_scale_cache[1])
        else:
            orb_r = int(FW * 0.27 * self.scale)
            orb_color = (255, 30, 80) if self.muted else (0, 65, 120)
            for i in range(7, 0, -1):
                r2   = int(orb_r * i / 7)
                frac = i / 7
                ga   = max(0, min(255, int(self.halo_a * 1.1 * frac)))
                c.create_oval(FCX-r2, FCY-r2, FCX+r2, FCY+r2,
                              fill=self._ac(int(orb_color[0]*frac),
                                            int(orb_color[1]*frac),
                                            int(orb_color[2]*frac), ga),
                              outline="")
            c.create_text(FCX, FCY, text=SYSTEM_NAME,
                          fill=self._ac(0, 212, 255, min(255, int(self.halo_a * 2))),
                          font=("Courier", 14, "bold"))

        # ── Header ────────────────────────────────────────────────────────────
        HDR = 62
        c.create_rectangle(0, 0, W, HDR, fill="#00080d", outline="")
        c.create_line(0, HDR, W, HDR, fill=C_MID, width=1)
        c.create_text(W // 2, 22, text=SYSTEM_NAME,
                      fill=C_PRI, font=("Courier", 18, "bold"))
        c.create_text(W // 2, 44, text="Just A Rather Very Intelligent System",
                      fill=C_MID, font=("Courier", 9))
        c.create_text(16, 31, text=MODEL_BADGE,
                      fill=C_DIM, font=("Courier", 9), anchor="w")
        c.create_text(W - 16, 31, text=time.strftime("%H:%M:%S"),
                      fill=C_PRI, font=("Courier", 14, "bold"), anchor="e")

        # ── Durum göstergesi ──────────────────────────────────────────────────
        sy = FCY + FW // 2 + 45

        if self.muted:
            stat = "⊘ MUTED"
            sc   = C_MUTED
        elif self.speaking:
            stat = "● SPEAKING"
            sc   = C_ACC
        elif self._jarvis_state == "THINKING":
            sym  = "◈" if self.status_blink else "◇"
            stat = f"{sym} THINKING"
            sc   = C_ACC2
        elif self._jarvis_state == "PROCESSING":
            sym  = "▷" if self.status_blink else "▶"
            stat = f"{sym} PROCESSING"
            sc   = C_ACC2
        elif self._jarvis_state == "LISTENING":
            sym  = "●" if self.status_blink else "○"
            stat = f"{sym} LISTENING"
            sc   = C_GREEN
        else:
            sym  = "●" if self.status_blink else "○"
            stat = f"{sym} {self.status_text}"
            sc   = C_PRI

        c.create_text(W // 2, sy, text=stat,
                      fill=sc, font=("Courier", 11, "bold"))

        # ── Ses dalgası ───────────────────────────────────────────────────────
        wy = sy + 22
        N  = 32
        BH = 18
        bw = 8
        total_w = N * bw
        wx0 = (W - total_w) // 2
        for i in range(N):
            if self.muted:
                hb  = 2
                col = C_MUTED
            elif self.speaking:
                hb  = random.randint(3, BH)
                col = C_PRI if hb > BH * 0.6 else C_MID
            else:
                hb  = int(3 + 2 * math.sin(t * 0.08 + i * 0.55))
                col = C_DIM
            bx = wx0 + i * bw
            c.create_rectangle(bx, wy + BH - hb, bx + bw - 1, wy + BH,
                                fill=col, outline="")

        # ── Footer ────────────────────────────────────────────────────────────
        c.create_rectangle(0, H - 28, W, H, fill="#00080d", outline="")
        c.create_line(0, H - 28, W, H - 28, fill=C_DIM, width=1)

        # F4 ipucu sağ tarafta
        c.create_text(W - 16, H - 14, fill=C_DIM, font=("Courier", 8),
                      text="[F4] MUTE", anchor="e")
        c.create_text(W // 2, H - 14, fill=C_DIM, font=("Courier", 8),
                      text="FatihMakes Industries  ·  CLASSIFIED  ·  MARK XXXV")

    # ── Log ───────────────────────────────────────────────────────────────────

    def write_log(self, text: str):
        """
        Thread-safe log writer. Can be called from any thread.

        БЛОК 10 ФАЗЫ 1: здесь же отмечается, что владелец на месте.
        Единственная правка в этом файле за всю фазу 1, и вот чем она куплена.

        Живая проба владельца 22.08.2026: он поставил напоминание, отошёл на
        шесть минут, вернулся, НАПЕЧАТАЛ «здаров» — и Джарвис промолчал. Замер
        показал, что голос и печать идут РАЗНЫМИ дорогами:

            микрофон -> main.py:1749 input_transcription -> разбор памяти,
                        где отметка стоит
            печать   -> ui.py:396 -> main.py:941 _on_text_command -> прямо в
                        модель, МИМО разбора памяти

        То есть напечатанное не считалось ответом ВООБЩЕ, ни при какой длине, и
        напоминание не догоняло владельца никогда. Обхода не было.

        `write_log` — единственная точка, через которую проходит ЛЮБАЯ реплика
        владельца: и голосовая (main.py:1760), и напечатанная (ui.py:396).
        Проверено грепом: других входов нет. Заодно закрывается и старая дыра
        с ответами короче шести символов («да», «ок»), которые не проходили
        порог main.py:1767.

        Правка нарочно сделана так, чтобы не могла ничего сломать: она ничего не
        рисует, не меняет ни одной существующей строки и целиком в try/except.
        Если она откажет, окно работает точно как раньше.
        """
        if text[:4].lower() == "you:":
            try:
                from core import scheduler
                scheduler.note_owner_spoke()
            except Exception:
                pass
        if self._is_ui_thread():
            self._write_log_impl(text)
        else:
            self._ui_queue.put({"type": "write_log", "text": text})

    def _write_log_impl(self, text: str):
        """
        Internal implementation — MUST only be called from UI thread.
        Handles state changes and starts typewriter animation.
        """
        self.typing_queue.append(text)
        tl = text.lower()
        if tl.startswith("you:"):
            self._set_state_impl("PROCESSING")
        elif tl.startswith("jarvis:") or tl.startswith("ai:"):
            self._set_state_impl("SPEAKING")
        if not self.is_typing:
            self._start_typing()

    def _start_typing(self):
        if not self.typing_queue:
            self.is_typing = False
            # OFFLINE не сбрасывается в LISTENING: пока связи нет, окно
            # обязано это показывать, а не возвращаться к обычному виду
            # сразу после того, как допечаталась строка об этом.
            if (not self.speaking and not self.muted
                    and getattr(self, "_jarvis_state", "") != "OFFLINE"):
                self._set_state_impl("LISTENING")
            return
        self.is_typing = True
        text = self.typing_queue.popleft()
        tl   = text.lower()
        if tl.startswith("you:"):
            tag = "you"
        elif tl.startswith("jarvis:") or tl.startswith("ai:"):
            tag = "ai"
        elif tl.startswith("err:") or "error" in tl or "failed" in tl:
            tag = "err"
        else:
            tag = "sys"
        self._type_char(text, 0, tag)

    def _type_char(self, text, i, tag):
        if i < len(text):
            self.log_text.insert(tk.END, text[i], tag)
            self.log_text.see(tk.END)
            self.root.after(8, self._type_char, text, i + 1, tag)
        else:
            self.log_text.insert(tk.END, "\n")
            self.root.after(25, self._start_typing)

    # ── Eski compat metotlar (main.py hâlâ bunları çağırabilir) ──────────────
    # These are also thread-safe since they delegate to set_state()

    def start_speaking(self):
        """Thread-safe wrapper to set SPEAKING state."""
        self.set_state("SPEAKING")

    def stop_speaking(self):
        """Thread-safe wrapper to set LISTENING state."""
        if not self.muted:
            self.set_state("LISTENING")

    # ── API key ───────────────────────────────────────────────────────────────

    def _api_keys_exist(self):
        from config.loader import is_configured
        return is_configured()

    def wait_for_api_key(self):
        while not self._api_key_ready:
            time.sleep(0.1)

    def _show_setup_ui(self):
        self.setup_frame = tk.Frame(
            self.root, bg="#00080d",
            highlightbackground=C_PRI, highlightthickness=1
        )
        self.setup_frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(self.setup_frame, text="◈  INITIALISATION REQUIRED",
                 fg=C_PRI, bg="#00080d", font=("Courier", 13, "bold")).pack(pady=(18, 4))
        tk.Label(self.setup_frame,
                 text="Enter your Gemini API key to boot J.A.R.V.I.S.",
                 fg=C_MID, bg="#00080d", font=("Courier", 9)).pack(pady=(0, 10))

        tk.Label(self.setup_frame, text="GEMINI API KEY",
                 fg=C_DIM, bg="#00080d", font=("Courier", 9)).pack(pady=(8, 2))
        self.gemini_entry = tk.Entry(
            self.setup_frame, width=52, fg=C_TEXT, bg="#000d12",
            insertbackground=C_TEXT, borderwidth=0, font=("Courier", 10), show="*"
        )
        self.gemini_entry.pack(pady=(0, 4))
        self._enable_clipboard(self.gemini_entry, editable=True)   # paste the API key on RU layout
        self._attach_menu(self.gemini_entry, editable=True)

        tk.Button(
            self.setup_frame, text="▸  INITIALISE SYSTEMS",
            command=self._save_api_keys, bg=C_BG, fg=C_PRI,
            activebackground="#003344", font=("Courier", 10),
            borderwidth=0, pady=8
        ).pack(pady=14)


    # ── Screen View ボタン ─────────────────────────────────────────────────────

    def _build_screen_view_button(self):
        """Third button in the bottom-left cluster: Screen View toggle."""
        BTN_W = 140
        BTN_H = 30
        BTN_X = 18 + 110 + 8 + 130 + 8   # after mute (110) + sc (130) + gaps
        BTN_Y = self.H - 40

        self._sv_canvas = tk.Canvas(
            self.root, width=BTN_W, height=BTN_H,
            bg=C_BG, highlightthickness=0, cursor="hand2",
        )
        self._sv_canvas.place(x=BTN_X, y=BTN_Y)
        self._sv_canvas.bind("<Button-1>", lambda e: self._on_sv_button_click())
        self._draw_sv_button("OFF")

    def _draw_sv_button(self, state_name: str, source_label: str = ""):
        """Redraw the Screen View button to reflect current manager state."""
        c = self._sv_canvas
        c.delete("all")
        configs = {
            "OFF":      (C_MID,    C_PANEL,   "○", " SCREEN VIEW", "#446677"),
            "STARTING": (C_ACC2,   "#1a1200",  "◈", " STARTING...", C_ACC2),
            "ON":       (C_GREEN,  "#001a09",  "●", " SCREEN ON",   C_GREEN),
            "STOPPING": (C_ACC,    "#1a0800",  "◎", " STOPPING...", C_ACC),
            "ERROR":    (C_RED,    "#1a0000",  "✕", " SCREEN ERR",  C_RED),
        }
        border, fill, icon, label, fg = configs.get(state_name, configs["OFF"])
        c.create_rectangle(0, 0, 140, 30, outline=border, fill=fill, width=1)
        # When ON and a source label is available, show a short source hint
        if state_name == "ON" and source_label:
            # Two-line layout: icon+state top, source bottom
            short = source_label[:16]
            c.create_text(70, 9, text=f"{icon}{label}", fill=fg,
                          font=("Courier", 8, "bold"))
            c.create_text(70, 22, text=short, fill=fg,
                          font=("Courier", 7))
        else:
            c.create_text(70, 15, text=f"{icon}{label}", fill=fg,
                          font=("Courier", 10, "bold"))

    def _on_sv_button_click(self):
        """
        Variant A: bottom button always opens / raises the Screen View panel.
        START and STOP controls live inside the panel itself.
        """
        self._open_or_raise_sv_panel()

    def _open_or_raise_sv_panel(self):
        """
        Open the Screen View panel if it does not exist yet; if it was
        withdrawn (closed via the X button), restore it; otherwise just
        bring it to the front.
        """
        if self._sv_panel is None:
            # First time: build the panel
            self._open_sv_panel()
            return

        try:
            exists = self._sv_panel.winfo_exists()
        except Exception:
            exists = False

        if not exists:
            # Panel was destroyed (shouldn't happen with withdraw, but be safe)
            self._sv_panel = None
            self._open_sv_panel()
            return

        # Panel exists — restore if withdrawn, then raise to front
        self._sv_panel.deiconify()
        self._sv_panel.lift()
        self._sv_panel.focus_force()

    # ── Screen View panel ──────────────────────────────────────────────────────

    def _open_sv_panel(self):
        """Create and show the Screen View management Toplevel."""
        from core.screen_share_manager import (
            get_manager, ScreenShareSource, ScreenShareState
        )

        panel = tk.Toplevel(self.root)
        panel.title("Screen View")
        panel.resizable(False, False)
        panel.configure(bg=C_BG)
        panel.attributes("-topmost", True)

        # Position near the button
        BTN_X = 18 + 110 + 8 + 130 + 8
        BTN_Y = self.H - 40
        x     = self.root.winfo_x() + BTN_X
        y     = self.root.winfo_y() + BTN_Y - 230
        panel.geometry(f"310x225+{x}+{y}")

        # Keep reference
        self._sv_panel = panel

        # ── Header ────────────────────────────────────────────────────────────
        tk.Label(panel, text="◈  SCREEN VIEW", fg=C_PRI, bg=C_BG,
                 font=("Courier", 12, "bold")).pack(pady=(14, 2))
        tk.Frame(panel, bg=C_MID, height=1).pack(fill="x", padx=16)

        # ── Status row ────────────────────────────────────────────────────────
        stat_frame = tk.Frame(panel, bg=C_BG)
        stat_frame.pack(pady=(10, 4))
        tk.Label(stat_frame, text="STATUS:", fg=C_DIM, bg=C_BG,
                 font=("Courier", 9)).pack(side="left")
        self._sv_status_label = tk.Label(stat_frame, text="OFF", fg="#446677",
                                          bg=C_BG, font=("Courier", 9, "bold"))
        self._sv_status_label.pack(side="left", padx=(6, 0))

        # ── Source selector ──────────────────────────────────────────────────
        tk.Label(panel, text="CAPTURE SOURCE:", fg=C_DIM, bg=C_BG,
                 font=("Courier", 8)).pack(pady=(6, 0))

        src_frame = tk.Frame(panel, bg=C_BG)
        src_frame.pack(pady=(4, 2))

        self._sv_source_var = tk.StringVar(value="full")

        tk.Radiobutton(
            src_frame,
            text="■ Entire Screen",
            variable=self._sv_source_var,
            value="full",
            command=self._sv_on_source_changed,
            fg=C_PRI, bg=C_BG, selectcolor=C_DIMMER, activebackground=C_BG,
            font=("Courier", 9, "bold"),
        ).pack(side="left", padx=(12, 6))

        tk.Radiobutton(
            src_frame,
            text="□ Specific Window",
            variable=self._sv_source_var,
            value="window",
            command=self._sv_on_source_changed,
            fg=C_TEXT, bg=C_BG, selectcolor=C_DIMMER, activebackground=C_BG,
            font=("Courier", 9),
        ).pack(side="left", padx=(6, 12))

        # ── Window selector (visible only when "Specific Window" is selected) ──
        self._sv_win_frame = tk.Frame(panel, bg=C_BG)
        self._sv_win_frame.pack(pady=(2, 0))

        tk.Label(self._sv_win_frame, text="Window:", fg=C_DIM, bg=C_BG,
                 font=("Courier", 8)).pack(side="left", padx=(12, 4))

        self._sv_win_var = tk.StringVar(value="Select window...")
        self._sv_win_menu = tk.OptionMenu(
            self._sv_win_frame, self._sv_win_var, "Select window..."
        )
        self._sv_win_menu.configure(
            fg=C_TEXT, bg=C_PANEL, activebackground=C_DIM, activeforeground=C_PRI,
            font=("Courier", 8), width=22, borderwidth=0,
            highlightthickness=1, highlightbackground=C_DIM,
        )
        self._sv_win_menu.pack(side="left")

        tk.Button(
            self._sv_win_frame, text="↻", command=self._sv_on_refresh_windows,
            fg=C_PRI, bg=C_PANEL, font=("Courier", 10, "bold"),
            borderwidth=0, cursor="hand2", padx=6,
        ).pack(side="left", padx=(4, 0))

        # Populate + hide initially
        self._sv_on_refresh_windows()
        self._sv_update_win_frame_visibility()

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(panel, bg=C_DIM, height=1).pack(fill="x", padx=16, pady=(10, 0))

        # ── Start / Stop button ───────────────────────────────────────────────
        self._sv_startstop_btn = tk.Button(
          panel, text="▸  START", command=self._sv_on_start_stop,
          fg=C_PRI, bg=C_PANEL, activeforeground=C_BG, activebackground=C_PRI,
          font=("Courier", 10, "bold"), borderwidth=0, cursor="hand2",
          highlightthickness=1, highlightbackground=C_MID, pady=6,
        )
        self._sv_startstop_btn.pack(pady=(10, 14), ipadx=10)

        # Close button
        panel.protocol("WM_DELETE_WINDOW", panel.withdraw)

        # Sync initial state immediately
        self._sv_sync_panel_to_state()

    def _sv_update_win_frame_visibility(self):
        """Show/hide the window selector row based on source radio selection."""
        if self._sv_source_var is None or self._sv_win_frame is None:
          return
        if self._sv_source_var.get() == "window":
          self._sv_win_frame.pack(pady=(2, 0))
        else:
          self._sv_win_frame.pack_forget()

    def _sv_on_source_changed(self):
        """Called when user switches source radio button."""
        self._sv_update_win_frame_visibility()
        # If already ON, push the new source to the manager immediately
        from core.screen_share_manager import get_manager, ScreenShareSource, ScreenShareState
        mgr = get_manager()
        if mgr.get_state() == ScreenShareState.ON:
          src = self._sv_build_source()
          if src:
              mgr.set_source(src)

    def _sv_on_refresh_windows(self):
        """Refresh the list of available windows."""
        from core.screen_share_manager import get_manager, ScreenShareSource
        wins = ScreenShareManager_list_windows()
        self._sv_windows = wins
        if self._sv_win_menu is None or self._sv_win_var is None:
          return
        menu = self._sv_win_menu["menu"]
        menu.delete(0, "end")
        for w in wins:
          label = w.display_name()
          menu.add_command(
              label=label,
              command=lambda lbl=label: self._sv_win_var.set(lbl),
          )
        if wins:
          self._sv_win_var.set(wins[0].display_name())

    def _sv_build_source(self):
        """Build a ScreenShareSource from current panel selections. Returns None on error."""
        from core.screen_share_manager import ScreenShareSource
        if self._sv_source_var is None:
          return None
        src_type = self._sv_source_var.get()
        if src_type == "full":
          return ScreenShareSource(type="full", monitor_index=1, title="Entire Screen")
        # window mode — find matching entry
        selected_label = (self._sv_win_var.get() if self._sv_win_var else "")
        for w in self._sv_windows:
          if w.display_name() == selected_label:
              return w
        # No match — fall back to full screen
        return ScreenShareSource(type="full", monitor_index=1, title="Entire Screen")

    def _sv_on_start_stop(self):
        """Handle Start / Stop button click."""
        from core.screen_share_manager import get_manager, ScreenShareState
        mgr   = get_manager()
        state = mgr.get_state()

        if state in (ScreenShareState.STARTING, ScreenShareState.STOPPING):
          return  # button disabled during transition — silently ignore

        if state in (ScreenShareState.ON,):
          msg = mgr.request_stop()
          self.write_log(f"SYS: {msg}")
        else:
          # OFF or ERROR → start
          src = self._sv_build_source()
          if src:
              mgr.set_source(src)
          msg = mgr.request_start()
          self.write_log(f"SYS: {msg}")

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _poll_sv_state(self):
        """
        Poll ScreenShareManager every 400ms and update:
        - the bottom-bar Screen View button
        - the management panel (if open)
        This runs in the UI thread via root.after() — safe to touch widgets.
        """
        try:
          from core.screen_share_manager import get_manager, ScreenShareState
          mgr        = get_manager()
          state      = mgr.get_state()
          state_name = state.name

          # Update bottom-bar button
          src_for_btn = mgr.get_source().display_name() if state == ScreenShareState.ON else ""
          self._draw_sv_button(state_name, source_label=src_for_btn)

          # Update panel if visible
          self._sv_sync_panel_to_state()

          # Update dialogue state if screen share is active
          try:
              from core.dialogue_state import update_screen_share as _ds_sv
              src = mgr.get_source()
              _ds_sv(
                  active      = (state == ScreenShareState.ON),
                  source_type = src.type,
                  window_title= src.title if src.type == "window" else None,
              )
          except Exception:
              pass

          # Keep the SCREEN-control (clicking) flag in sync every poll so the
          # model's context never drifts from the live button, even across
          # reconnects or missed toggles.
          try:
              from core.dialogue_state import set_screen_control as _ds_sc
              _ds_sc(bool(self.screen_control))
          except Exception:
              pass

        except Exception:
          pass

        self.root.after(400, self._poll_sv_state)

    def _sv_sync_panel_to_state(self):
        """Sync the management panel widgets to the current manager state."""
        if self._sv_panel is None:
          return
        try:
          if not self._sv_panel.winfo_exists():
              return
        except Exception:
          return

        from core.screen_share_manager import get_manager, ScreenShareState
        mgr   = get_manager()
        state = mgr.get_state()
        info  = mgr.get_status()

        _state_colors = {
          "OFF":      "#446677",
          "STARTING": C_ACC2,
          "ON":       C_GREEN,
          "STOPPING": C_ACC,
          "ERROR":    C_RED,
        }
        sn   = state.name
        col  = _state_colors.get(sn, C_DIM)
        text = sn
        if sn == "ERROR":
          text = f"ERROR: {info.get('error', '')[:30]}"
        elif sn == "ON":
          age = info.get("frame_age", "?")
          text = f"ON  (frame {age}s ago)"

        if self._sv_status_label:
          self._sv_status_label.configure(text=text, fg=col)

        # Start/Stop button state
        if self._sv_startstop_btn:
          if state in (ScreenShareState.STARTING, ScreenShareState.STOPPING):
              self._sv_startstop_btn.configure(
                  state="disabled", text="◈  PLEASE WAIT", fg=C_DIM
              )
          elif state == ScreenShareState.ON:
              self._sv_startstop_btn.configure(
                  state="normal", text="■  STOP", fg=C_RED
              )
          else:
              self._sv_startstop_btn.configure(
                  state="normal", text="▸  START", fg=C_PRI
              )



    # ── API key ─────────────────────────────────────────────────────────────

    def _save_api_keys(self):
        gemini = self.gemini_entry.get().strip()
        if not gemini:
          return
        # read-merge-write через единый config: прежняя реализация деструктивно
        # перезаписывала файл одним ключом, стирая camera_index/timezone.
        from config.loader import set_secret
        set_secret("gemini_api_key", gemini)
        self.setup_frame.destroy()
        self._api_key_ready = True
        self.set_state("LISTENING")
        self.write_log("SYS: Systems initialised. JARVIS online.")



def ScreenShareManager_list_windows():
  """Thin wrapper so ui.py doesn't need a top-level import."""
  try:
        from core.screen_share_manager import ScreenShareManager
        return ScreenShareManager.list_windows()
  except Exception:
        from core.screen_share_manager import ScreenShareSource
        return [ScreenShareSource(type="full", monitor_index=1, title="Entire Screen")]
