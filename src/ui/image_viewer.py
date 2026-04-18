import os
from typing import Optional
from tkinter import messagebox
import customtkinter as ctk
from PIL import Image
from .constants import BG_DARK, BG_CARD, TEXT_MUTED, ACCENT, DANGER
from .components.video_player import VideoPlayer



class ImageViewer(ctk.CTkToplevel):
    def __init__(self, parent, paths: list[str], start_index: int, on_delete_request, **kwargs):
        super().__init__(parent)
        self.title("Image Viewer")

        self.paths = list(paths)
        self.index = max(0, min(start_index, len(paths) - 1))
        self.on_delete_request = on_delete_request
        self.on_toggle_selection = kwargs.get("on_toggle_selection")
        self.is_selected_callback = kwargs.get("is_selected_callback")

        # IMPORTANT: keep a strong reference so GC doesn't destroy the image
        self._ctk_img = None
        self.video_player: Optional[VideoPlayer] = None

        self.configure(fg_color=BG_DARK)

        # Maximize the window
        try:
            self.state("zoomed")         # Windows
        except Exception:
            self.attributes("-zoomed", True)  # Linux fallback

        self.focus_force()
        self.grab_set()

        self._build_ui()
        self.bind("<Left>", lambda e: self._prev())
        self.bind("<Right>", lambda e: self._next())
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Delete>", lambda e: self._delete_current())
        self.bind("<Configure>", self._on_resize)

        # Show image after the window is fully drawn
        self.after(100, self._show_current)

    def _build_ui(self):
        # Top info bar
        self.top = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=52)
        self.top.pack(fill="x", side="top")
        
        self.info_label = ctk.CTkLabel(self.top, text="", font=ctk.CTkFont(size=13, weight="bold"), text_color="white")
        self.info_label.pack(side="left", padx=20, pady=12)

        # Middle: Selection checkbox
        self.select_cb = ctk.CTkCheckBox(self.top, text="Select for deletion", 
                                          font=ctk.CTkFont(size=12, weight="bold"),
                                          command=self._toggle_selection)
        self.select_cb.pack(side="left", padx=20)

        # Right side: Close and Delete buttons (Close first for safety, then Delete)
        ctk.CTkButton(self.top, text="✕ Close", width=90, height=32, fg_color="transparent",
                      hover_color="#334155", font=ctk.CTkFont(weight="bold"), 
                      command=self.destroy).pack(side="right", padx=16, pady=10)
        
        self.del_btn = ctk.CTkButton(
            self.top, text="🗑  Delete File", width=120, height=32,
            fg_color=DANGER, hover_color="#B91C1C", font=ctk.CTkFont(weight="bold"),
            command=self._delete_current
        )
        self.del_btn.pack(side="right", padx=4, pady=10)

        self.open_ext_btn = ctk.CTkButton(
            self.top, text="⧉ Open Externally", width=130, height=32,
            fg_color="#334155", hover_color="#475569", font=ctk.CTkFont(weight="bold"),
            command=self._open_externally
        )
        self.open_ext_btn.pack(side="right", padx=4, pady=10)

        # Center/Leftish: Path label (flexible)
        self.path_label = ctk.CTkLabel(self.top, text="", font=ctk.CTkFont(size=11),
                                       text_color=TEXT_MUTED, anchor="w")
        self.path_label.pack(side="left", fill="x", expand=True, padx=20)

        # Image canvas area
        self.img_frame = ctk.CTkFrame(self, fg_color=BG_DARK)
        self.img_frame.pack(fill="both", expand=True)
        self.img_frame.grid_rowconfigure(0, weight=1)
        self.img_frame.grid_columnconfigure(1, weight=1)

        # Prev button
        self.prev_btn = ctk.CTkButton(
            self.img_frame, text="‹", width=56, height=100,
            font=ctk.CTkFont(size=32, weight="bold"),
            fg_color="#1E293B", hover_color="#334155",
            text_color="white", command=self._prev
        )
        self.prev_btn.grid(row=0, column=0, padx=8, pady=8, sticky="nsew")

        # Image label (center)
        self.img_label = ctk.CTkLabel(self.img_frame, text="Loading...",
                                       fg_color="transparent", text_color=TEXT_MUTED)
        self.img_label.grid(row=0, column=1, sticky="nsew")

        # Next button
        self.next_btn = ctk.CTkButton(
            self.img_frame, text="›", width=56, height=100,
            font=ctk.CTkFont(size=32, weight="bold"),
            fg_color="#1E293B", hover_color="#334155",
            text_color="white", command=self._next
        )
        self.next_btn.grid(row=0, column=2, padx=8, pady=8, sticky="nsew")

    def _on_resize(self, event=None):
        if hasattr(self, "_resize_timer"):
            self.after_cancel(self._resize_timer)
        self._resize_timer = self.after(120, self._show_current)

    def _show_current(self):
        if not self.paths:
            self.destroy()
            return

        path = self.paths[self.index]
        self.info_label.configure(text=f"{self.index + 1} / {len(self.paths)}")
        self.path_label.configure(text=path)
        
        # Update selection checkbox
        if self.is_selected_callback:
            selected = self.is_selected_callback(path)
            if selected: self.select_cb.select()
            else: self.select_cb.deselect()

        from folder_scanner import VIDEO_EXTENSIONS
        ext = os.path.splitext(path)[1].lower()
        
        # Cleanup previous video player if it exists
        if self.video_player:
            self.video_player.stop()
            self.video_player.destroy()
            self.video_player = None

        if ext in VIDEO_EXTENSIONS:
            self._ctk_img = None
            self.img_label.grid_remove() # Hide image label
            
            self.video_player = VideoPlayer(self.img_frame, video_path=path, fg_color=BG_DARK)
            self.video_player.grid(row=0, column=1, sticky="nsew")
            return
        else:
            self.img_label.grid() # Show image label

        try:
            img_pil = Image.open(path)
            img_pil = img_pil.convert("RGBA") if img_pil.mode in ("P", "RGBA") else img_pil.convert("RGB")

            self.update_idletasks()
            # Use the image frame's size, not the full window
            frame_w = self.img_label.winfo_width()
            frame_h = self.img_label.winfo_height()

            if frame_w < 100:
                frame_w = self.winfo_width() - 140   # subtract nav buttons
            if frame_h < 100:
                frame_h = self.winfo_height() - 60  # subtract top bar

            # Clamp to sane minimums
            frame_w = max(frame_w, 400)
            frame_h = max(frame_h, 300)

            ratio = min(frame_w / img_pil.width, frame_h / img_pil.height)
            new_size = (max(1, int(img_pil.width * ratio)), max(1, int(img_pil.height * ratio)))

            # Keep strong reference on self — critical to prevent GC blank image
            self._ctk_img = ctk.CTkImage(img_pil, size=new_size)
            
            # Wrap terminal UI update in try-except to catch TclError if the image handle is stale
            try:
                self.img_label.configure(image=self._ctk_img, text="")
            except Exception as e:
                # If we get a TclError about a missing image, it usually means 
                # a rapid update invalidated the handle. We can ignore or log it.
                if "doesn't exist" in str(e):
                    pass
                else:
                    raise e
        except Exception as e:
            self._ctk_img = None
            try:
                self.img_label.configure(image=None, text=f"⚠  Could not load image\n{e}",
                                         text_color=TEXT_MUTED)
            except Exception:
                pass # Window might be closing

    def _open_externally(self):
        if not self.paths:
            return
        path = self.paths[self.index]
        import platform
        import subprocess
        try:
            if platform.system() == 'Windows':
                os.startfile(path)
            elif platform.system() == 'Darwin':
                subprocess.call(('open', path))
            else:
                subprocess.call(('xdg-open', path))
        except Exception as e:
            messagebox.showerror("Error", f"Could not open file: {e}")

    def _prev(self):
        if not self.paths:
            return
        self.index = (self.index - 1) % len(self.paths)
        self._show_current()

    def _next(self):
        if not self.paths:
            return
        self.index = (self.index + 1) % len(self.paths)
        self._show_current()

    def _delete_current(self):
        if not self.paths:
            return
        path = self.paths[self.index]
        if self.on_delete_request(path):   # delete callback handles confirm + trash
            self.paths.pop(self.index)
            if not self.paths:
                self.destroy()
            else:
                self.index = self.index % len(self.paths)
                self._show_current()

    def _toggle_selection(self):
        if not self.paths: return
        path = self.paths[self.index]
        if self.on_toggle_selection:
            self.on_toggle_selection(path, self.select_cb.get())

    def destroy(self):
        if self.video_player:
            self.video_player.stop()
        super().destroy()
