import os
import cv2
import PIL.Image
import customtkinter as ctk
import logging
from typing import Optional

log = logging.getLogger(__name__)

class VideoPlayer(ctk.CTkFrame):
    """A simple video player component using OpenCV and CustomTkinter."""
    
    def __init__(self, master, video_path: str, **kwargs):
        super().__init__(master, **kwargs)
        
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)
        
        if not self.cap.isOpened():
            log.error(f"Failed to open video file: {video_path}")
            self.error_label = ctk.CTkLabel(self, text=f"Failed to load video:\n{os.path.basename(video_path)}")
            self.error_label.pack(expand=True)
            return

        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        if self.fps <= 0: self.fps = 30
        self.frame_delay = int(1000 / self.fps)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration = self.total_frames / self.fps
        
        self.is_playing = True
        self.after_id = None
        
        self._build_ui()
        self.bind("<Configure>", self._on_resize)
        
        # Start playback
        self._update_frame()

    def _build_ui(self):
        # Image Display
        self.display_label = ctk.CTkLabel(self, text="", fg_color="black")
        self.display_label.pack(fill="both", expand=True, pady=(0, 10))
        
        # Controls Frame
        self.controls = ctk.CTkFrame(self, fg_color="transparent")
        self.controls.pack(fill="x", side="bottom", padx=10, pady=(0, 10))
        
        # Play/Pause Button
        self.play_btn = ctk.CTkButton(self.controls, text="⏸", width=40, height=30, 
                                      command=self.toggle_play)
        self.play_btn.pack(side="left", padx=(0, 10))
        
        # Time Label
        self.time_label = ctk.CTkLabel(self.controls, text="00:00 / 00:00", font=("Arial", 12))
        self.time_label.pack(side="left", padx=(0, 10))
        
        # Slider for seeking
        self.slider = ctk.CTkSlider(self.controls, from_=0, to=self.total_frames - 1, 
                                    number_of_steps=self.total_frames,
                                    command=self._on_seek)
        self.slider.set(0)
        self.slider.pack(side="left", fill="x", expand=True)

    def toggle_play(self):
        self.is_playing = not self.is_playing
        self.play_btn.configure(text="⏸" if self.is_playing else "▶")
        if self.is_playing:
            self._update_frame()

    def _on_seek(self, value):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(value))
        if not self.is_playing:
            # Show the frame at the new position even if paused
            self._fetch_and_display()

    def _on_resize(self, event=None):
        # Debounce or just update on next frame
        pass

    def _format_time(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    def _fetch_and_display(self) -> bool:
        ret, frame = self.cap.read()
        if not ret:
            # Loop video or stop? Let's loop for now
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self.cap.read()
            if not ret: return False
            
        # Current progress
        current_frame = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        current_time = current_frame / self.fps
        
        # Update UI components
        self.slider.set(current_frame)
        self.time_label.configure(text=f"{self._format_time(current_time)} / {self._format_time(self.duration)}")
        
        # Prepare image
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img_pil = PIL.Image.fromarray(frame_rgb)
        
        # Resize to fit display_label
        display_w = self.display_label.winfo_width()
        display_h = self.display_label.winfo_height()
        
        if display_w > 10 and display_h > 10:
            ratio = min(display_w / img_pil.width, display_h / img_pil.height)
            new_size = (int(img_pil.width * ratio), int(img_pil.height * ratio))
            img_pil = img_pil.resize(new_size, PIL.Image.Resampling.LANCZOS)
        
        ctk_img = ctk.CTkImage(img_pil, size=img_pil.size)
        self.display_label.configure(image=ctk_img)
        self.display_label._ctk_img = ctk_img # Keep reference
        return True

    def _update_frame(self):
        if not self.is_playing:
            return
            
        if self._fetch_and_display():
            self.after_id = self.after(self.frame_delay, self._update_frame)
        else:
            self.is_playing = False
            self.play_btn.configure(text="▶")

    def stop(self):
        """Releases resources. Call this when destroying the parent."""
        self.is_playing = False
        if self.after_id:
            self.after_cancel(self.after_id)
        if self.cap:
            self.cap.release()
