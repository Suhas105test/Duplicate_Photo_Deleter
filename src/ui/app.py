import logging
import os
import time
import traceback
import threading
import queue
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from folder_scanner import (
    scan_folder, scan_files, 
    VIDEO_EXTENSIONS, IMAGE_EXTENSIONS, DOCUMENT_EXTENSIONS
)
from hash_generator import generate_hashes, find_exact_byte_duplicates
from duplicate_detector import detect_duplicates, DetectionResult
from delete_manager import (
    delete_files, verify_paths_exist, 
    has_undoable_deletes, restore_last_deleted
)
import compressor

# Internal Modular Imports
from .constants import (
    BG_DARK, BG_CARD, ACCENT, DANGER, SUCCESS, WARN, TEXT_MUTED, TEXT_DIM, WARM_ORANGE, BG_THUMB
)
from .models import ScanSettings
from .thumbnail_cache import ThumbnailCache
from .image_viewer import ImageViewer
from .components.duplicate_group_card import DuplicateGroupCard
from .components.file_grid_card import FileGridCard

log = logging.getLogger(__name__)

class SmartPhotoCleanerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Smart Photo Cleaner")
        self.geometry("1160x860")
        self.minsize(960, 680)
        self.configure(fg_color=BG_DARK)

        self._folder_path: Optional[str] = None
        self._selected_files_list: Optional[list[str]] = None
        self._scan_request_tab: Optional[str] = None  # which tab initiated the scan
        self._detection_result: Optional[DetectionResult] = None
        self._exact_group_cards: list[DuplicateGroupCard] = []
        self._similar_group_cards: list[DuplicateGroupCard] = []
        self._screenshot_cards: list[FileGridCard] = []
        self._blurry_cards: list[FileGridCard] = []
        self._large_cards: list[FileGridCard] = []
        self._message_cards: list[FileGridCard] = []
        self._timeline_cards: list[FileGridCard] = []
        # heatmap feature removed
        self._progress_queue: queue.Queue = queue.Queue()
        self._is_scanning   = False
        self._is_paused     = False
        self._cancel_event  = threading.Event()
        self._pause_event   = threading.Event()
        self._settings      = ScanSettings()
        self._thumb_cache   = ThumbnailCache(max_size=200, num_threads=4)
        self._elapsed_timer = None

        # Session Stats
        self._scan_start_time: float = 0.0
        self._last_count: int = 0  # track latest file count seen from progress updates
        self._session_deleted_count: int = 0
        self._session_saved_bytes: int = 0
        self._tab_descriptions = {
            "Dashboard": "Central command center for scanning folders or specific files. Use this to start a new cleanup session.\n\nExample: Select your 'Downloads' folder to find all duplicate images.",
            "Duplicates": "Find exact byte-for-byte copies of files. Deleting these is 100% safe as they are identical.\n\nExample: Two identical 'IMG_001.jpg' files in different folders.",
            "Similar Photos": "Identifies visually similar photos (burst shots, slightly different angles, or resized versions). Adjust the threshold slider to control strictness.\n\nExample: A series of portrait shots where only one is perfect.",
            "Screenshots": "Specifically filters for screenshots, which often clutter galleries.\n\nExample: Temporary captures of maps or messages you no longer need.",
            "Messages Media": "Isolates media saved from apps like WhatsApp, Telegram, or Messenger.\n\nExample: Many copies of the same meme sent in different group chats.",
            "Blurry Photos": "AI-powered detection of low-quality, out-of-focus images.\n\nExample: Accidental pocket photos or failed night-time captures.",
            "Large Files": "Finds the biggest space-hogging files in your library.\n\nExample: 4K video recordings or large RAW image files.",
            "Timeline Viewer": "Browse your media chronologically. Great for finding old forgotten photos by year.\n\nExample: Quickly jumping to photos from '2021' to free up space.",
            "Media Compressor": "Reduce file sizes by up to 90% while maintaining visual quality. Supports both images and videos.\n\nExample: Shrinking a 5GB video folder down to 500MB for sharing.",
            "Settings": "Configure app behavior, performance modes, and file handling preferences."
        }
        self._tab_modes = {
            "Duplicates": "duplicates",
            "Similar Photos": "similar",
            "Screenshots": "screenshots",
            "Blurry Photos": "blurry",
            "Large Files": "large",
            "Messages Media": "messages",
            "Timeline Viewer": "timeline",
        }

        log = logging.getLogger("ui.app.SmartPhotoCleanerApp")
        log.info("Building layout...")
        self._build_layout()
        log.info("Layout built successfully")
        
        log.info("Starting progress polling...")
        self._poll_progress()
        log.info("Progress polling started")
        
        log.info("Setting up window close handler...")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # Toast Notification Frame (initialized last to be on top)
        self._toast_frame = ctk.CTkFrame(self, fg_color="#10B981", corner_radius=20)
        self._toast_label = ctk.CTkLabel(self._toast_frame, text="", text_color="white", font=ctk.CTkFont(size=13, weight="bold"))
        self._toast_label.pack(padx=20, pady=8)

        log.info("SmartPhotoCleanerApp initialization complete!")

    def _on_close(self):
        self._cancel_event.set()
        self._thumb_cache.shutdown()
        self.destroy()

    def _build_layout(self):
        log = logging.getLogger("ui.app._build_layout")
        log.info("Starting _build_layout...")
        
        try:
            log.info("Configuring grid...")
            self.grid_rowconfigure(0, weight=1)
            self.grid_columnconfigure(0, weight=0)
            self.grid_columnconfigure(1, weight=1)
            log.info("Grid configured")

            # Sidebar
            log.info("Creating sidebar...")
            self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color=BG_CARD)
            self.sidebar_frame.grid(row=0, column=0, sticky="nsew")

            ctk.CTkLabel(
                self.sidebar_frame, text="Smart Photo\nCleaner",
                font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT
            ).grid(row=0, column=0, padx=20, pady=(20, 30))
            log.info("Sidebar created")

            self.nav_btns = {}
            items = ["Dashboard", "Duplicates", "Screenshots", "Messages Media", "Similar Photos", "Blurry Photos", "Large Files", "Timeline Viewer", "Media Compressor", "Settings"]
            for i, text in enumerate(items):
                btn = ctk.CTkButton(
                    self.sidebar_frame, text=text, fg_color="transparent", text_color="white",
                    hover_color="#334155", anchor="w", command=lambda t=text: self.select_frame_by_name(t)
                )
                btn.grid(row=i+1, column=0, padx=10, pady=5, sticky="ew")
                self.nav_btns[text] = btn

            self.sidebar_frame.grid_rowconfigure(12, weight=1)
            self._theme_btn = ctk.CTkButton(self.sidebar_frame, text="☀ Light Mode", command=self._toggle_theme)
            self._theme_btn.grid(row=13, column=0, padx=20, pady=20)
            log.info("Navigation buttons created")

            # Main
            log.info("Creating main container...")
            self.main_container = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
            self.main_container.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
            log.info("Main container created")

            log.info("Initializing frames...")
            self.frames = {}
            self._init_frames()
            log.info("Frames initialized")

            # Tab headline (created once, updated per tab)
            log.info("Creating tab headline...")
            self._current_tab_label = ctk.CTkLabel(
                self.main_container,
                text="",
                font=ctk.CTkFont(size=12),
                text_color=TEXT_MUTED
            )
            log.info("Tab headline created")

            # Action Bar
            log.info("Creating action bar...")
            self._action_bar = ctk.CTkFrame(self.main_container, fg_color=BG_CARD, corner_radius=12)
            self._delete_btn = ctk.CTkButton(self._action_bar, text="🗑  Delete Selected", fg_color=DANGER, command=self._delete_selected, state="disabled")
            self._delete_btn.pack(side="left", padx=12, pady=10)
            self._compress_btn = ctk.CTkButton(self._action_bar, text="🗜 Compress Selected", fg_color="#0EA5E9", command=self._compress_selected, state="disabled")
            self._compress_btn.pack(side="left", padx=(0, 8), pady=10)
            self._quick_clean_btn = ctk.CTkButton(self._action_bar, text="⚡ Quick Clean", fg_color=WARN, command=self._quick_clean, state="disabled")
            self._quick_clean_btn.pack(side="left", padx=(0, 8), pady=10)
            self._select_all_btn = ctk.CTkButton(self._action_bar, text="✓  Auto-Select", command=self._select_all_duplicates, state="disabled")
            self._select_all_btn.pack(side="left", padx=(0, 8), pady=10)
            self._undo_btn = ctk.CTkButton(self._action_bar, text="↶  Undo", fg_color="#D97706", command=self._undo_last_delete)
            self._result_label = ctk.CTkLabel(self._action_bar, text="", text_color=SUCCESS)
            self._result_label.pack(side="right", padx=16)
            log.info("Action bar created")

            log.info("Selecting Dashboard frame...")
            self.select_frame_by_name("Dashboard")
            log.info("_build_layout completed successfully!")
        except Exception as e:
            log.error(f"Error in _build_layout: {str(e)}", exc_info=True)
            raise

    def _init_frames(self):
        dash = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Dashboard"] = dash
        
        # Dashboard Header
        header = ctk.CTkFrame(dash, fg_color="transparent")
        header.pack(fill="x", pady=(10, 20))
        ctk.CTkLabel(header, text="Dashboard", font=ctk.CTkFont(size=24, weight="bold")).pack(side="left", anchor="w")
        ctk.CTkButton(header, text="ℹ", width=30, height=30, command=lambda: self._show_tab_info("Dashboard")).pack(side="right", padx=(10, 0))
        
        self._build_dashboard(dash)

        # keep track of tab-specific scan buttons so we can enable/disable them
        self._tab_scan_buttons: list[ctk.CTkButton] = []
        for name, attr in [("Duplicates", "_scroll_exact"), ("Screenshots", "_scroll_screenshots"), ("Messages Media", "_scroll_messages"), ("Similar Photos", "_scroll_similar"), ("Blurry Photos", "_scroll_blurry"), ("Large Files", "_scroll_large"), ("Timeline Viewer", "_scroll_timeline")]:
            f = ctk.CTkFrame(self.main_container, fg_color="transparent")
            self.frames[name] = f
            # Header with title and info button
            header = ctk.CTkFrame(f, fg_color="transparent")
            header.pack(fill="x", pady=(10, 20))
            ctk.CTkLabel(header, text=name, font=ctk.CTkFont(size=24, weight="bold")).pack(side="left", anchor="w")
            info_btn = ctk.CTkButton(header, text="ℹ", width=30, height=30, command=lambda n=name: self._show_tab_info(n))
            info_btn.pack(side="right", padx=(10, 0))
            # each feature tab gets its own scan/refresh button
            btn_box = ctk.CTkFrame(f, fg_color="transparent")
            btn_box.pack(anchor="ne", padx=20, pady=(0, 10))
            
            if name == "Similar Photos":
                # Add similarity slider directly here
                sim_row = ctk.CTkFrame(btn_box, fg_color="transparent")
                sim_row.pack(side="left", padx=20)
                ctk.CTkLabel(sim_row, text="Threshold:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=5)
                self._tol_slider = ctk.CTkSlider(sim_row, from_=0, to=10, width=120, number_of_steps=10, command=self._on_slider_change)
                self._tol_slider.set(self._settings.tolerance)
                self._tol_slider.pack(side="left", padx=5)
                self._tol_label = ctk.CTkLabel(sim_row, text=str(self._settings.tolerance), font=ctk.CTkFont(size=11), width=30)
                self._tol_label.pack(side="left", padx=5)

            scan_btn = ctk.CTkButton(btn_box, text=f"🔍 Scan {name}", command=lambda n=name: self._scan_from_tab(n, self._tab_modes.get(n, "photos")))
            scan_btn.pack(side="right")
            self._tab_scan_buttons.append(scan_btn)
            s = ctk.CTkScrollableFrame(f, fg_color="transparent")
            s.pack(fill="both", expand=True)
            setattr(self, attr, s)
            
            # Bind infinite scroll
            s._parent_canvas.bind("<MouseWheel>", lambda e, n=name: self._on_tab_scroll(n))
            s._parent_canvas.bind("<Button-4>", lambda e, n=name: self._on_tab_scroll(n)) # Linux scroll up
            s._parent_canvas.bind("<Button-5>", lambda e, n=name: self._on_tab_scroll(n)) # Linux scroll down

        stg = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Settings"] = stg
        # Header for Settings
        header = ctk.CTkFrame(stg, fg_color="transparent")
        header.pack(fill="x", pady=(10, 20))
        ctk.CTkLabel(header, text="Settings", font=ctk.CTkFont(size=24, weight="bold")).pack(side="left", anchor="w")
        info_btn = ctk.CTkButton(header, text="ℹ", width=30, height=30, command=lambda: self._show_tab_info("Settings"))
        info_btn.pack(side="right", padx=(10, 0))
        self._build_settings(stg)
        comp = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Media Compressor"] = comp
        # Header for Media Compressor
        header = ctk.CTkFrame(comp, fg_color="transparent")
        header.pack(fill="x", pady=(10, 20))
        ctk.CTkLabel(header, text="Media Compressor", font=ctk.CTkFont(size=24, weight="bold")).pack(side="left", anchor="w")
        info_btn = ctk.CTkButton(header, text="ℹ", width=30, height=30, command=lambda: self._show_tab_info("Media Compressor"))
        info_btn.pack(side="right", padx=(10, 0))
        self._build_compressor_tab(comp)

    def select_frame_by_name(self, name):
        for n, b in self.nav_btns.items(): b.configure(fg_color="#334155" if n == name else "transparent")
        for f in self.frames.values(): f.pack_forget()
        self._current_tab_label.pack_forget()
        self._action_bar.pack_forget()
        
        # Show selected frame
        self.frames[name].pack(fill="both", expand=True, padx=0, pady=0)
        
        # Show action bar if needed
        if name not in ["Dashboard", "Settings", "Media Compressor"]:
            self._action_bar.pack(fill="x", pady=(10, 0))
            # Trigger lazy loading for the current tab
            self.after(200, lambda: self._trigger_lazy_load(name))

    def _trigger_lazy_load(self, tab_name: str):
        """Triggers thumbnail loading for cards in the current tab."""
        # Map tab names to their card lists
        tab_to_cards = {
            "Duplicates": self._exact_group_cards,
            "Similar Photos": self._similar_group_cards,
            "Screenshots": self._screenshot_cards,
            "Messages Media": self._message_cards,
            "Blurry Photos": self._blurry_cards,
            "Large Files": self._large_cards,
            "Timeline Viewer": self._timeline_cards,
        }
        cards = tab_to_cards.get(tab_name, [])
        # In a full implementation, we'd only load visible cards.
        # For now, we'll load all cards in the tab but only when the tab is switched to,
        # avoiding the initial "all tabs at once" spam.
        for card in cards:
            card.load_thumbnails()

    def _build_dashboard(self, parent):
        p = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12); p.pack(fill="x", pady=(0, 20))
        r = ctk.CTkFrame(p, fg_color="transparent"); r.pack(fill="x", padx=16, pady=16)
        self._folder_entry = ctk.CTkEntry(r, placeholder_text="Select folder...", state="readonly"); self._folder_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(r, text="📁 Browse Folder", command=self._select_folder).pack(side="left", padx=8)
        ctk.CTkButton(r, text="📄 Select Files", fg_color="#334155", hover_color="#475569", command=self._select_files).pack(side="left", padx=(0, 4))
        
        # HEIC format note (below browsing buttons in same panel)
        ctk.CTkLabel(p, text="Note:  ℹ️  HEIC format requires Microsoft HEIC Image Extensions from Windows Store for viewing.", font=ctk.CTkFont(size=9), text_color=TEXT_MUTED).pack(anchor="w", padx=16, pady=(0, 12))
        
        c = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12); c.pack(fill="x", pady=(0, 20))
        br = ctk.CTkFrame(c, fg_color="transparent"); br.pack(fill="x", padx=16, pady=16)
        self._scan_btn = ctk.CTkButton(br, text="🔍 Scan Photos & Docs", command=self._start_photo_scan, state="disabled"); self._scan_btn.pack(side="left")
        self._scan_videos_btn = ctk.CTkButton(br, text="🎥 Scan Videos", fg_color="#4F46E5", command=self._start_video_scan, state="disabled"); self._scan_videos_btn.pack(side="left", padx=10)
        self._pause_btn = ctk.CTkButton(br, text="⏸ Pause", fg_color="#F59E0B", command=self._pause_resume_scan, state="disabled"); self._pause_btn.pack(side="left", padx=10)
        self._cancel_btn = ctk.CTkButton(br, text="✕ Cancel", width=60, fg_color=DANGER, hover_color="#B91C1C", command=self._cancel_scan, state="disabled"); self._cancel_btn.pack(side="left")

        self._status_label = ctk.CTkLabel(c, text="Idle", text_color=TEXT_MUTED); self._status_label.pack(padx=16, anchor="w")
        self._progress_bar = ctk.CTkProgressBar(c, height=8); self._progress_bar.pack(fill="x", padx=16, pady=10); self._progress_bar.set(0)
        
        # Elapsed time and speed indicators
        self._elapsed_label = ctk.CTkLabel(c, text="Elapsed: 00:00", text_color=TEXT_DIM, font=ctk.CTkFont(size=10))
        self._elapsed_label.pack(padx=16, anchor="w")
        self._speed_label = ctk.CTkLabel(c, text="Speed: 0 files/sec", text_color=TEXT_DIM, font=ctk.CTkFont(size=10))
        self._speed_label.pack(padx=16, anchor="w")
        
        # Scan duration label (final)
        self._scan_duration_label = ctk.CTkLabel(c, text="", text_color=TEXT_DIM, font=ctk.CTkFont(size=10))
        self._scan_duration_label.pack(padx=16, anchor="w")

        s = ctk.CTkFrame(parent, fg_color="transparent")
        s.pack(fill="x", pady=(0, 20))
        
        # Grid for stats (2 rows, 4 columns)
        for col in range(4):
            s.grid_columnconfigure(col, weight=1)
            
        self._stat_found = self._stat_card_grid(s, "Total Scanned", "0", 0, 0)
        self._stat_unique = self._stat_card_grid(s, "Unique Files", "0", 0, 1)
        self._stat_dupes = self._stat_card_grid(s, "Duplicate Groups", "0", 0, 2)
        self._stat_extra_copies = self._stat_card_grid(s, "Extra copies", "0", 0, 3)
        
        self._stat_waste = self._stat_card_grid(s, "Reclaimable", "0 MB", 1, 0)
        self._stat_prefilter = self._stat_card_grid(s, "Prefilter saved", "0", 1, 1)
        self._stat_selected = self._stat_card_grid(s, "Selected", "0", 1, 2)
        # Empty space or another stat in 1,3
        
        ds = ctk.CTkFrame(parent, fg_color="transparent")
        ds.pack(fill="x", pady=20)
        self._stat_deleted_count = self._stat_card(ds, "Deleted (Session)", "0")
        self._stat_space_saved = self._stat_card(ds, "Space Saved (Session)", "0 MB")

    def _stat_card_grid(self, p, l, v, row, col):
        f = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10)
        f.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
        ctk.CTkLabel(f, text=l, font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).pack(pady=(8, 0))
        val = ctk.CTkLabel(f, text=v, font=ctk.CTkFont(size=18, weight="bold"))
        val.pack(pady=(0, 8))
        return val

    def _stat_card(self, p, l, v):
        f = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10); f.pack(side="left", padx=4, fill="both", expand=True)
        ctk.CTkLabel(f, text=l, font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).pack(pady=(8, 0))
        val = ctk.CTkLabel(f, text=v, font=ctk.CTkFont(size=18, weight="bold")); val.pack(pady=(0, 8))
        return val

    def _build_settings(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)

        panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        panel.pack(fill="x", pady=(0, 20), ipady=10)

        # Prefilter toggle
        adv_row = ctk.CTkFrame(panel, fg_color="transparent")
        adv_row.pack(fill="x", padx=20, pady=(20, 0))
        ctk.CTkLabel(adv_row, text="Advanced", font=ctk.CTkFont(size=12, weight="bold"), text_color="white").pack(side="left")
        ctk.CTkButton(adv_row, text="?", width=30, height=20, font=ctk.CTkFont(size=10), command=self._show_md5_help).pack(side="right")
        self._prefilter_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            panel, text="MD5 pre-filter (skip hashing byte-identical files for faster scans)",
            variable=self._prefilter_var, font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, command=self._sync_settings
        ).pack(anchor="w", padx=20, pady=(10, 20))

    def _build_compressor_tab(self, parent):
        # File/Folder selection panel

        # File/Folder selection panel
        sel_panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        sel_panel.pack(fill="x", pady=(0, 20), ipady=6)
        ctk.CTkLabel(sel_panel, text="Select Media", font=ctk.CTkFont(size=12, weight="bold"), text_color="white").pack(anchor="w", padx=20, pady=(12, 8))
        sel_row = ctk.CTkFrame(sel_panel, fg_color="transparent")
        sel_row.pack(fill="x", padx=20, pady=(0, 12))
        ctk.CTkButton(sel_row, text="📁 Select Folder", command=self._compressor_select_folder).pack(side="left", padx=(0, 8))
        ctk.CTkButton(sel_row, text="📄 Select Files", fg_color="#334155", command=self._compressor_select_files).pack(side="left")
        self._compressor_selection_label = ctk.CTkLabel(sel_panel, text="No files selected", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self._compressor_selection_label.pack(anchor="w", padx=20, pady=(0, 12))
        self._compressor_files: list[str] = []

        # Quality settings panel
        panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        panel.pack(fill="x", pady=(0, 16), ipady=6)

        q_header = ctk.CTkFrame(panel, fg_color="transparent")
        q_header.pack(fill="x", padx=20, pady=(16, 0))
        ctk.CTkLabel(q_header, text="Image Quality", font=ctk.CTkFont(size=12, weight="bold"), text_color="white").pack(side="left")
        ctk.CTkButton(q_header, text="?", width=24, height=24, font=ctk.CTkFont(size=10), command=self._show_image_quality_help).pack(side="right")
        ctk.CTkLabel(panel, text="JPEG quality (1–95). Lower = smaller file, more compression. 85 is recommended.", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(anchor="w", padx=20)
        q_row = ctk.CTkFrame(panel, fg_color="transparent")
        q_row.pack(fill="x", padx=20, pady=(8, 12))
        self._quality_label = ctk.CTkLabel(q_row, text="85", font=ctk.CTkFont(size=11, weight="bold"), text_color=ACCENT, width=40)
        self._quality_label.pack(side="right")
        self._quality_slider = ctk.CTkSlider(q_row, from_=30, to=95, number_of_steps=65, command=lambda v: self._quality_label.configure(text=str(int(v))))
        self._quality_slider.set(85)
        self._quality_slider.pack(side="left", fill="x", expand=True)

        crf_header = ctk.CTkFrame(panel, fg_color="transparent")
        crf_header.pack(fill="x", padx=20, pady=(12, 0))
        ctk.CTkLabel(crf_header, text="Video CRF (Constant Rate Factor)", font=ctk.CTkFont(size=12, weight="bold"), text_color="white").pack(side="left")
        ctk.CTkButton(crf_header, text="?", width=24, height=24, font=ctk.CTkFont(size=10), command=self._show_video_crf_help).pack(side="right")
        ctk.CTkLabel(panel, text="Lower CRF = better quality, larger file. 28 is visually near-lossless.", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(anchor="w", padx=20)
        crf_row = ctk.CTkFrame(panel, fg_color="transparent")
        crf_row.pack(fill="x", padx=20, pady=(8, 16))
        self._crf_label = ctk.CTkLabel(crf_row, text="28", font=ctk.CTkFont(size=11, weight="bold"), text_color=ACCENT, width=40)
        self._crf_label.pack(side="right")
        self._crf_slider = ctk.CTkSlider(crf_row, from_=18, to=40, number_of_steps=22, command=lambda v: self._crf_label.configure(text=str(int(v))))
        self._crf_slider.set(28)
        self._crf_slider.pack(side="left", fill="x", expand=True)

        # In-place toggle
        opts_panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        opts_panel.pack(fill="x", pady=(0, 16), ipady=6)
        self._inplace_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            opts_panel, text="Replace original files (in-place compression)",
            variable=self._inplace_var, font=ctk.CTkFont(size=11), text_color=TEXT_MUTED
        ).pack(anchor="w", padx=20, pady=14)

        # Action + output area
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(btn_row, text="🗜 Compress Selected Files", height=44, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#0EA5E9", hover_color="#0284C7", command=self._compress_selected).pack(side="left")

        self._compress_output = ctk.CTkScrollableFrame(parent, fg_color=BG_CARD, corner_radius=12, height=200)
        self._compress_output.pack(fill="both", expand=True, pady=(8, 0))
        ctk.CTkLabel(self._compress_output, text="Compression results will appear here.", text_color=TEXT_MUTED, font=ctk.CTkFont(size=11)).pack(pady=20)


    def _select_folder(self):
        f = filedialog.askdirectory()
        if f:
            self._folder_path = f
            self._selected_files_list = None  # Clear any file selection
            self._folder_entry.configure(state="normal"); self._folder_entry.delete(0, "end"); self._folder_entry.insert(0, f); self._folder_entry.configure(state="readonly")
            self._scan_btn.configure(state="normal"); self._scan_videos_btn.configure(state="normal")

    def _select_files(self):
        files = filedialog.askopenfilenames(
            title="Select files to scan",
            filetypes=[
                ("Image & Video files", "*.jpg *.jpeg *.png *.gif *.bmp *.heic *.webp *.mp4 *.mov *.avi *.mkv *.pdf *.txt"),
                ("All files", "*.*")
            ]
        )
        if files:
            self._selected_files_list = list(files)
            self._folder_entry.configure(
                state="normal"
            )
            self._folder_entry.delete(0, "end")
            self._folder_entry.insert(0, f"{len(files)} file(s) selected")
            self._folder_entry.configure(state="readonly")
            self._scan_btn.configure(state="normal")
            self._scan_videos_btn.configure(state="normal")

    def _start_photo_scan(self): self._start_scan("photos")
    def _start_video_scan(self): self._start_scan("videos")
    
    def _scan_from_tab(self, tab_name: str, mode: str = "photos"):
        """Initiate a scan requested from a specific tab.
        
        Always performs a scan to show progress and update timing/stats.
        """
        self._scan_request_tab = tab_name
        # show dashboard so user can observe progress controls
        self.select_frame_by_name("Dashboard")
        self._start_scan(mode)

    def _start_scan(self, mode):
        # Validation: Ensure folder or files are selected
        if not self._folder_path and not self._selected_files_list:
            messagebox.showwarning("No Input", "Please select a folder or files before starting the scan.")
            return

        # mode = "photos" or "videos"; called by dashboard or feature tabs
        self._scan_start_time = time.perf_counter()
        self._start_elapsed_timer()
        self._last_count = 0
        self._is_scanning = True; self._is_paused = False
        self._cancel_event.clear(); self._pause_event.set()
        self._clear_results(); self._set_scanning_ui(True)
        self._scan_duration_label.configure(text="In-Progress")
        s = ScanSettings(); s.tolerance = self._settings.tolerance; s.use_prefilter = self._prefilter_var.get()
        s.mode = mode
        if mode == "videos": s.target_extensions = VIDEO_EXTENSIONS; s.use_prefilter = False
        else: s.target_extensions = IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS
        threading.Thread(target=self._scan_worker, args=(s,), daemon=True).start()

    def _pause_resume_scan(self):
        if self._is_paused:
            # Resume
            self._pause_event.set()
            self._is_paused = False
            self._pause_btn.configure(text="⏸ Pause", fg_color="#F59E0B")
            self._set_status("Resuming scan...", SUCCESS)
        else:
            # Pause
            self._pause_event.clear()
            self._is_paused = True
            self._pause_btn.configure(text="▶ Resume", fg_color="#10B981")
            self._set_status("Scan paused", WARN)

    def _cancel_scan(self): 
        self._cancel_event.set()
        self._pause_event.set()  # Ensure not stuck in pause
        self._set_status("Cancelling scan...", WARN)
        self._stop_elapsed_timer()

    def _scan_worker(self, settings):
        try:
            # Check for cancellation
            if self._cancel_event.is_set():
                self._push("status", "Scan cancelled", 0, True, "")
                return
            
            # 1. Scanning — either a full folder or a selected file list
            self._push("status", "Listing files...", 0.05, False, "")

            if self._selected_files_list:
                res = scan_files(self._selected_files_list)
                self._push("status", f"Loaded {res.image_count} selected files.", 0.15, False, "")
            else:
                def scan_progress(count, current_path, total_scanned):
                    if self._cancel_event.is_set():
                        raise Exception("Scan cancelled")
                    # periodically update with total scanned count for speed calculation
                    if total_scanned % 50 == 0:
                        self._push("status", f"Scanning: found {count} media files...", 0.1, False, total_scanned)
                res = scan_folder(self._folder_path, recursive=True, progress_callback=scan_progress, cancel_event=self._cancel_event)
            
            # PUSH PARTIAL RESULTS TO DASHBOARD
            self._push("partial_count", str(res.image_count), 0.15, False, "")
            
            # Check for cancellation after scanning
            if self._cancel_event.is_set():
                self._push("status", "Scan cancelled", 0, True, "")
                return
            
            # 2. Hashing (Images, Videos, and Documents)
            self._push("status", "Preparing for hashing...", 0.2, False, "")
            
            all_files_to_hash = [f.path for f in res.images]
            
            def hash_progress(done, total):
                if self._cancel_event.is_set():
                    raise Exception("Scan cancelled")
                # Wait for pause/resume
                self._pause_event.wait()
                if done % 10 == 0 or done == total:
                    pct = 0.2 + (done / total) * 0.6  # Mapping 0-100% to 0.2-0.8 on bar
                    self._push("status", f"Scanned: {done} / {total} files", pct, False, done)

            h_map = generate_hashes(all_files_to_hash, progress_callback=hash_progress, cancel_event=self._cancel_event)
            
            # Check for cancellation after hashing
            if self._cancel_event.is_set():
                self._push("status", "Scan cancelled", 0, True, "")
                return
            
            # 3. Detection
            self._push("status", "Finding duplicates...", 0.9, False, "")
            detection = detect_duplicates(h_map, hash_tolerance=settings.tolerance, all_images=res.images, folder_sizes=dict(res.folder_sizes), mode=settings.mode)
            
            self._push("status", "Scan complete!", 1.0, False, "")
            self._push_done(detection)
        except Exception as e:
            error_msg = str(e)
            if "cancelled" in error_msg.lower():
                self._push("status", "Scan cancelled", 0, True, "")
            else:
                log.exception("Scan worker failed")
                self._push("status", f"Error: {error_msg}", 0, True, "")
            self._push_done(None)

    def _push(self, k, m, f, e, s): 
        # capture any numeric count for speed calculations
        try:
            if isinstance(s, (int, float)):
                self._last_count = int(s)
        except Exception:
            pass
        self._progress_queue.put((k, m, f, e, s))
    def _push_done(self, r): 
        scan_duration = time.perf_counter() - self._scan_start_time
        minutes = int(scan_duration // 60)
        seconds = int(scan_duration % 60)
        duration_text = f"Scan completed in: {minutes} minutes {seconds} seconds"
        self._scan_duration_label.configure(text=duration_text)
        self._stop_elapsed_timer()
        # update final speed using last count if available
        if self._last_count and scan_duration > 0:
            final_speed = self._last_count / scan_duration
            self._speed_label.configure(text=f"Speed: {final_speed:.1f} files/sec")
        self._progress_queue.put(("done", r))

    def _start_elapsed_timer(self):
        if self._elapsed_timer is None:
            self._update_elapsed_time()
            self._elapsed_timer = self.after(1000, self._start_elapsed_timer)

    def _stop_elapsed_timer(self):
        if self._elapsed_timer:
            self.after_cancel(self._elapsed_timer)
            self._elapsed_timer = None

    def _update_elapsed_time(self):
        if self._is_scanning and self._scan_start_time:
            elapsed = time.perf_counter() - self._scan_start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            self._elapsed_label.configure(text=f"Elapsed: {mins:02d}:{secs:02d}")

    def _poll_progress(self):
        try:
            # Limit processing to 100 messages per tick to keep UI snappy
            for _ in range(100):
                try:
                    m = self._progress_queue.get_nowait()
                    if m[0] == "status":
                        self._status_label.configure(text=m[1])
                    elif m[0] == "partial_count":
                        self._stat_found.configure(text=m[1])
                        self._progress_bar.set(m[2])
                        # Update elapsed timer every tick
                        if self._is_scanning and self._scan_start_time:
                            elapsed = time.perf_counter() - self._scan_start_time
                            mins = int(elapsed // 60)
                            secs = int(elapsed % 60)
                            self._elapsed_label.configure(text=f"Elapsed: {mins:02d}:{secs:02d}")
                            # compute speed if count provided
                            try:
                                count = float(m[4])
                                if elapsed > 0:
                                    self._speed_label.configure(text=f"Speed: {count/elapsed:.1f} files/sec")
                            except Exception:
                                pass

                        # Update button states based on pause status
                        if self._is_paused:
                            self._pause_btn.configure(text="▶ Resume", fg_color="#10B981")
                        else:
                            self._pause_btn.configure(text="⏸ Pause", fg_color="#F59E0B")
                    elif m[0] == "done":
                        # final stats
                        self._is_scanning = False; self._is_paused = False
                        self._set_scanning_ui(False)
                        
                        # Process results
                        if m[1]:
                            # Automatically switch to Duplicates tab if we were on Dashboard
                            # to show the user that results are ready.
                            if not self._scan_request_tab or self._scan_request_tab == "Dashboard":
                                self.select_frame_by_name("Duplicates")
                                self._scan_request_tab = None # consume it
                            elif self._scan_request_tab:
                                self.select_frame_by_name(self._scan_request_tab)
                                self._scan_request_tab = None
                            
                            self._render_results(m[1])
                            
                            # set speed based on total images checked
                            total = getattr(m[1], "total_images_checked", None)
                            if total and self._scan_start_time:
                                elapsed = time.perf_counter() - self._scan_start_time
                                if elapsed > 0:
                                    self._speed_label.configure(text=f"Speed: {total/elapsed:.1f} files/sec")
                        break
                except queue.Empty:
                    break
        except queue.Empty: pass
        self.after(60, self._poll_progress)

    def _render_results(self, det):
        # keep result for potential cached navigations
        log.info(f"Rendering scan results: {det.total_images_checked} checked, {det.duplicate_group_count} groups found")
        self._detection_result = det
        self._clear_results()
        self._stat_found.configure(text=str(det.total_images_checked))
        self._stat_dupes.configure(text=str(det.duplicate_group_count))
        self._stat_waste.configure(text=f"{det.total_wasted_mb():.1f} MB")
        
        # Reset pagination states
        self._render_state = {
            "exact": {"index": 0, "groups": [g for g in det.groups if g.match_type != "near_duplicate"]},
            "similar": {"index": 0, "groups": [g for g in det.groups if g.match_type == "near_duplicate"]},
            "screenshots": {"index": 0, "items": det.screenshots},
            "blurry": {"index": 0, "items": det.blurry_photos},
            "large": {"index": 0, "items": det.large_files},
            "messages": {"index": 0, "items": det.whatsapp_media + det.telegram_media},
            "timeline": {"year_idx": 0, "photo_idx": 0, "items": list(det.timeline.items()) if det.timeline else []}
        }

        # Render first batch for all with error isolation
        render_tasks = [
            ("Duplicates", 20),
            ("Similar Photos", 20),
            ("Screenshots", 20),
            ("Blurry Photos", 20),
            ("Large Files", 20),
            ("Messages Media", 80),
            ("Timeline Viewer", 5)
        ]
        
        for tab, size in render_tasks:
            try:
                self._render_next_batch(tab, size)
            except Exception:
                log.exception(f"Failed to render initial batch for {tab}")

        # Update stats
        extra_copies = sum(g.count - 1 for g in det.groups)
        self._stat_extra_copies.configure(text=str(extra_copies))
        self._stat_prefilter.configure(text=str(getattr(det, "prefilter_exact_count", 0)))

        self._select_all_btn.configure(state="normal")
        self._quick_clean_btn.configure(state="normal")
        self._compress_btn.configure(state="normal")
        
        # Dashboard Sync: Ensure stats are updated after scan
        self._refresh_dashboard_stats()
        self._update_global_dashboard_stats()

    def _on_tab_scroll(self, tab_name: str):
        """Infinite scroll handler."""
        attr = {
            "Duplicates": "_scroll_exact", "Similar Photos": "_scroll_similar",
            "Screenshots": "_scroll_screenshots", "Messages Media": "_scroll_messages",
            "Blurry Photos": "_scroll_blurry", "Large Files": "_scroll_large",
            "Timeline Viewer": "_scroll_timeline"
        }.get(tab_name)
        
        if not attr: return
        scroll = getattr(self, attr)
        
        # Get scroll fraction
        y_fraction = scroll._parent_canvas.yview()
        if y_fraction[1] > 0.70: # Reached 70% of the way down (Early preload)
            # Skip auto-load for Messages Media and Timeline if we want manual "Next Page"
            if tab_name not in ["Messages Media", "Timeline Viewer"]:
                self._render_next_batch(tab_name)

    def _render_next_batch(self, tab_name: str, batch_size: int = 20):
        """Standardized batch renderer for all categories."""
        if not hasattr(self, "_render_state"): return
        
        if tab_name == "Duplicates":
            state = self._render_state["exact"]
            scroll = self._scroll_exact
            cards = self._exact_group_cards
            self._do_render_groups(state, scroll, cards, batch_size, "Duplicates")
        elif tab_name == "Similar Photos":
            state = self._render_state["similar"]
            scroll = self._scroll_similar
            cards = self._similar_group_cards
            self._do_render_groups(state, scroll, cards, batch_size, "Similar Photos")
        elif tab_name == "Screenshots":
            state = self._render_state["screenshots"]
            start = state["index"]
            end = min(start + batch_size, len(state["items"]))
            if start < end:
                new_paths = state["items"][start:end]
                if start == 0:
                    c = FileGridCard(self._scroll_screenshots, "Screenshots", new_paths, self._thumb_cache, self._update_selected_count)
                    c.pack(fill="x", pady=4); self._screenshot_cards.append(c)
                else:
                    c = self._screenshot_cards[0]
                    c.add_paths(new_paths)
                c.load_thumbnails()
                state["index"] = end
            elif not state["items"] and start == 0:
                self._show_empty(self._scroll_screenshots)
        elif tab_name == "Blurry Photos":
            state = self._render_state["blurry"]
            start = state["index"]
            end = min(start + batch_size, len(state["items"]))
            if start < end:
                new_paths = state["items"][start:end]
                if start == 0:
                    c = FileGridCard(self._scroll_blurry, "Blurry Photos", new_paths, self._thumb_cache, self._update_selected_count)
                    c.pack(fill="x", pady=4); self._blurry_cards.append(c)
                else:
                    c = self._blurry_cards[0]
                    c.add_paths(new_paths)
                c.load_thumbnails()
                state["index"] = end
            elif not state["items"] and start == 0:
                self._show_empty(self._scroll_blurry)
        elif tab_name == "Large Files":
            state = self._render_state["large"]
            start = state["index"]
            end = min(start + batch_size, len(state["items"]))
            if start < end:
                new_paths = state["items"][start:end]
                if start == 0:
                    c = FileGridCard(self._scroll_large, "Large Media (>50MB)", new_paths, self._thumb_cache, self._update_selected_count)
                    c.pack(fill="x", pady=4); self._large_cards.append(c)
                else:
                    c = self._large_cards[0]
                    c.add_paths(new_paths)
                c.load_thumbnails()
                state["index"] = end
            elif not state["items"] and start == 0:
                self._show_empty(self._scroll_large)
        elif tab_name == "Messages Media":
            batch_size = 80 # Override for messages
            state = self._render_state["messages"]
            start = state["index"]
            end = min(start + batch_size, len(state["items"]))
            if start < end:
                new_paths = state["items"][start:end]
                if start == 0:
                    c = FileGridCard(self._scroll_messages, "Messaging Media", new_paths, self._thumb_cache, self._update_selected_count)
                    c.pack(fill="x", pady=4); self._message_cards.append(c)
                else:
                    c = self._message_cards[0]
                    c.add_paths(new_paths)
                c.load_thumbnails()
                state["index"] = end
                
                # Check if we should show a "Next Page" button
                self._update_load_more_button("Messages Media")
            elif not state["items"] and start == 0:
                self._show_empty(self._scroll_messages)
        elif tab_name == "Timeline Viewer":
            batch_size = 80 # Override for timeline photos
            state = self._render_state["timeline"]
            items = state["items"] # List of (year, [paths])
            
            photos_loaded = 0
            while photos_loaded < batch_size and state["year_idx"] < len(items):
                year, paths = items[state["year_idx"]]
                start_p = state["photo_idx"]
                end_p = min(start_p + (batch_size - photos_loaded), len(paths))
                
                if start_p < end_p:
                    batch_paths = paths[start_p:end_p]
                    # Find or create card for this year
                    existing_card = None
                    for c in self._timeline_cards:
                        if getattr(c, "_category_name", "") == f"Year {year}":
                            existing_card = c
                            break
                    
                    if existing_card:
                        existing_card.add_paths(batch_paths)
                        existing_card.load_thumbnails()
                    else:
                        c = FileGridCard(self._scroll_timeline, f"Year {year}", batch_paths, self._thumb_cache, self._update_selected_count)
                        c.pack(fill="x", pady=8); self._timeline_cards.append(c)
                        c.load_thumbnails()
                    
                    photos_loaded += (end_p - start_p)
                    state["photo_idx"] = end_p
                
                # If we finished all photos in this year, move to next year
                if state["photo_idx"] >= len(paths):
                    state["year_idx"] += 1
                    state["photo_idx"] = 0
            
            # Check for "Next Page" button
            self._update_load_more_button("Timeline Viewer")
            
            if not items and state["year_idx"] == 0:
                self._show_empty(self._scroll_timeline)

    def _do_render_groups(self, state, scroll, card_list, batch_size, tab_name):
        groups = state["groups"]
        start = state["index"]
        end = min(start + batch_size, len(groups))
        
        # Remove old load more button if exists
        for child in scroll.winfo_children():
            if getattr(child, "_is_load_more", False):
                child.destroy()

        for i in range(start, end):
            g = groups[i]
            c = DuplicateGroupCard(scroll, g, self._thumb_cache, self._update_selected_count)
            c.pack(fill="x", pady=4); card_list.append(c)
            c.load_thumbnails()
        
        state["index"] = end
        if not groups and start == 0:
            self._show_empty(scroll)

    def _check_auto_load(self):
        """Checks if current visible cards are too few and loads more if needed."""
        # Get current tab
        current_tab = None
        for name, btn in self.nav_btns.items():
            if btn.cget("fg_color") == "#334155": # Selected color
                current_tab = name
                break
        
        if not current_tab: return
        
        tab_to_cards = {
            "Duplicates": self._exact_group_cards, "Similar Photos": self._similar_group_cards,
            "Screenshots": self._screenshot_cards, "Messages Media": self._message_cards,
            "Blurry Photos": self._blurry_cards, "Large Files": self._large_cards,
            "Timeline Viewer": self._timeline_cards
        }
        
        cards = tab_to_cards.get(current_tab, [])
        if len(cards) < 10: # Threshold for auto-filling
            self._render_next_batch(current_tab)
            
    def _update_load_more_button(self, tab_name: str):
        """Adds a 'Next Page' button at the bottom if more items exist for a category."""
        state_map = {
            "Screenshots": "screenshots", "Blurry Photos": "blurry",
            "Large Files": "large", "Messages Media": "messages",
            "Timeline Viewer": "timeline"
        }
        scroll_map = {
            "Screenshots": self._scroll_screenshots, "Blurry Photos": self._scroll_blurry,
            "Large Files": self._scroll_large, "Messages Media": self._scroll_messages,
            "Timeline Viewer": self._scroll_timeline
        }
        
        s_key = state_map.get(tab_name)
        if not s_key or not hasattr(self, "_render_state"): return
        
        state = self._render_state[s_key]
        scroll = scroll_map.get(tab_name)
        
        # Remove existing button if any
        if hasattr(self, f"_load_more_{s_key}"):
            btn = getattr(self, f"_load_more_{s_key}")
            if btn: btn.destroy()
            setattr(self, f"_load_more_{s_key}", None)
            
        if s_key == "timeline":
            has_more = state["year_idx"] < len(state["items"])
        else:
            has_more = state["index"] < len(state["items"])

        if has_more:
            # Create a container for the button to center it
            btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
            btn_frame.pack(fill="x", pady=20)
            
            btn = ctk.CTkButton(
                btn_frame, text="Next Page →", 
                width=200, height=40, font=ctk.CTkFont(size=14, weight="bold"),
                command=lambda n=tab_name: self._render_next_batch(n)
            )
            btn.pack(expand=True)
            setattr(self, f"_load_more_{s_key}", btn_frame)

    def _show_toast(self, message: str, duration: int = 2500):
        """Shows a temporary notification near the bottom."""
        self._toast_label.configure(text=message)
        self._toast_frame.place(relx=0.5, rely=0.9, anchor="center")
        self.after(duration, lambda: self._toast_frame.place_forget())

    def _is_path_selected(self, path: str) -> bool:
        """Checks if a path is selected in any visible card."""
        all_cards = (self._exact_group_cards + self._similar_group_cards + 
                     self._screenshot_cards + self._blurry_cards + 
                     self._large_cards + self._message_cards + self._timeline_cards)
        for card in all_cards:
            if hasattr(card, "_checkboxes") and path in card._checkboxes:
                return card._checkboxes[path].get()
        return False

    def _toggle_path_selection(self, path: str, selected: bool):
        """Syncs selection state for a path across all cards."""
        all_cards = (self._exact_group_cards + self._similar_group_cards + 
                     self._screenshot_cards + self._blurry_cards + 
                     self._large_cards + self._message_cards + self._timeline_cards)
        for card in all_cards:
            if hasattr(card, "_checkboxes") and path in card._checkboxes:
                cb = card._checkboxes[path]
                if selected: cb.select()
                else: cb.deselect()
        self._update_selected_count()

    def _show_empty(self, parent):
        ctk.CTkLabel(parent, text="No Files to Show", text_color=TEXT_MUTED, font=ctk.CTkFont(size=16)).pack(expand=True)

    def _refresh_dashboard_stats(self):
        """Recalculate and update dashboard summary stats based on current cards.
        Called after deletions or when the dataset changes without a full re-scan.
        """
        total_images = 0
        dup_groups = 0
        total_waste = 0
        extra = 0
        for card in self._exact_group_cards + self._similar_group_cards:
            dup_groups += 1
            total_images += len(card.group.files)
            total_waste += card.group.wasted_bytes()
            extra += card.group.count - 1
        self._stat_found.configure(text=str(total_images))
        self._stat_dupes.configure(text=str(dup_groups))
        self._stat_waste.configure(text=f"{total_waste / (1024*1024):.1f} MB")
        self._stat_extra_copies.configure(text=str(extra))

    def _update_global_dashboard_stats(self):
        """Update all dashboard stats to reflect actual file counts across all categories.
        Uses full dataset from state, not just rendered cards.
        """
        if not hasattr(self, "_render_state"): return
        
        # Exact/Similar (Groups) from full render state
        exact_groups = self._render_state["exact"]["groups"]
        similar_groups = self._render_state["similar"]["groups"]
        all_groups = exact_groups + similar_groups
        
        dup_groups = len(all_groups)
        total_dup_images = sum(len(g.files) for g in all_groups)
        total_waste = sum(g.wasted_bytes() for g in all_groups)
        extra = sum(len(g.files) - 1 for g in all_groups)
        
        # Categorized lists
        screenshot_count = len(self._render_state["screenshots"]["items"])
        blurry_count = len(self._render_state["blurry"]["items"])
        large_count = len(self._render_state["large"]["items"])
        message_count = len(self._render_state["messages"]["items"])
        
        # Update stats
        total_scanned = getattr(self._detection_result, "total_images_checked", 0)
        total_found = total_dup_images + screenshot_count + blurry_count + large_count + message_count
        
        self._stat_found.configure(text=str(total_scanned))
        self._stat_unique.configure(text=str(total_scanned - total_found))
        self._stat_dupes.configure(text=str(dup_groups))
        self._stat_waste.configure(text=f"{total_waste / (1024*1024):.1f} MB")
        self._stat_extra_copies.configure(text=str(extra))

    def _scroll_active_tab_to_top(self):
        """Scrolls the currently visible results tab to the top."""
        # Find active tab by checking button background
        current_tab = None
        for name, btn in self.nav_btns.items():
            if btn.cget("fg_color") == "#334155": # Selected color
                current_tab = name
                break
        
        if not current_tab: return
        
        attr = {
            "Duplicates": "_scroll_exact", "Similar Photos": "_scroll_similar",
            "Screenshots": "_scroll_screenshots", "Messages Media": "_scroll_messages",
            "Blurry Photos": "_scroll_blurry", "Large Files": "_scroll_large",
            "Timeline Viewer": "_scroll_timeline"
        }.get(current_tab)
        
        if attr:
            scroll = getattr(self, attr)
            if hasattr(scroll, "_parent_canvas"):
                scroll._parent_canvas.yview_moveto(0)

    def _clear_results(self):
        # Clear duplicate card lists
        for w in self._scroll_exact.winfo_children(): w.destroy()
        self._exact_group_cards.clear()
        for w in self._scroll_similar.winfo_children(): w.destroy()
        self._similar_group_cards.clear()

        # Clear categorization tabs
        for scroll, cards in [
            (self._scroll_screenshots, self._screenshot_cards),
            (self._scroll_blurry, self._blurry_cards),
            (self._scroll_large, self._large_cards),
            (self._scroll_messages, self._message_cards),
            (self._scroll_timeline, self._timeline_cards)
        ]:
            for w in scroll.winfo_children(): w.destroy()
            cards.clear()

        # Reset stats
        self._stat_found.configure(text="0")
        self._stat_dupes.configure(text="0")
        self._stat_waste.configure(text="0 MB")
        self._stat_prefilter.configure(text="0")
        self._stat_extra_copies.configure(text="0")
        self._stat_selected.configure(text="0")
        self._delete_btn.configure(state="disabled", text="Delete Selected")
        self._compress_btn.configure(state="disabled")
        self._select_all_btn.configure(state="disabled")
        self._quick_clean_btn.configure(state="disabled")

    def _update_selected_count(self):
        t = sum(len(c.get_selected_paths()) for c in self._exact_group_cards + self._similar_group_cards + self._screenshot_cards + self._blurry_cards + self._large_cards + self._message_cards + self._timeline_cards)
        self._stat_selected.configure(text=str(t))
        self._delete_btn.configure(state="normal" if t > 0 else "disabled", text=f"Delete ({t})" if t > 0 else "Delete Selected")

    def _delete_selected(self):
        paths = []
        all_card_lists = [
            self._exact_group_cards, self._similar_group_cards,
            self._screenshot_cards, self._blurry_cards,
            self._large_cards, self._message_cards,
            self._timeline_cards
        ]
        
        for clist in all_card_lists:
            for c in clist:
                paths.extend(c.get_selected_paths())
        
        if not paths: return
        
        if not messagebox.askyesno("Confirm Delete", f"Are you sure you want to move {len(paths)} files to the Recycle Bin?"):
            return

        res = delete_files(paths)
        deleted_set = set(res.deleted)
        
        # Calculate bytes saved - get file sizes before they're deleted
        bytes_saved = 0
        for path in paths:
            try:
                bytes_saved += os.path.getsize(path)
            except (OSError, ValueError):
                pass
        
        # Refresh UI: remove deleted files from all cards
        for clist in all_card_lists:
            to_remove = []
            for c in clist:
                if hasattr(c, "remove_paths"):
                    should_destroy = c.remove_paths(deleted_set)
                    if should_destroy:
                        c.destroy()
                        to_remove.append(c)
            for c in to_remove:
                if c in clist:
                    clist.remove(c)

        # Update session global stats
        self._session_deleted_count += len(res.deleted)
        self._session_saved_bytes += bytes_saved
        
        # UI Update for Session Stats
        self._stat_deleted_count.configure(text=str(self._session_deleted_count))
        self._stat_space_saved.configure(text=f"{self._session_saved_bytes / (1024*1024):.1f} MB")
        
        self._update_selected_count()
        # refresh dashboard summary metrics (Found/Waste/Groups) after deletions
        self._refresh_dashboard_stats()
        self._update_global_dashboard_stats()
        
        # IMPORTANT: Auto-load more if current view is too small after deletions
        self._check_auto_load()
        
        # FIX: Ensure thumbnails are loaded for remaining items to avoid stuck "loading" state
        for clist in all_card_lists:
            for c in clist:
                if hasattr(c, "load_thumbnails"):
                    c.load_thumbnails()
        
        # Scroll to top after deletion
        self._scroll_active_tab_to_top()
        
        # Force refresh of all scrollable frames to ensure proper re-rendering
        for frame in [self._scroll_exact, self._scroll_similar, self._scroll_screenshots, self._scroll_blurry, self._scroll_large, self._scroll_messages, self._scroll_timeline]:
            frame.update_idletasks()
        
        self._show_toast(f"Successfully moved {len(res.deleted)} files to the Recycle Bin.")
        if has_undoable_deletes(): self._undo_btn.pack(side="right", padx=10)

    def _show_tab_info(self, tab_name: str):
        desc = self._tab_descriptions.get(tab_name, "No description available.")
        messagebox.showinfo(f"{tab_name} Info", desc)

    def _show_hamming_help(self):
        messagebox.showinfo("Hamming Distance Help", 
            "Hamming distance measures how many bits differ between two perceptual hashes.\n\n"
            "• 0: Exact identical images (same hash)\n"
            "• 1-2: Minor differences (e.g., EXIF metadata changes)\n"
            "• 3-5: Similar images (crops, slight edits)\n"
            "• 6+: More liberal grouping (may include false positives)\n\n"
            "Recommended: Start with 0 for exact duplicates, increase to 3-5 for similar photos.")

    def _show_md5_help(self):
        messagebox.showinfo("MD5 Pre-filter Help", 
            "MD5 pre-filter speeds up scanning by checking file sizes first, then MD5 hashes for byte-identical files.\n\n"
            "Why use it:\n"
            "• Files with same size are checked for exact byte matches using MD5\n"
            "• Byte-identical files skip expensive perceptual hashing\n"
            "• Significantly faster for folders with many copies\n\n"
            "Disable only if you suspect MD5 collisions (very rare).")

    def _show_image_quality_help(self):
        messagebox.showinfo("Image Quality Help",
            "JPEG Quality Parameter (1-95):\n\n"
            "• 30-50: High compression, visible artifacts, very small files\n"
            "• 50-75: Balanced compression, minor quality loss, small files\n"
            "• 75-90: Good quality with some compression, recommended range\n"
            "• 85: Recommended for most use cases (default)\n"
            "• 90+: Near-lossless quality, large file sizes\n\n"
            "Tips: Use 85 for photos, 90 for professional work, 70 for web.")

    def _show_video_crf_help(self):
        messagebox.showinfo("Video CRF Help",
            "CRF - Constant Rate Factor (18-40):\n\n"
            "• 18-22: High quality, large files (professional)\n"
            "• 23-28: Visually lossless (imperceptible quality loss)\n"
            "• 28: Default - great balance of quality and file size\n"
            "• 29-35: Good quality, smaller files\n"
            "• 36+: Lower quality, much smaller files (streaming)\n\n"
            "Lower CRF = better quality but larger file\n"
            "Higher CRF = worse quality but smaller file")

    def _undo_last_delete(self):
        res = restore_last_deleted()
        if res.deleted:
            messagebox.showinfo("Restored", f"Restored {len(res.deleted)} files."); self._undo_btn.pack_forget()
            self._start_photo_scan()

    def _delete_single_file(self, path: str) -> bool:
        """Called from ImageViewer to delete the currently viewed file."""
        if not messagebox.askyesno("Delete File", f"Move '{os.path.basename(path)}' to the Recycle Bin?"):
            return False
        res = delete_files([path])
        if res.deleted:
            # Calculate bytes saved
            bytes_saved = 0
            try:
                bytes_saved = os.path.getsize(path)
            except (OSError, ValueError):
                pass
            
            # Remove from all cards
            deleted_set = set(res.deleted)
            all_card_lists = [
                self._exact_group_cards, self._similar_group_cards,
                self._screenshot_cards, self._blurry_cards,
                self._large_cards, self._message_cards, self._timeline_cards
            ]
            for clist in all_card_lists:
                to_remove = [c for c in clist if c.remove_paths(deleted_set)]
                for c in to_remove:
                    c.destroy(); clist.remove(c)
            self._session_deleted_count += 1
            self._session_saved_bytes += bytes_saved
            self._stat_deleted_count.configure(text=str(self._session_deleted_count))
            self._stat_space_saved.configure(text=f"{self._session_saved_bytes / (1024*1024):.1f} MB")
            self._update_selected_count()
            # refresh dashboard as we removed a file
            self._refresh_dashboard_stats()
            self._update_global_dashboard_stats()
            
            # Auto-load more if needed
            self._check_auto_load()
            
            # FIX: Re-trigger thumbnail loading for visible cards
            for clist in all_card_lists:
                for c in clist:
                    if hasattr(c, "load_thumbnails"):
                        c.load_thumbnails()
            
            # Scroll to top
            self._scroll_active_tab_to_top()
            
            if has_undoable_deletes(): self._undo_btn.pack(side="right", padx=10)
            return True
        return False

    def _set_status(self, t, c=TEXT_MUTED): self._status_label.configure(text=t, text_color=c)
    def _set_scanning_ui(self, s):
        st = "disabled" if s else "normal"
        self._scan_btn.configure(state=st); self._scan_videos_btn.configure(state=st)
        # disable per-tab scan buttons too
        for b in getattr(self, '_tab_scan_buttons', []):
            b.configure(state=st)
        self._pause_btn.configure(state="normal" if s else "disabled")
        self._cancel_btn.configure(state="normal" if s else "disabled")

    def _on_slider_change(self, v):
        val = int(float(v))
        self._settings.tolerance = val
        label = "(Exact only)" if val == 0 else f"(~{val * 4}% diff allowed)"
        self._tol_label.configure(text=f"{val}  {label}")
        self._sync_settings()

    def _sync_settings(self):
        self._settings.use_prefilter = self._prefilter_var.get()

    def _select_all_duplicates(self):
        for card in self._exact_group_cards + self._similar_group_cards:
            card.select_all_except_keep()
        self._update_selected_count()

    def _quick_clean(self):
        """Auto-select all non-kept duplicates from every group and prompt once to delete."""
        for card in self._exact_group_cards + self._similar_group_cards:
            card.select_all_except_keep()
        self._update_selected_count()
        t = sum(len(c.get_selected_paths()) for c in self._exact_group_cards + self._similar_group_cards)
        if t == 0:
            messagebox.showinfo("Quick Clean", "No duplicate copies to remove.")
            return
        if messagebox.askyesno("Quick Clean", f"Ready to move {t} duplicate copies to the Recycle Bin. Proceed?"):
            # Call delete logic directly to avoid the second confirmation dialog
            paths = []
            all_card_lists = [self._exact_group_cards, self._similar_group_cards]
            for clist in all_card_lists:
                for c in clist:
                    paths.extend(c.get_selected_paths())
            if not paths: return
            res = delete_files(paths)
            deleted_set = set(res.deleted)
            for clist in all_card_lists:
                to_remove = [c for c in clist if c.remove_paths(deleted_set)]
                for c in to_remove:
                    c.destroy(); clist.remove(c)
            
            # Sync session stats for Quick Clean too
            quick_bytes_saved = 0
            for p in res.deleted: # Use res.deleted which is verified deleted
                try: quick_bytes_saved += os.path.getsize(p)
                except: pass
            
            self._session_deleted_count += len(res.deleted)
            self._session_saved_bytes += quick_bytes_saved
            
            self._stat_deleted_count.configure(text=str(self._session_deleted_count))
            self._stat_space_saved.configure(text=f"{self._session_saved_bytes / (1024*1024):.1f} MB")
            
            self._update_selected_count()
            self._refresh_dashboard_stats()
            self._update_global_dashboard_stats()
            
            # IMPORTANT: Auto-load more after Quick Clean
            self._check_auto_load()
            
            # Scroll to top
            self._scroll_active_tab_to_top()
            
            self._show_toast(f"Quick Clean: Moved {len(res.deleted)} file(s)")
            if has_undoable_deletes(): self._undo_btn.pack(side="right", padx=10)

    def _compressor_select_folder(self):
        folder = filedialog.askdirectory(title="Select folder to compress")
        if folder:
            # Find all supported media files in folder
            self._compressor_files = []
            for ext in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                import glob
                self._compressor_files.extend(glob.glob(os.path.join(folder, f"**/*{ext}"), recursive=True))
            self._compressor_selection_label.configure(text=f"Selected: {len(self._compressor_files)} files from folder")

    def _compressor_select_files(self):
        files = filedialog.askopenfilenames(
            title="Select files to compress",
            filetypes=[("Images & Videos", "*.(jpg|jpeg|png|mp4|mov|avi)"), ("All files", "*.*")]
        )
        if files:
            self._compressor_files = list(files)
            self._compressor_selection_label.configure(text=f"Selected: {len(self._compressor_files)} files")

    def _toggle_theme(self):
        if ctk.get_appearance_mode() == "Dark":
            ctk.set_appearance_mode("light")
            self._theme_btn.configure(text="🌙 Dark Mode")
        else:
            ctk.set_appearance_mode("dark")
            self._theme_btn.configure(text="☀ Light Mode")

    def _compress_selected(self):
        # Guard: compressor tab might not be built yet if user hasn't visited it
        if not hasattr(self, "_quality_slider"):
            messagebox.showinfo("Compressor", "Please open the Media Compressor tab first to set quality options.")
            self.select_frame_by_name("Media Compressor")
            return
            
        image_paths, video_paths = [], []
        
        # PRIORitize files selected directly in the Compressor tab
        if self._compressor_files:
            source_paths = self._compressor_files
            log.info(f"Compressor: processing {len(source_paths)} files from direct upload/browse")
        else:
            # FALLBACK to checkboxes in search result tabs
            source_paths = []
            all_cards = self._exact_group_cards + self._similar_group_cards + self._screenshot_cards + self._blurry_cards + self._large_cards + self._message_cards + self._timeline_cards
            for card in all_cards:
                source_paths.extend(card.get_selected_paths())
            log.info(f"Compressor: processing {len(source_paths)} files from result checkboxes")

        for p in source_paths:
            ext = os.path.splitext(p)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                video_paths.append(p)
            else:
                image_paths.append(p)

        if not image_paths and not video_paths:
            messagebox.showinfo("No Selection", "Please either browse a folder in this tab OR select files from the results categories first.")
            return

        quality = int(self._quality_slider.get())
        crf = int(self._crf_slider.get())
        in_place = self._inplace_var.get()

        for w in self._compress_output.winfo_children(): w.destroy()
        ctk.CTkLabel(self._compress_output, text=f"Compressing {len(image_paths)} image(s) and {len(video_paths)} video(s)...", text_color=TEXT_MUTED).pack(pady=8)

        start_time = time.perf_counter()
        def _do_compress():
            results = []
            for p in image_paths:
                res = compressor.compress_image(p, quality=quality, in_place=in_place)
                results.append(res)
            for p in video_paths:
                res = compressor.compress_video(p, crf=crf, in_place=in_place)
                results.append(res)
            
            duration = time.perf_counter() - start_time
            self.after(0, lambda: self._show_compress_results(results, duration))

        threading.Thread(target=_do_compress, daemon=True).start()

    def _show_compress_results(self, results, duration):
        for w in self._compress_output.winfo_children(): w.destroy()
        total_saved = sum(r.saved_bytes for r in results if r.success)
        ok = sum(1 for r in results if r.success)
        fail = sum(1 for r in results if not r.success)
        
        summary = f"✅ {ok} compressed, ❌ {fail} failed — saved {total_saved / (1024*1024):.1f} MB (Completed in {duration:.1f}s)"
        ctk.CTkLabel(self._compress_output, text=summary, font=ctk.CTkFont(weight="bold"), text_color=SUCCESS).pack(pady=(8, 4))
        for r in results:
            if r.success:
                saved_kb = r.saved_bytes / 1024
                line = f"  ✔  {os.path.basename(r.original_path)}  →  {saved_kb:.0f} KB saved"
                col = "white"
            else:
                line = f"  ✘  {os.path.basename(r.original_path)}: {r.error}"
                col = DANGER
            ctk.CTkLabel(self._compress_output, text=line, text_color=col, font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=12)

def launch():
    log = logging.getLogger("ui.launch")
    try:
        log.info("Creating SmartPhotoCleanerApp instance...")
        app = SmartPhotoCleanerApp()
        log.info("App created successfully, entering mainloop...")
        app.mainloop()
        log.info("Mainloop ended")
    except Exception as e:
        log.critical(f"Launch failed with error: {str(e)}", exc_info=True)
        raise
