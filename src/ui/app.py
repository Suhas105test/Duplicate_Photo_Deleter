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
    scan_folder, scan_folders, scan_files,
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
    BG_DARK, BG_CARD, ACCENT, DANGER, SUCCESS, WARN, TEXT_MUTED, TEXT_DIM, WARM_ORANGE, BG_THUMB, TEXT_BLACK
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

        self._folder_paths: list[str] = []           # Multi-folder scanning
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
        self._folder_row_widgets = []
        self._tab_count_labels = {}
        self._render_state = {}

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
        log.info("Closing application...")
        self._cancel_event.set()
        if hasattr(self, "_thumb_cache"):
            self._thumb_cache.shutdown()
        self.quit()
        self.destroy()
        # Failsafe to ensure process dies
        import os
        os._exit(0)

    def _build_layout(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # ── Sidebar ─────────────────────────────────────────────────────────
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color=BG_CARD)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(11, weight=1)

        ctk.CTkLabel(
            self.sidebar_frame, text="Smart Photo\nCleaner",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT
        ).grid(row=0, column=0, padx=20, pady=(20, 30))

        self.nav_btns = {}
        for i, text in enumerate(["Dashboard", "Duplicates", "Similar Photos", "Screenshots", "Blurry Photos", "Large Files", "Messages Media", "Timeline Viewer", "Media Compressor", "Settings"]):
            btn = ctk.CTkButton(
                self.sidebar_frame, text=text, fg_color="transparent", text_color="white",
                hover_color="#334155", anchor="w", command=lambda t=text: self.select_frame_by_name(t)
            )
            btn.grid(row=i+1, column=0, padx=10, pady=5, sticky="ew")
            self.nav_btns[text] = btn

        # Theme toggle at bottom of sidebar
        self._theme_btn = ctk.CTkButton(
            self.sidebar_frame, text="☀ Light Mode", fg_color="#334155", hover_color="#475569",
            command=self._toggle_theme
        )
        self._theme_btn.grid(row=11, column=0, padx=20, pady=20)

        # ── Main Content Container ──────────────────────────────────────────
        self.main_container = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        self.main_container.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)

        self.frames = {}

        # 1. Dashboard
        dash = ctk.CTkScrollableFrame(self.main_container, fg_color="transparent")
        self.frames["Dashboard"] = dash
        self._build_dashboard(dash)

        # 2. Duplicates
        dupes = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Duplicates"] = dupes
        self._build_tab_header(dupes, "Duplicates")
        self._scroll_exact = ctk.CTkScrollableFrame(dupes, fg_color="transparent")
        self._scroll_exact.pack(fill="both", expand=True)
        empty_frame = ctk.CTkFrame(self._scroll_exact, fg_color="transparent")
        empty_frame.pack(expand=True, pady=80)
        ctk.CTkLabel(empty_frame, text="No Files", text_color=TEXT_MUTED, font=ctk.CTkFont(size=16)).pack()
        help_btn = ctk.CTkButton(empty_frame, text="?", width=30, height=30, font=ctk.CTkFont(size=12), command=lambda: messagebox.showinfo("Duplicates", self._tab_descriptions["Duplicates"]))
        help_btn.pack(pady=(10, 0))

        # 3. Similar Photos
        sim = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Similar Photos"] = sim
        self._build_tab_header(sim, "Similar Photos")
        self._scroll_similar = ctk.CTkScrollableFrame(sim, fg_color="transparent")
        self._scroll_similar.pack(fill="both", expand=True)
        empty_frame = ctk.CTkFrame(self._scroll_similar, fg_color="transparent")
        empty_frame.pack(expand=True, pady=80)
        ctk.CTkLabel(empty_frame, text="No Files", text_color=TEXT_MUTED, font=ctk.CTkFont(size=16)).pack()
        help_btn = ctk.CTkButton(empty_frame, text="?", width=30, height=30, font=ctk.CTkFont(size=12), command=lambda: messagebox.showinfo("Similar Photos", self._tab_descriptions["Similar Photos"]))
        help_btn.pack(pady=(10, 0))

        # 4. Screenshots
        scr = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Screenshots"] = scr
        self._build_tab_header(scr, "Screenshots")
        self._scroll_screenshots = ctk.CTkScrollableFrame(scr, fg_color="transparent")
        self._scroll_screenshots.pack(fill="both", expand=True)
        empty_frame = ctk.CTkFrame(self._scroll_screenshots, fg_color="transparent")
        empty_frame.pack(expand=True, pady=80)
        ctk.CTkLabel(empty_frame, text="No Files", text_color=TEXT_MUTED, font=ctk.CTkFont(size=16)).pack()
        help_btn = ctk.CTkButton(empty_frame, text="?", width=30, height=30, font=ctk.CTkFont(size=12), command=lambda: messagebox.showinfo("Screenshots", self._tab_descriptions["Screenshots"]))
        help_btn.pack(pady=(10, 0))

        # 5. Blurry Photos
        blur = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Blurry Photos"] = blur
        self._build_tab_header(blur, "Blurry Photos")
        self._scroll_blurry = ctk.CTkScrollableFrame(blur, fg_color="transparent")
        self._scroll_blurry.pack(fill="both", expand=True)
        empty_frame = ctk.CTkFrame(self._scroll_blurry, fg_color="transparent")
        empty_frame.pack(expand=True, pady=80)
        ctk.CTkLabel(empty_frame, text="No Files", text_color=TEXT_MUTED, font=ctk.CTkFont(size=16)).pack()
        help_btn = ctk.CTkButton(empty_frame, text="?", width=30, height=30, font=ctk.CTkFont(size=12), command=lambda: messagebox.showinfo("Blurry Photos", self._tab_descriptions["Blurry Photos"]))
        help_btn.pack(pady=(10, 0))

        # 6. Large Files
        large = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Large Files"] = large
        self._build_tab_header(large, "Large Files")
        self._scroll_large = ctk.CTkScrollableFrame(large, fg_color="transparent")
        self._scroll_large.pack(fill="both", expand=True)
        empty_frame = ctk.CTkFrame(self._scroll_large, fg_color="transparent")
        empty_frame.pack(expand=True, pady=80)
        ctk.CTkLabel(empty_frame, text="No Files", text_color=TEXT_MUTED, font=ctk.CTkFont(size=16)).pack()
        help_btn = ctk.CTkButton(empty_frame, text="?", width=30, height=30, font=ctk.CTkFont(size=12), command=lambda: messagebox.showinfo("Large Files", self._tab_descriptions["Large Files"]))
        help_btn.pack(pady=(10, 0))

        # 7. Messages Media
        msg = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Messages Media"] = msg
        self._build_tab_header(msg, "Messages Media")
        self._scroll_messages = ctk.CTkScrollableFrame(msg, fg_color="transparent")
        self._scroll_messages.pack(fill="both", expand=True)
        empty_frame = ctk.CTkFrame(self._scroll_messages, fg_color="transparent")
        empty_frame.pack(expand=True, pady=80)
        ctk.CTkLabel(empty_frame, text="No Files", text_color=TEXT_MUTED, font=ctk.CTkFont(size=16)).pack()
        help_btn = ctk.CTkButton(empty_frame, text="?", width=30, height=30, font=ctk.CTkFont(size=12), command=lambda: messagebox.showinfo("Messages Media", self._tab_descriptions["Messages Media"]))
        help_btn.pack(pady=(10, 0))

        # 8. Timeline Viewer
        timeline = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Timeline Viewer"] = timeline
        self._build_tab_header(timeline, "Timeline Viewer")
        self._scroll_timeline = ctk.CTkScrollableFrame(timeline, fg_color="transparent")
        self._scroll_timeline.pack(fill="both", expand=True)
        empty_frame = ctk.CTkFrame(self._scroll_timeline, fg_color="transparent")
        empty_frame.pack(expand=True, pady=80)
        ctk.CTkLabel(empty_frame, text="No Files", text_color=TEXT_MUTED, font=ctk.CTkFont(size=16)).pack()
        help_btn = ctk.CTkButton(empty_frame, text="?", width=30, height=30, font=ctk.CTkFont(size=12), command=lambda: messagebox.showinfo("Timeline Viewer", self._tab_descriptions["Timeline Viewer"]))
        help_btn.pack(pady=(10, 0))

        # 9. Media Compressor
        compressor_tab = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Media Compressor"] = compressor_tab
        self._build_compressor_tab(compressor_tab)

        # 10. Settings
        stg = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Settings"] = stg
        self._build_settings(stg)

        # Shared Action Bar for all views except Dashboard & Settings
        self._action_bar = ctk.CTkFrame(self.main_container, fg_color=BG_CARD, corner_radius=12)
        
        self._delete_btn = ctk.CTkButton(
            self._action_bar, text="🗑  Delete Selected", height=40, font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=DANGER, hover_color="#B91C1C", command=self._delete_selected, state="disabled"
        )
        self._delete_btn.pack(side="left", padx=12, pady=10)

        self._quick_clean_btn = ctk.CTkButton(
            self._action_bar, text="⚡ Quick Clean", height=40, font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=WARN, hover_color="#D97706", command=self._quick_clean, state="disabled"
        )
        self._quick_clean_btn.pack(side="left", padx=(0, 8), pady=10)

        self._select_all_btn = ctk.CTkButton(
            self._action_bar, text="✓  Auto-Select Relevant", height=40, font=ctk.CTkFont(size=12),
            fg_color="#334155", hover_color="#475569", command=self._select_all_duplicates, state="disabled"
        )
        self._select_all_btn.pack(side="left", padx=(0, 8), pady=10)

        self._result_label = ctk.CTkLabel(self._action_bar, text="", font=ctk.CTkFont(size=12), text_color=SUCCESS)
        self._result_label.pack(side="right", padx=16)

        # Start on dashboard
        self.select_frame_by_name("Dashboard")


    def select_frame_by_name(self, name: str):
        # Update button colors
        for btn_name, btn in self.nav_btns.items():
            if btn_name == name:
                btn.configure(fg_color="#334155")
            else:
                btn.configure(fg_color="transparent")

        # Hide all frames
        for frame in self.frames.values():
            frame.grid_forget()
        self._action_bar.grid_forget()

        # Show selected frame
        self.frames[name].grid(row=0, column=0, sticky="nsew")
        if name not in ["Dashboard", "Settings"]:
            self._action_bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))


    def _build_dashboard(self, parent):
        # ── Hero Header ─────────────────────────────────────────────────────
        hero = ctk.CTkFrame(parent, fg_color=("#1E40AF", "#1E3A5F"), corner_radius=16)
        hero.pack(fill="x", pady=(0, 20))

        hero_inner = ctk.CTkFrame(hero, fg_color="transparent")
        hero_inner.pack(fill="x", padx=24, pady=18)

        ctk.CTkLabel(
            hero_inner,
            text="🧹 Smart Photo Cleaner",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="white"
        ).pack(anchor="w")

        ctk.CTkLabel(
            hero_inner,
            text="Scan, identify, and remove duplicate & unwanted photos in seconds.",
            font=ctk.CTkFont(size=12),
            text_color="#93C5FD"
        ).pack(anchor="w", pady=(4, 0))

        # ── Folder Selection Panel ───────────────────────────────────────────
        folder_panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=14)
        folder_panel.pack(fill="both", pady=(0, 14), expand=True)

        folder_header = ctk.CTkFrame(folder_panel, fg_color="transparent")
        folder_header.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(folder_header, text="📁  Scan Target", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkLabel(folder_header, text="Add one or more folders or select individual files", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).pack(side="left", padx=(10, 0))

        path_row = ctk.CTkFrame(folder_panel, fg_color="transparent")
        path_row.pack(fill="x", padx=16, pady=(0, 10))

        self._folder_entry = ctk.CTkEntry(
            path_row, placeholder_text="No folder selected — click Browse or Select Files...",
            height=36, font=ctk.CTkFont(size=12), state="readonly",
            border_color=("#CBD5E1", "#334155")
        )
        self._folder_entry.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            path_row, text="📁  Browse", width=110, height=36,
            fg_color=ACCENT, hover_color=("#1D4ED8", "#2563EB"),
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._select_folder
        ).pack(side="left", padx=(8, 0))

        ctk.CTkButton(
            path_row, text="📄 Files", width=90, height=36,
            fg_color=("#334155", "#334155"), hover_color="#475569",
            font=ctk.CTkFont(size=12),
            command=self._select_files
        ).pack(side="left", padx=(6, 0))

        self._folder_list_frame = ctk.CTkScrollableFrame(folder_panel, fg_color="transparent", height=80)
        self._folder_list_frame.pack(fill="both", padx=16, pady=(0, 12), expand=True)
        self._empty_folder_label = ctk.CTkLabel(
            self._folder_list_frame,
            text="No folders selected yet. Click Browse above to get started.",
            text_color=TEXT_MUTED, font=ctk.CTkFont(size=11), justify="center"
        )
        self._empty_folder_label.pack(pady=10)

        # ── Scan Controls & Progress ─────────────────────────────────────────
        controls = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=14)
        controls.pack(fill="x", pady=(0, 14))

        btn_row = ctk.CTkFrame(controls, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(14, 10))

        self._scan_btn = ctk.CTkButton(
            btn_row, text="🔍  Scan Photos", width=155, height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=("#2563EB", "#3B82F6"), hover_color=("#1D4ED8", "#2563EB"),
            command=self._start_photo_scan, state="disabled"
        )
        self._scan_btn.pack(side="left")

        self._scan_videos_btn = ctk.CTkButton(
            btn_row, text="🎞️  Scan Videos", width=155, height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=("#7C3AED", "#8B5CF6"), hover_color=("#6D28D9", "#7C3AED"),
            command=self._start_video_scan, state="disabled"
        )
        self._scan_videos_btn.pack(side="left", padx=(10, 0))

        self._cancel_btn = ctk.CTkButton(
            btn_row, text="✕  Cancel", width=110, height=36,
            font=ctk.CTkFont(size=12),
            fg_color=("#7F1D1D", "#7F1D1D"), hover_color="#991B1B",
            command=self._cancel_scan, state="disabled"
        )
        self._cancel_btn.pack(side="left", padx=(10, 0))

        self._refresh_btn = ctk.CTkButton(
            btn_row, text="↺  Re-Scan", height=36, width=110,
            font=ctk.CTkFont(size=12),
            fg_color=("#334155", "#334155"), hover_color="#475569",
            command=lambda: self._start_scan(self._settings.mode, force_rescan=True),
            state="disabled"
        )
        self._refresh_btn.pack(side="left", padx=(6, 0))

        # Progress area
        prog_frame = ctk.CTkFrame(controls, fg_color=("#F8FAFC", "#0F172A"), corner_radius=10)
        prog_frame.pack(fill="x", padx=16, pady=(0, 14))

        prog_top = ctk.CTkFrame(prog_frame, fg_color="transparent")
        prog_top.pack(fill="x", padx=14, pady=(10, 4))

        self._status_label = ctk.CTkLabel(
            prog_top, text="Select a folder to begin scanning.",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_MUTED, anchor="w"
        )
        self._status_label.pack(side="left", fill="x", expand=True)

        self._elapsed_label = ctk.CTkLabel(
            prog_top, text="", font=ctk.CTkFont(size=11),
            text_color=TEXT_DIM, anchor="e"
        )
        self._elapsed_label.pack(side="right")

        self._progress_bar = ctk.CTkProgressBar(
            prog_frame, height=10, mode="determinate",
            progress_color=("#3B82F6", "#60A5FA"),
            fg_color=("#E2E8F0", "#1E293B")
        )
        self._progress_bar.pack(fill="x", padx=14, pady=(0, 6))
        self._progress_bar.set(0)

        prog_bottom = ctk.CTkFrame(prog_frame, fg_color="transparent")
        prog_bottom.pack(fill="x", padx=14, pady=(0, 10))

        self._progress_details_label = ctk.CTkLabel(
            prog_bottom, text="", font=ctk.CTkFont(size=10), text_color=TEXT_DIM, anchor="w"
        )
        self._progress_details_label.pack(side="left")

        self._speed_label = ctk.CTkLabel(
            prog_bottom, text="", font=ctk.CTkFont(size=10), text_color=TEXT_DIM, anchor="center"
        )
        self._speed_label.pack(side="left", padx=(14, 0))

        self._scan_duration_label = ctk.CTkLabel(
            prog_bottom, text="", font=ctk.CTkFont(size=10), text_color=TEXT_DIM, anchor="e"
        )
        self._scan_duration_label.pack(side="right")

        # Hidden labels (kept for backward compat but not rendered prominently)
        self._eta_label = ctk.CTkLabel(prog_frame, text="", font=ctk.CTkFont(size=10), text_color=TEXT_DIM)

        # ── Scan Overview Stats ──────────────────────────────────────────────
        stats_section = ctk.CTkFrame(parent, fg_color="transparent")
        stats_section.pack(fill="x", pady=(0, 4))

        section_hdr = ctk.CTkFrame(stats_section, fg_color="transparent")
        section_hdr.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            section_hdr, text="📊  Scan Overview",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(side="left")

        stats_frame_top = ctk.CTkFrame(stats_section, fg_color="transparent")
        stats_frame_top.pack(fill="x", pady=(0, 8))

        stats_frame_bottom = ctk.CTkFrame(stats_section, fg_color="transparent")
        stats_frame_bottom.pack(fill="x")

        self._stat_found     = self._stat_card_v2(stats_frame_top, "🖼", "Images Scanned",      "—",     "#3B82F6")
        self._stat_prefilter = self._stat_card_v2(stats_frame_top, "⚡", "Pre-filter Saved",     "—",     "#8B5CF6")
        self._stat_dupes     = self._stat_card_v2(stats_frame_top, "🔁", "Duplicate Groups",     "—",     "#EF4444")
        self._stat_waste     = self._stat_card_v2(stats_frame_top, "💾", "Reclaimable Space",    "—",     "#F59E0B")
        self._stat_selected  = self._stat_card_v2(stats_frame_bottom, "✓",  "Selected Items",       "0",     "#10B981")
        self._stat_deleted_count = self._stat_card_v2(stats_frame_bottom, "🗑", "Deleted (Session)", "0",     "#DC2626")
        self._stat_space_saved   = self._stat_card_v2(stats_frame_bottom, "✨", "Space Saved",      "0 MB",  "#16A34A")



    def _build_settings(self, parent):
        ctk.CTkLabel(parent, text="Settings", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", pady=(10, 20))
        
        panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        panel.pack(fill="x", pady=(0, 20), ipady=10)
        
        # Similarity slider
        ctk.CTkLabel(panel, text="Similarity Threshold (Hamming distance)", font=ctk.CTkFont(size=12, weight="bold"), text_color="white").pack(anchor="w", padx=20, pady=(20, 0))
        ctk.CTkLabel(panel, text="Higher values group more photos together as 'similar'. 0 means exact identical images only.", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(anchor="w", padx=20)
        
        slider_row = ctk.CTkFrame(panel, fg_color="transparent")
        slider_row.pack(fill="x", padx=20, pady=(10, 10))
        
        self._tol_label = ctk.CTkLabel(slider_row, text="0  (Exact only)", font=ctk.CTkFont(size=11, weight="bold"), text_color=ACCENT, width=140)
        self._tol_label.pack(side="right")
        
        self._tol_slider = ctk.CTkSlider(slider_row, from_=0, to=10, number_of_steps=10, command=self._on_slider_change)
        self._tol_slider.set(0)
        self._tol_slider.pack(side="left", fill="x", expand=True)

        # Prefilter toggle
        ctk.CTkLabel(panel, text="Advanced", font=ctk.CTkFont(size=12, weight="bold"), text_color="white").pack(anchor="w", padx=20, pady=(20, 0))
        self._prefilter_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            panel, text="MD5 pre-filter (skip hashing byte-identical files for faster scans)",
            variable=self._prefilter_var, font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, command=self._sync_settings
        ).pack(anchor="w", padx=20, pady=(10, 20))


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

    def _stat_card_v2(self, p, icon: str, label: str, value: str, accent_color: str):
        """Modern stat card with colored icon badge, label and value."""
        f = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=12)
        f.pack(side="left", padx=4, fill="both", expand=True)
        # Top accent line
        top = ctk.CTkFrame(f, fg_color=accent_color, corner_radius=0, height=3)
        top.pack(fill="x")
        # Icon
        ctk.CTkLabel(f, text=icon, font=ctk.CTkFont(size=20), text_color=accent_color).pack(pady=(10, 0))
        # Value (large bold)
        val = ctk.CTkLabel(f, text=value, font=ctk.CTkFont(size=20, weight="bold"))
        val.pack(pady=(2, 0))
        # Label (small muted)
        ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=9), text_color=TEXT_MUTED).pack(pady=(0, 10))
        return val

    def _build_compressor_tab(self, parent):
        # File/Folder selection panel

        # File/Folder selection panel
        sel_panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        sel_panel.pack(fill="x", pady=(0, 20), ipady=6)
        ctk.CTkLabel(sel_panel, text="Select Media", font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_BLACK).pack(anchor="w", padx=20, pady=(12, 8))
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
        ctk.CTkLabel(q_header, text="Image Quality", font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_BLACK).pack(side="left")
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
        ctk.CTkLabel(crf_header, text="Video CRF (Constant Rate Factor)", font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_BLACK).pack(side="left")
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
        self._comp_btn = ctk.CTkButton(btn_row, text="🗜 Compress Selected Files", height=44, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#0EA5E9", hover_color="#0284C7", command=self._compress_selected)
        self._comp_btn.pack(side="left")

        self._compress_output = ctk.CTkScrollableFrame(parent, fg_color=BG_CARD, corner_radius=12, height=200)
        self._compress_output.pack(fill="both", expand=True, pady=(8, 0))
        ctk.CTkLabel(self._compress_output, text="Compression results will appear here.", text_color=TEXT_MUTED, font=ctk.CTkFont(size=11)).pack(pady=20)


    # ── Multi-folder helpers ────────────────────────────────────────────────

    def _add_folder(self):
        """Open a directory chooser and add the selected folder to the list."""
        f = filedialog.askdirectory(title="Add Folder to Scan")
        if f and f not in self._folder_paths:
            self._folder_paths.append(f)
            self._selected_files_list = None
            self._refresh_folder_list_ui()
            self._update_folder_entry()
            self._scan_btn.configure(state="normal")
            self._scan_videos_btn.configure(state="normal")
            self._status_label.configure(text=f"Ready! {len(self._folder_paths)} folder(s) selected.", text_color="#10B981")
            self.update_idletasks()

    def _remove_folder(self, folder_path: str):
        """Remove a single folder from the list."""
        if folder_path in self._folder_paths:
            self._folder_paths.remove(folder_path)
        self._refresh_folder_list_ui()
        self._update_folder_entry()
        if not self._folder_paths and not self._selected_files_list:
            self._scan_btn.configure(state="disabled")
            self._scan_videos_btn.configure(state="disabled")

    def _clear_folders(self):
        """Remove all folders from the list."""
        self._folder_paths.clear()
        self._refresh_folder_list_ui()
        self._update_folder_entry()
        if not self._selected_files_list:
            self._scan_btn.configure(state="disabled")
            self._scan_videos_btn.configure(state="disabled")

    def _refresh_folder_list_ui(self):
        """Redraw the folder list inside the scrollable frame."""
        # Destroy old row widgets
        for (fr, *_) in self._folder_row_widgets:
            fr.destroy()
        self._folder_row_widgets.clear()

        if not self._folder_paths:
            self._empty_folder_label.pack(pady=10)
            return

        self._empty_folder_label.pack_forget()
        for fp in self._folder_paths:
            row = ctk.CTkFrame(self._folder_list_frame, fg_color="#1E3A5F", corner_radius=6, height=40)
            row.pack(fill="x", pady=4, padx=4)
            lbl = ctk.CTkLabel(row, text=fp, font=ctk.CTkFont(size=11),
                               text_color="white", anchor="w")
            lbl.pack(side="left", padx=8, pady=6, fill="both", expand=True)
            remove_btn = ctk.CTkButton(
                row, text="✕", width=28, height=24,
                fg_color=DANGER, hover_color="#B91C1C",
                command=lambda p=fp: self._remove_folder(p)
            )
            remove_btn.pack(side="right", padx=6, pady=6)
            self._folder_row_widgets.append((row, lbl, remove_btn))

    # Keep old _select_folder as an alias so tab-initiated scans still work
    def _select_folder(self):
        self._add_folder()

    def _select_files(self):
        files = filedialog.askopenfilenames(
            title="Select Photos/Videos",
            filetypes=[("Media Files", "*.jpg *.jpeg *.png *.heic *.mp4 *.mov *.mkv *.avi *.webp *.gif")]
        )
        if files:
            self._selected_files_list = list(files)
            self._folder_paths.clear()
            self._refresh_folder_list_ui()
            self._update_folder_entry()
            # Show a summary label inside the list frame
            self._empty_folder_label.configure(
                text=f"{len(files)} individual file(s) selected for scanning."
            )
            self._empty_folder_label.pack(pady=10)
            self._scan_btn.configure(state="normal")
            self._scan_videos_btn.configure(state="normal")
            self._status_label.configure(text=f"Ready! {len(files)} file(s) selected.", text_color="#10B981")
            self.update_idletasks()

    def _update_folder_entry(self):
        if self._selected_files_list:
            label = f"{len(self._selected_files_list)} file(s) selected"
        elif len(self._folder_paths) == 1:
            label = self._folder_paths[0]
        elif self._folder_paths:
            label = f"{len(self._folder_paths)} folders selected"
        else:
            label = ""

        self._folder_entry.configure(state="normal")
        self._folder_entry.delete(0, "end")
        self._folder_entry.insert(0, label)
        self._folder_entry.configure(state="readonly")

    def _start_photo_scan(self): self._start_scan("full")
    def _start_video_scan(self): self._start_scan("videos")
    
    def _scan_from_tab(self, tab_name: str, mode: str = "full"):
        """Initiate a scan requested from a specific tab.
        
        Always performs a scan to show progress and update timing/stats.
        """
        self._scan_request_tab = tab_name
        # show dashboard so user can observe progress controls
        self.select_frame_by_name("Dashboard")
        self._start_scan(mode)

    def _start_scan(self, mode: str, force_rescan: bool = False):
        # Validation: Ensure at least one folder or some files are selected
        if not self._folder_paths and not self._selected_files_list:
            entry_path = getattr(self, '_folder_entry', None)
            if entry_path:
                fallback_path = self._folder_entry.get().strip()
                if fallback_path and os.path.isdir(fallback_path):
                    self._folder_paths.append(fallback_path)
                    self._update_folder_entry()

        if not self._folder_paths and not self._selected_files_list:
            messagebox.showwarning("No Input", "Please add at least one folder or select files before scanning.")
            return

        # Show the user that scanning has begun before the worker thread starts
        self._set_status("Preparing file discovery...", SUCCESS)
        self._progress_details_label.configure(text="Scanning selected files..." if self._selected_files_list else "Scanning selected folders...")
        self._progress_bar.set(0.0)

        # NEW: Instant re-detection if we already have hashes and only tolerance changed (slider)
        if mode == "similar" and not force_rescan and hasattr(self, "_last_h_map") and self._last_h_map:
            log.info("Performing instant re-detection based on existing hashes...")
            self._set_scanning_ui(True)
            self._set_status("Updating similarity results...", SUCCESS)
            
            def _quick_detect():
                det = detect_duplicates(
                    self._last_h_map, 
                    hash_tolerance=self._settings.tolerance,
                    exact_byte_dupes=self._last_exact_groups,
                    all_images=self._detection_result.all_images if self._detection_result else [],
                    folder_sizes=self._detection_result.folder_sizes if self._detection_result else {},
                    mode="similar"
                )
                self.after(0, lambda: self._push_done(det))
                
            threading.Thread(target=_quick_detect, daemon=True).start()
            return

        # mode = "full" (all images/docs/metadata) or "videos" (videos only)
        # called by dashboard or feature tabs
        self._scan_start_time = time.perf_counter()
        self._start_elapsed_timer()
        self._last_count = 0
        self._is_scanning = True; self._is_paused = False
        self._cancel_event.clear(); self._pause_event.set()
        self._clear_results(); self._set_scanning_ui(True)
        self._scan_duration_label.configure(text="")
        s = ScanSettings(); s.tolerance = self._settings.tolerance; s.use_prefilter = self._prefilter_var.get()
        s.mode = mode
        if mode == "similar":
            # Optimized 'Similar only' scan: ignore docs/videos if not needed
            # (Though user might want similar videos too, usually they mean photos)
            s.target_extensions = IMAGE_EXTENSIONS
            s.do_duplicates = True
            s.do_metadata = False # Skip heavy metadata if just looking for similarity
        elif mode == "videos": 
            s.target_extensions = VIDEO_EXTENSIONS
            s.use_prefilter = True  # FIX: Enable MD5 for videos too!
            s.do_duplicates = True
            s.do_metadata = True
        else: 
            s.target_extensions = IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS | VIDEO_EXTENSIONS
            s.do_duplicates = True
            s.do_metadata = True

        self._settings = s

        if self._selected_files_list:
            self._set_status(f"Scanning {len(self._selected_files_list)} selected file(s)...", SUCCESS)
            self._progress_details_label.configure(text="Scanning selected files...")
        else:
            self._set_status(f"Scanning {len(self._folder_paths)} folder(s)...", SUCCESS)
            self._progress_details_label.configure(text="Scanning selected folders...")

        self._scan_thread = threading.Thread(target=self._scan_worker, args=(s,), daemon=True)
        self._scan_thread.start()

    def _pause_resume_scan(self):
        if self._is_paused:
            # Resume
            self._pause_event.set()
            self._is_paused = False
            pause_btn = self.__dict__.get('_pause_btn')
            if pause_btn: pause_btn.configure(text="⏸ Pause", fg_color="#F59E0B")
            self._set_status("Resuming scan...", SUCCESS)
        else:
            # Pause
            self._pause_event.clear()
            self._is_paused = True
            pause_btn = self.__dict__.get('_pause_btn')
            if pause_btn: pause_btn.configure(text="▶ Resume", fg_color="#10B981")
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

            # ── STAGE 1: File Discovery (0% → 5%) ──────────────────────────
            self._push("status", "Listing files...", 0.01, False, "")

            if self._selected_files_list:
                res = scan_files(self._selected_files_list)
            else:
                def scan_progress(count, current_path, total_scanned):
                    if self._cancel_event.is_set(): raise Exception("Scan cancelled")
                    if total_scanned == 1 or total_scanned % 10 == 0 or total_scanned == 0:
                        pct = min(0.05, 0.01 + (total_scanned / 500) * 0.04)
                        self._push("status", f"Scanning: found {total_scanned} media files...", pct, False, total_scanned)
                res = scan_folders(self._folder_paths, progress_callback=scan_progress,
                                   recursive=True, cancel_event=self._cancel_event)

            total_files = res.image_count
            self._push("partial_count", str(total_files), 0.05, False, "")
            if self._cancel_event.is_set(): return

            # Build stat_map from scanner results for cache speedup
            stat_map = {img.path: (img.timestamp, img.size_bytes) for img in res.images}

            # ── STAGE 2: MD5 Pre-filter (5% → 25%) ─────────────────────────
            self._push("status", "Starting MD5 pre-filter...", 0.06, False, "")

            singleton_paths = res.size_singleton_paths
            candidate_groups = res.size_candidate_groups

            exact_groups = []
            md5_survivors = []

            if candidate_groups:
                def prefilter_progress(done, total, phase=1):
                    if phase == 1:
                        pct = 0.05 + (done / max(total, 1)) * 0.10  # 5% → 15%
                    else:
                        pct = 0.15 + (done / max(total, 1)) * 0.10  # 15% → 25%
                    msg = f"Pre-filter phase {phase}: {done}/{total}"
                    self._push("status", msg, pct, False, done)

                exact_groups, md5_survivors = find_exact_byte_duplicates(
                    candidate_groups,
                    progress_callback=prefilter_progress,
                    cancel_event=self._cancel_event,
                    stat_map=stat_map,
                )

                # Push exact-byte duplicate groups to UI immediately (lightweight)
                if exact_groups:
                    from duplicate_detector import _exact_byte_groups_to_duplicate_groups, DetectionResult
                    byte_groups = _exact_byte_groups_to_duplicate_groups(exact_groups)
                    partial_det = DetectionResult(
                        groups=byte_groups,
                        total_images_checked=total_files,
                        unique_images=total_files - sum(len(g) - 1 for g in exact_groups),
                        prefilter_exact_count=sum(len(g) for g in exact_groups),
                    )
                    self._push("switch_tab", "Duplicates", 0.25, False)
                    self._push("update_results", partial_det, 0.25, False)
            else:
                md5_survivors = []

            if self._cancel_event.is_set(): return

            # ── STAGE 3: Perceptual Hashing (25% → 90%) ────────────────────
            # Combine all paths that need phashing
            paths_to_hash = list(singleton_paths) + list(md5_survivors)
            # Also hash one representative from each exact group (for Similar Photos)
            for group in exact_groups:
                if group:
                    paths_to_hash.append(group[0])

            # Deduplicate
            paths_to_hash = list(dict.fromkeys(paths_to_hash))

            h_map = {}
            if paths_to_hash:
                total_to_hash = len(paths_to_hash)
                def phash_progress(done, total):
                    pct = 0.25 + (done / max(total, 1)) * 0.65  # 25% → 90%
                    if done % 500 == 0 or done == total:
                        self._push("status", f"Hashing: {done}/{total}", pct, False, done)
                    elif done % 50 == 0:
                        self._push("status", f"Hashing: {done}/{total}", pct, False, done)

                h_map = generate_hashes(
                    paths_to_hash,
                    progress_callback=phash_progress,
                    cancel_event=self._cancel_event,
                    stat_map=stat_map,
                )

            if self._cancel_event.is_set(): return

            # ── STAGE 4: Final Detection & Grouping (90% → 100%) ───────────
            self._push("status", "Building duplicate groups...", 0.92, False, "")
            final_det = detect_duplicates(
                h_map,
                hash_tolerance=settings.tolerance,
                exact_byte_dupes=exact_groups,
                all_images=res.images,
                folder_sizes=dict(res.folder_sizes),
                mode=settings.mode
            )

            # Store for instant re-detection on slider change
            self._last_h_map = h_map
            self._last_exact_groups = exact_groups

            self._push("status", "Scan complete!", 1.0, False, "")
            self._progress_queue.put(("done", final_det, 1.0, False, ""))

        except Exception as e:
            error_msg = str(e)
            if "cancelled" in error_msg.lower():
                self._push("status", "Scan cancelled", 0, True, "")
            else:
                log.exception("Scan worker failed")
                self._push("status", f"Error: {error_msg}", 0, True, "")
            self._progress_queue.put(("done", None, 0, True, ""))

    def _push(self, k, m, f, e, s=None): 
        # k: key, m: msg, f: fraction, e: is_error, s: count/status
        self._progress_queue.put((k, m, f, e, s))

    def _start_elapsed_timer(self):
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
                        if m[2] is not False and float(m[2]) >= 0:
                            self._progress_bar.set(m[2])
                        details = []
                        if m[2] is not False and isinstance(m[2], (float, int)):
                            details.append(f"{int(float(m[2]) * 100)}%")
                        if m[4] not in (None, ""):
                            try:
                                count = int(float(m[4]))
                                details.append(f"{count} files scanned")
                            except Exception:
                                pass
                        self._progress_details_label.configure(text=" · ".join(details) if details else m[1] or "Scanning...")
                        try:
                            if self._is_scanning and self._scan_start_time and m[4]:
                                elapsed = time.perf_counter() - self._scan_start_time
                                count = float(m[4])
                                if elapsed > 0:
                                    self._speed_label.configure(text=f"Speed: {count/elapsed:.1f} files/sec")
                        except Exception:
                            pass
                    elif m[0] == "partial_count":
                        self._stat_found.configure(text=m[1])
                        self._progress_bar.set(m[2])
                        if m[2] is not False and isinstance(m[2], (float, int)):
                            self._progress_details_label.configure(text=f"{int(float(m[2]) * 100)}% discovered")
                        else:
                            self._progress_details_label.configure(text="Files discovered")
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
                        pause_btn = self.__dict__.get('_pause_btn')
                        if pause_btn:
                            if self._is_paused:
                                pause_btn.configure(text="▶ Resume", fg_color="#10B981")
                            else:
                                pause_btn.configure(text="⏸ Pause", fg_color="#F59E0B")
                    elif m[0] == "switch_tab":
                        self.select_frame_by_name(m[1])
                    elif m[0] == "update_results":
                        self._render_results(m[1], is_partial=True)  # noqa: ok
                    elif m[0] == "done":
                        # final stats
                        self._is_scanning = False; self._is_paused = False
                        self._push_done(m[1])
                        break
                except queue.Empty:
                    break
                except Exception as loop_err:
                    import logging
                    logging.getLogger(__name__).error("Error in _poll_progress loop: %s", loop_err, exc_info=True)
        except queue.Empty: pass
        self.after(150, self._poll_progress)

    def _render_results(self, detection: DetectionResult, is_partial: bool = False):
        self._clear_results()

        self._stat_found.configure(text=f"{detection.total_images_checked:,}")
        saved = detection.prefilter_exact_count
        self._stat_prefilter.configure(text=f"{saved:,}" if saved > 0 else "—")
        self._stat_dupes.configure(text=str(detection.duplicate_group_count))
        waste_mb = detection.total_wasted_mb()
        self._stat_waste.configure(text=f"{waste_mb:.1f} MB")
        self._stat_selected.configure(text="0")

        exact_groups = [g for g in detection.groups if g.match_type in ("exact_bytes", "exact_hash")]
        similar_groups = [g for g in detection.groups if g.match_type == "near_duplicate"]

        if not exact_groups:
            ctk.CTkLabel(self._scroll_exact, text="✅  No exact duplicates found!", font=ctk.CTkFont(size=15), text_color=SUCCESS, justify="center").pack(expand=True, pady=60)
        else:
            for group in exact_groups:
                card = DuplicateGroupCard(self._scroll_exact, group, thumb_cache=self._thumb_cache, on_selection_change=self._update_selected_count)
                card.pack(fill="x", padx=8, pady=(0, 10))
                self._exact_group_cards.append(card)

        if not similar_groups:
            ctk.CTkLabel(self._scroll_similar, text="✅  No similar photos found!", font=ctk.CTkFont(size=15), text_color=SUCCESS, justify="center").pack(expand=True, pady=60)
        else:
            for group in similar_groups:
                card = DuplicateGroupCard(self._scroll_similar, group, thumb_cache=self._thumb_cache, on_selection_change=self._update_selected_count)
                card.pack(fill="x", padx=8, pady=(0, 10))
                self._similar_group_cards.append(card)

        if detection.screenshots:
            card = FileGridCard(self._scroll_screenshots, "Screenshots", detection.screenshots, self._thumb_cache, self._update_selected_count)
            card.pack(fill="x", padx=8, pady=(0, 10))
            self._screenshot_cards.append(card)
        else:
            ctk.CTkLabel(self._scroll_screenshots, text="✅  No screenshots found!", font=ctk.CTkFont(size=15), text_color=SUCCESS, justify="center").pack(expand=True, pady=60)

        if detection.blurry_photos:
            card = FileGridCard(self._scroll_blurry, "Blurry Photos", detection.blurry_photos, self._thumb_cache, self._update_selected_count)
            card.pack(fill="x", padx=8, pady=(0, 10))
            self._blurry_cards.append(card)
        else:
            ctk.CTkLabel(self._scroll_blurry, text="✅  No blurry photos found!", font=ctk.CTkFont(size=15), text_color=SUCCESS, justify="center").pack(expand=True, pady=60)

        if detection.large_files:
            card = FileGridCard(self._scroll_large, "Large Files (>2MB)", detection.large_files, self._thumb_cache, self._update_selected_count)
            card.pack(fill="x", padx=8, pady=(0, 10))
            self._large_cards.append(card)
        else:
            ctk.CTkLabel(self._scroll_large, text="✅  No large files found!", font=ctk.CTkFont(size=15), text_color=SUCCESS, justify="center").pack(expand=True, pady=60)

        chat_media = list(getattr(detection, "whatsapp_media", [])) + list(getattr(detection, "telegram_media", []))
        if chat_media:
            card = FileGridCard(self._scroll_messages, "Messages Media", chat_media, self._thumb_cache, self._update_selected_count)
            card.pack(fill="x", padx=8, pady=(0, 10))
            self._message_cards.append(card)
        else:
            ctk.CTkLabel(self._scroll_messages, text="✅  No message media found!", font=ctk.CTkFont(size=15), text_color=SUCCESS, justify="center").pack(expand=True, pady=60)

        if detection.timeline:
            timeline_files = [path for year in sorted(detection.timeline.keys(), reverse=True) for path in detection.timeline[year]]
            card = FileGridCard(self._scroll_timeline, "Timeline Viewer", timeline_files, self._thumb_cache, self._update_selected_count)
            card.pack(fill="x", padx=8, pady=(0, 10))
            self._timeline_cards.append(card)
        else:
            ctk.CTkLabel(self._scroll_timeline, text="✅  No timeline items available yet!", font=ctk.CTkFont(size=15), text_color=SUCCESS, justify="center").pack(expand=True, pady=60)

        self._select_all_btn.configure(state="normal")
        self._refresh_btn.configure(state="normal")
        self._quick_clean_btn.configure(state="normal")

        # Populate _render_state so _update_tab_titles() doesn't crash with KeyError
        self._render_state = {
            "exact":       {"groups": exact_groups},
            "similar":     {"groups": similar_groups},
            "screenshots": {"items": detection.screenshots},
            "blurry":      {"items": detection.blurry_photos},
            "large":       {"items": detection.large_files},
            "messages":    {"items": list(getattr(detection, "whatsapp_media", [])) + list(getattr(detection, "telegram_media", []))},
            "timeline":    {"items": [p for paths in detection.timeline.values() for p in paths] if detection.timeline else []},
        }

        # Trigger thumbnail loading for all cards (deferred, avoids blocking UI)
        self.after(200, self._check_auto_load)

        # Store result for use by dashboard refresh
        self._detection_result = detection

        elapsed = time.perf_counter() - self._scan_start_time
        self._set_status(f"Scan complete in {elapsed:.1f}s — {detection.duplicate_group_count} group(s) · {waste_mb:.1f} MB reclaimable", SUCCESS)

    def _clear_results(self):
        # Reset Dashboard Stats
        if hasattr(self, "_stat_found"):
            self._stat_found.configure(text="0")
            self._stat_dupes.configure(text="0")
            self._stat_waste.configure(text="0 MB")
            self._stat_prefilter.configure(text="0")
            self._stat_selected.configure(text="0")
            self._stat_deleted_count.configure(text="0")
            self._stat_space_saved.configure(text="0 MB")
        
        # Clear result lists
        self._detection_result = None
        for w in self._scroll_exact.winfo_children(): w.destroy()
        self._exact_group_cards.clear()
        for w in self._scroll_similar.winfo_children(): w.destroy()
        self._similar_group_cards.clear()
        for card in self._timeline_cards: card.destroy()
        self._timeline_cards.clear()

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
        self._stat_selected.configure(text="0")
        self._stat_deleted_count.configure(text="0")
        self._stat_space_saved.configure(text="0 MB")
        self._delete_btn.configure(state="disabled", text="Delete Selected")
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

        # IMPORTANT: Calculate bytes saved BEFORE deletion
        path_size_map = {}
        for path in paths:
            try:
                path_size_map[path] = os.path.getsize(path)
            except Exception:
                path_size_map[path] = 0
                
        res = delete_files(paths)
        deleted_set = set(res.deleted)
        
        # Calculate actual bytes saved for successfully deleted files
        bytes_saved = sum(path_size_map.get(p, 0) for p in res.deleted)
        
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
        
        if res.deleted:
            self._show_toast(f"Successfully moved {len(res.deleted)} files to the Recycle Bin.")
        else:
            # If nothing was deleted, show why
            error_msg = res.failed[0][1] if res.failed else "Check file permissions or Recycle Bin space."
            messagebox.showerror("Delete Failed", f"Could not move files to Recycle Bin.\n\nError: {error_msg}")
            
        if has_undoable_deletes(): self._undo_btn.pack(side="right", padx=10)
        
        # Finally update tab titles to reflect new counts
        self._update_tab_titles()

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
            self._update_tab_titles() # NEW: Sync titles
            return True
        return False

    def _update_tab_titles(self):
        """Standardized helper to update the (X groups, Y photos) header labels for all tabs."""
        if not hasattr(self, "_render_state"): return
        for name in ["Duplicates", "Similar Photos", "Screenshots", "Blurry Photos", "Large Files", "Messages Media", "Timeline Viewer"]:
            if name in self._tab_count_labels:
                state_key = {
                    "Duplicates": "exact", "Similar Photos": "similar",
                    "Screenshots": "screenshots", "Blurry Photos": "blurry",
                    "Large Files": "large", "Messages Media": "messages",
                    "Timeline Viewer": "timeline"
                }[name]
                st = self._render_state.get(state_key)
                if st is None:
                    continue
                try:
                    if "groups" in st:
                        n_groups = len(st["groups"])
                        n_photos = sum(len(g.files) if hasattr(g, "files") else g.count for g in st["groups"])
                        self._tab_count_labels[name].configure(text=f"({n_groups} groups, {n_photos} photos)")
                    else:
                        n_items = len(st["items"])
                        self._tab_count_labels[name].configure(text=f"({n_items} items)")
                except Exception as exc:
                    log.debug("_update_tab_titles error for %s: %s", name, exc)

    def _build_tab_header(self, parent, title: str):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", pady=(10, 0), padx=16)
        ctk.CTkLabel(header, text=title, font=ctk.CTkFont(size=15, weight="bold"), text_color="white", anchor="w").pack(side="left")
        ctk.CTkButton(
            header,
            text="?",
            width=30,
            height=30,
            font=ctk.CTkFont(size=12),
            fg_color=BG_CARD,
            hover_color="#475569",
            command=lambda: messagebox.showinfo(title, self._tab_descriptions.get(title, "No information available")),
        ).pack(side="right")

    def _set_status(self, t, c=TEXT_MUTED): self._status_label.configure(text=t, text_color=c)
    def _set_scanning_ui(self, s):
        st = "disabled" if s else "normal"
        self._scan_btn.configure(state=st); self._scan_videos_btn.configure(state=st)
        # disable per-tab scan buttons too
        for b in getattr(self, '_tab_scan_buttons', []):
            b.configure(state=st)
        # _pause_btn is optional - only configure if it exists
        pause_btn = self.__dict__.get('_pause_btn')
        if pause_btn is not None:
            try:
                pause_btn.configure(state="normal" if s else "disabled")
            except Exception:
                pass
        self._cancel_btn.configure(state="normal" if s else "disabled")
        if self.__dict__.get('_refresh_btn') is not None:
            self._refresh_btn.configure(state="disabled" if s else "normal")

    def _push_done(self, r):
        if r:
            # Switch view if needed
            if not self._scan_request_tab or self._scan_request_tab == "Dashboard":
                self.select_frame_by_name("Duplicates")
            elif self._scan_request_tab:
                self.select_frame_by_name(self._scan_request_tab)
            self._scan_request_tab = None
            
            # Cache results for instant re-detection
            self._detection_result = r
            self._last_h_map = getattr(r, "hash_map", {})
            self._last_exact_groups = getattr(r, "exact_byte_dupes", [])
            
            # Performance stats
            dur = time.perf_counter() - self._scan_start_time
            self._scan_duration_label.configure(text=f"Last Scan: {dur:.1f}s")
            
            # Render
            self._render_results(r)
        
        self._set_scanning_ui(False)
        self._stop_elapsed_timer()
        self._set_status("Ready", TEXT_DIM)
        self._update_tab_titles()

    def _on_slider_change(self, v):
        val = int(float(v))
        self._settings.tolerance = val
        label = "(Exact only)" if val == 0 else f"(~{val * 4}% diff allowed)"
        self._tol_label.configure(text=f"{val}  {label}")
        self._sync_settings()
        # Trigger instant re-detection for slider
        if hasattr(self, "_last_h_map") and self._last_h_map:
             self._start_scan("similar", force_rescan=False)

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
            messagebox.showinfo("Delete All Duplicates", "No duplicate copies found to delete.")
            return
        if messagebox.askyesno("Confirm Mass Delete", f"Ready to move {t} duplicate copies (leaving the best original version of each) to the Recycle Bin. Proceed?"):
            # Call delete logic directly to avoid the second confirmation dialog
            paths = []
            all_card_lists = [self._exact_group_cards, self._similar_group_cards]
            for clist in all_card_lists:
                for c in clist:
                    paths.extend(c.get_selected_paths())
            # IMPORTANT: Pre-calculate sizes
            path_size_map = {}
            for p in paths:
                try: path_size_map[p] = os.path.getsize(p)
                except: path_size_map[p] = 0

            res = delete_files(paths)
            deleted_set = set(res.deleted)
            for clist in all_card_lists:
                to_remove = [c for c in clist if c.remove_paths(deleted_set)]
                for c in to_remove:
                    c.destroy(); clist.remove(c)
            
            # Sync session stats for Mass Delete too
            quick_bytes_saved = sum(path_size_map.get(p, 0) for p in res.deleted)
            
            self._session_deleted_count += len(res.deleted)
            self._session_saved_bytes += quick_bytes_saved
            
            self._stat_deleted_count.configure(text=str(self._session_deleted_count))
            self._stat_space_saved.configure(text=f"{self._session_saved_bytes / (1024*1024):.1f} MB")
            
            self._update_selected_count()
            self._refresh_dashboard_stats()
            self._update_global_dashboard_stats()
            self._update_tab_titles() # NEW: Sync titles
            
            # IMPORTANT: Auto-load more after Mass Delete
            self._check_auto_load()
            
            # Scroll to top
            self._scroll_active_tab_to_top()
            
            self._show_toast(f"Mass Delete: Moved {len(res.deleted)} duplicate file(s) to Recycle Bin")
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

        progress_bar = ctk.CTkProgressBar(self._compress_output, width=300)
        progress_bar.pack(pady=10)
        progress_bar.set(0)

        start_time = time.perf_counter()
        
        def _update_progress(p: float):
            self.after(0, lambda: progress_bar.set(p))
            
        def _do_compress():
            results = []
            for p in image_paths:
                res = compressor.compress_image(p, quality=quality, in_place=in_place)
                results.append(res)
            for p in video_paths:
                res = compressor.compress_video(p, crf=crf, in_place=in_place, progress_callback=_update_progress)
                results.append(res)
                # Reset progress for the next video if any
                _update_progress(0)
            
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

    # ── Missing utility methods ─────────────────────────────────────────────

    def _show_toast(self, message: str, duration_ms: int = 3000):
        """Show a floating toast notification at the bottom of the screen."""
        try:
            self._toast_label.configure(text=f"  {message}  ")
            self._toast_frame.place(relx=0.5, rely=0.93, anchor="center")
            self.after(duration_ms, self._hide_toast)
        except Exception:
            pass

    def _hide_toast(self):
        try:
            self._toast_frame.place_forget()
        except Exception:
            pass

    def _refresh_dashboard_stats(self):
        """Re-compute dashboard stat cards from current detection result."""
        if not hasattr(self, "_detection_result") or self._detection_result is None:
            return
        det = self._detection_result
        try:
            self._stat_found.configure(text=f"{det.total_images_checked:,}")
            saved = det.prefilter_exact_count
            self._stat_prefilter.configure(text=f"{saved:,}" if saved > 0 else "—")
            remaining_groups = [g for g in det.groups if len(g.files) >= 2]
            self._stat_dupes.configure(text=str(len(remaining_groups)))
            waste_mb = sum(g.wasted_bytes() for g in remaining_groups) / (1024 * 1024)
            self._stat_waste.configure(text=f"{waste_mb:.1f} MB")
        except Exception as exc:
            log.debug("_refresh_dashboard_stats error: %s", exc)

    def _update_global_dashboard_stats(self):
        """Sync session-level stats (deleted count + space saved) to dashboard cards."""
        try:
            self._stat_deleted_count.configure(text=str(self._session_deleted_count))
            self._stat_space_saved.configure(text=f"{self._session_saved_bytes / (1024 * 1024):.1f} MB")
        except Exception as exc:
            log.debug("_update_global_dashboard_stats error: %s", exc)

    def _check_auto_load(self):
        """After deletion, re-trigger thumbnail loading for remaining cards."""
        all_card_lists = [
            self._exact_group_cards, self._similar_group_cards,
            self._screenshot_cards, self._blurry_cards,
            self._large_cards, self._message_cards, self._timeline_cards
        ]
        for clist in all_card_lists:
            for card in clist:
                if hasattr(card, "load_thumbnails"):
                    try:
                        card.load_thumbnails()
                    except Exception:
                        pass

    def _scroll_active_tab_to_top(self):
        """Scroll the currently active tab's scroll frame back to the top."""
        try:
            scroll_map = {
                "Duplicates": self._scroll_exact,
                "Similar Photos": self._scroll_similar,
                "Screenshots": self._scroll_screenshots,
                "Blurry Photos": self._scroll_blurry,
                "Large Files": self._scroll_large,
                "Messages Media": self._scroll_messages,
                "Timeline Viewer": self._scroll_timeline,
            }
            for name, frame in self.frames.items():
                try:
                    if frame.winfo_ismapped() and name in scroll_map:
                        scroll_map[name]._parent_canvas.yview_moveto(0)
                        break
                except Exception:
                    pass
        except Exception as exc:
            log.debug("_scroll_active_tab_to_top error: %s", exc)


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
