import re
from pathlib import Path


def find_ui_file() -> Path:
    candidates = [Path("src/ui.py"), Path("src/ui/app.py")]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    ui_dir = Path("src/ui")
    if ui_dir.is_dir():
        py_files = sorted(ui_dir.glob("*.py"))
        if py_files:
            return py_files[0]

    raise FileNotFoundError(
        "Cannot locate the UI source file. Expected 'src/ui.py' or 'src/ui/app.py'. "
        "Create the file or update this script with the correct target path."
    )


def replace_block(content: str, old: str, new: str, description: str) -> str:
    if old not in content:
        print(f"Warning: {description} not found; skipping.")
        return content
    return content.replace(old, new)


def replace_pattern(content: str, pattern: re.Pattern, new: str, description: str) -> str:
    if not pattern.search(content):
        print(f"Warning: {description} pattern not found; skipping.")
        return content
    return pattern.sub(new, content)


def main():
    ui_path = find_ui_file()
    with ui_path.open("r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update __init__ tracking lists
    old_init_lists = """        self._group_cards: list[DuplicateGroupCard] = []
        self._screenshot_cards: list[FileGridCard] = []
        self._blurry_cards: list[FileGridCard] = []
        self._large_cards: list[FileGridCard] = []"""
    new_init_lists = """        self._exact_group_cards: list[DuplicateGroupCard] = []
        self._similar_group_cards: list[DuplicateGroupCard] = []
        self._screenshot_cards: list[FileGridCard] = []
        self._blurry_cards: list[FileGridCard] = []
        self._large_cards: list[FileGridCard] = []"""
    content = replace_block(content, old_init_lists, new_init_lists, "__init__ tracking list updates")

    # 2. Complete rewrite of _build_layout
    # Since _build_layout is very long, we replace the old block only if it matches the expected pattern.
    layout_pattern = re.compile(r"    def _build_layout\(self\):.*?    def _stat_card", re.DOTALL)

    new_layout = """    def _build_layout(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # ── Sidebar ─────────────────────────────────────────────────────────
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color=BG_CARD)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(8, weight=1)

        ctk.CTkLabel(
            self.sidebar_frame, text="Smart Photo\\nCleaner",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT
        ).grid(row=0, column=0, padx=20, pady=(20, 30))

        self.nav_btns = {}
        for i, text in enumerate(["Dashboard", "Duplicates", "Similar Photos", "Screenshots", "Blurry Photos", "Large Files", "Settings"]):
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
        self._theme_btn.grid(row=9, column=0, padx=20, pady=20)

        # ── Main Content Container ──────────────────────────────────────────
        self.main_container = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        self.main_container.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)

        self.frames = {}

        # 1. Dashboard
        dash = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Dashboard"] = dash
        self._build_dashboard(dash)

        # 2. Duplicates
        dupes = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Duplicates"] = dupes
        self._scroll_exact = ctk.CTkScrollableFrame(dupes, fg_color="transparent")
        self._scroll_exact.pack(fill="both", expand=True)

        # 3. Similar Photos
        sim = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Similar Photos"] = sim
        self._scroll_similar = ctk.CTkScrollableFrame(sim, fg_color="transparent")
        self._scroll_similar.pack(fill="both", expand=True)

        # 4. Screenshots
        scr = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Screenshots"] = scr
        self._scroll_screenshots = ctk.CTkScrollableFrame(scr, fg_color="transparent")
        self._scroll_screenshots.pack(fill="both", expand=True)

        # 5. Blurry Photos
        blur = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Blurry Photos"] = blur
        self._scroll_blurry = ctk.CTkScrollableFrame(blur, fg_color="transparent")
        self._scroll_blurry.pack(fill="both", expand=True)

        # 6. Large Files
        large = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.frames["Large Files"] = large
        self._scroll_large = ctk.CTkScrollableFrame(large, fg_color="transparent")
        self._scroll_large.pack(fill="both", expand=True)

        # 7. Settings
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
        ctk.CTkLabel(parent, text="Dashboard", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", pady=(10, 20))

        # Folder Selection
        folder_panel = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        folder_panel.pack(fill="x", pady=(0, 20))
        
        ctk.CTkLabel(folder_panel, text="Scan Folder", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(12, 4))
        path_row = ctk.CTkFrame(folder_panel, fg_color="transparent")
        path_row.pack(fill="x", padx=16, pady=(0, 16))
        
        self._folder_entry = ctk.CTkEntry(path_row, placeholder_text="No folder selected…", height=34, font=ctk.CTkFont(size=12), state="readonly")
        self._folder_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(path_row, text="📁  Browse", width=108, height=34, command=self._select_folder).pack(side="left", padx=(8, 0))

        # Controls & Progress
        controls = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        controls.pack(fill="x", pady=(0, 20))
        
        btn_row = ctk.CTkFrame(controls, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=16)
        
        self._scan_btn = ctk.CTkButton(btn_row, text="🔍  Scan Photos", width=148, height=44, font=ctk.CTkFont(size=14, weight="bold"), command=self._start_scan, state="disabled")
        self._scan_btn.pack(side="left")
        
        self._cancel_btn = ctk.CTkButton(btn_row, text="✕  Cancel", width=148, height=34, font=ctk.CTkFont(size=12), fg_color="#7F1D1D", hover_color="#991B1B", command=self._cancel_scan, state="disabled")
        self._cancel_btn.pack(side="left", padx=10)
        
        self._refresh_btn = ctk.CTkButton(btn_row, text="↺  Refresh Scan", height=34, width=140, font=ctk.CTkFont(size=12), fg_color="#334155", hover_color="#475569", command=self._start_scan, state="disabled")
        self._refresh_btn.pack(side="left")

        # Progress
        prog_frame = ctk.CTkFrame(controls, fg_color="transparent")
        prog_frame.pack(fill="x", padx=16, pady=(0, 16))
        
        self._status_label = ctk.CTkLabel(prog_frame, text="Select a folder to begin.", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED)
        self._status_label.pack(anchor="w")
        
        self._eta_label = ctk.CTkLabel(prog_frame, text="", font=ctk.CTkFont(size=10), text_color=TEXT_DIM)
        self._eta_label.pack(anchor="w")
        
        self._progress_bar = ctk.CTkProgressBar(prog_frame, height=8, mode="determinate")
        self._progress_bar.pack(fill="x", pady=(8, 0))
        self._progress_bar.set(0)

        # Dashboard Stats
        ctk.CTkLabel(parent, text="Scan Overview", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(10, 10))
        stats_frame = ctk.CTkFrame(parent, fg_color="transparent")
        stats_frame.pack(fill="x")
        
        self._stat_found    = self._stat_card(stats_frame, "Images Scanned", "—")
        self._stat_prefilter= self._stat_card(stats_frame, "Pre-filter Saved", "—")
        self._stat_dupes    = self._stat_card(stats_frame, "Duplicate Groups", "—")
        self._stat_waste    = self._stat_card(stats_frame, "Reclaimable Space", "—")
        self._stat_selected = self._stat_card(stats_frame, "Selected items", "0")


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


    def _stat_card"""
    
    content = replace_pattern(content, layout_pattern, new_layout, "_build_layout replacement")

    # 3. Update Result Rendering & Clear routines
    old_render = re.compile(r"    def _render_results\(self.*?    def _clear_results", re.DOTALL)
    
    new_render_code = """    def _render_results(self, detection: DetectionResult):
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

        if detection.blurry_photos:
            card = FileGridCard(self._scroll_blurry, "Blurry Photos", detection.blurry_photos, self._thumb_cache, self._update_selected_count)
            card.pack(fill="x", padx=8, pady=(0, 10))
            self._blurry_cards.append(card)

        if detection.large_files:
            card = FileGridCard(self._scroll_large, "Large Files (>2MB)", detection.large_files, self._thumb_cache, self._update_selected_count)
            card.pack(fill="x", padx=8, pady=(0, 10))
            self._large_cards.append(card)

        self._select_all_btn.configure(state="normal")
        self._refresh_btn.configure(state="normal")
        self._quick_clean_btn.configure(state="normal")

        elapsed = time.perf_counter() - self._scan_start_time
        self._set_status(f"Scan complete in {elapsed:.1f}s — {detection.duplicate_group_count} group(s) · {waste_mb:.1f} MB reclaimable", SUCCESS)

    def _clear_results"""
    content = replace_pattern(content, old_render, new_render_code, "_render_results replacement")

    old_clear = re.compile(r"    def _clear_results\(self.*?    def _show_empty_state", re.DOTALL)
    new_clear_code = """    def _clear_results(self):
        if hasattr(self, "_scroll_exact"):
            for scroll in [self._scroll_exact, self._scroll_similar, self._scroll_screenshots, self._scroll_blurry, self._scroll_large]:
                for w in scroll.winfo_children():
                    w.destroy()
        self._exact_group_cards.clear()
        self._similar_group_cards.clear()
        self._screenshot_cards.clear()
        self._blurry_cards.clear()
        self._large_cards.clear()
        self._stat_found.configure(text="—")
        self._stat_prefilter.configure(text="—")
        self._stat_dupes.configure(text="—")
        self._stat_waste.configure(text="—")
        self._stat_selected.configure(text="0")
        if hasattr(self, "_delete_btn"):
            self._delete_btn.configure(state="disabled")
            self._select_all_btn.configure(state="disabled")
            self._quick_clean_btn.configure(state="disabled")

    def _show_empty_state"""
    content = replace_pattern(content, old_clear, new_clear_code, "_clear_results replacement")

    # 4. Update the _update_selected_count and delete logic
    content = content.replace(
        "self._group_cards + self._screenshot_cards",
        "self._exact_group_cards + self._similar_group_cards + self._screenshot_cards"
    )
    content = content.replace(
        "for card in self._group_cards:",
        "for card in self._exact_group_cards + self._similar_group_cards:"
    )

    # 5. Fixing the toggle theme logic which references self._theme_btn text
    old_theme_toggle = """    def _toggle_theme(self):
        if ctk.get_appearance_mode() == "Dark":
            ctk.set_appearance_mode("light")
            self._theme_btn.configure(text="🌙 Dark")
        else:
            ctk.set_appearance_mode("dark")
            self._theme_btn.configure(text="☀ Light")"""
    
    new_theme_toggle = """    def _toggle_theme(self):
        if ctk.get_appearance_mode() == "Dark":
            ctk.set_appearance_mode("light")
            self._theme_btn.configure(text="🌙 Dark Mode")
        else:
            ctk.set_appearance_mode("dark")
            self._theme_btn.configure(text="☀ Light Mode")"""
    content = replace_block(content, old_theme_toggle, new_theme_toggle, "theme toggle button text update")

    with ui_path.open("w", encoding="utf-8") as f:
        f.write(content)

    print(f"Refactor complete. Updated {ui_path}")

if __name__ == "__main__":
    main()
