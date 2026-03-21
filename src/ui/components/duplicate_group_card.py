import os
import customtkinter as ctk
from typing import Optional, Set
from ..constants import (
    BG_CARD, BG_THUMB, TEXT_MUTED, TEXT_DIM, ACCENT, DANGER,
    NEAR_DUP, EXACT_BYTE, WARM_ORANGE
)
from ..thumbnail_cache import ThumbnailCache
from duplicate_detector import DuplicateGroup

class DuplicateGroupCard(ctk.CTkFrame):
    """
    Widget displaying one duplicate group: header, match badge, thumbnail row.
    Thumblazy-loaded via ThumbnailCache.
    """
    def __init__(
        self,
        parent,
        group: DuplicateGroup,
        thumb_cache: ThumbnailCache,
        on_selection_change,
        **kwargs,
    ):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=12, **kwargs)
        self.group = group
        self.thumb_cache = thumb_cache
        self.on_selection_change = on_selection_change
        self._checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self._img_labels: dict[str, ctk.CTkLabel] = {}
        self._res_labels: dict[str, ctk.CTkLabel] = {}
        self._thumb_cards: dict[str, ctk.CTkFrame] = {}
        self._thumb_row = 0
        self._thumb_col = 0
        self._build()

    def _build(self):
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent", height=40)
        header.pack(fill="x", padx=16, pady=(12, 8))
        
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", fill="both")
        
        ctk.CTkLabel(title_box, text=f"Group #{self.group.group_id + 1}", 
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        
        # Match badge
        badge = ctk.CTkLabel(
            title_box, text=self.group.match_label,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="white", fg_color=self._badge_style(),
            padx=8, corner_radius=6, height=20
        )
        badge.pack(side="left", padx=10)

        # Selection buttons
        btn_box = ctk.CTkFrame(header, fg_color="transparent")
        btn_box.pack(side="right", padx=10)
        
        ctk.CTkButton(btn_box, text="Auto", width=50, height=22, font=ctk.CTkFont(size=11),
                     fg_color="#334155", command=self._select_auto).pack(side="left", padx=2)
        ctk.CTkButton(btn_box, text="None", width=50, height=22, font=ctk.CTkFont(size=11),
                     fg_color="#334155", command=self._deselect_all).pack(side="left", padx=2)

        # Wasted space label
        wasted_mb = self.group.wasted_bytes() / (1024 * 1024)
        ctk.CTkLabel(header, text=f"Potentially Save: {wasted_mb:.1f} MB",
                     font=ctk.CTkFont(size=12), text_color=TEXT_MUTED).pack(side="right")

        # Thumbnails container with proper grid configuration
        self.thumbs_container = ctk.CTkFrame(self, fg_color="transparent")
        self.thumbs_container.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        
        # Configure 8 columns with equal weight
        for col in range(8):
            self.thumbs_container.grid_columnconfigure(col, weight=1, minsize=160)

        suggested_keep = self.group.suggested_keep
        for path in self.group.files:
            is_keeper = (path == suggested_keep)
            self._add_thumb_card(self.thumbs_container, path, is_keeper)

        # Trigger the selection count update after all checkboxes are built
        # (cb.select() doesn't fire the command automatically)
        self.after(0, self.on_selection_change)

    def _badge_style(self) -> str:
        if self.group.match_type == "exact_bytes": return EXACT_BYTE
        if self.group.match_type == "exact_hash": return ACCENT
        return NEAR_DUP

    def _add_thumb_card(self, parent, path: str, is_keeper: bool):
        card = ctk.CTkFrame(parent, fg_color=BG_THUMB, corner_radius=8, width=150, height=200)
        card.grid(row=self._thumb_row, column=self._thumb_col, padx=6, pady=6, sticky="ew")
        card.grid_propagate(False)
        self._thumb_cards[path] = card

        self._thumb_col += 1
        if self._thumb_col >= 8:
            self._thumb_col = 0
            self._thumb_row += 1

        # Image label (clickable to open viewer)
        img_label = ctk.CTkLabel(card, text="⌛", fg_color="transparent", cursor="hand2")
        img_label.pack(fill="both", expand=True, padx=4, pady=4)
        img_label.bind("<Button-1>", lambda e: self._open_viewer(path))
        self._img_labels[path] = img_label

        self._img_labels[path] = img_label

        # Removed immediate async load for lazy-loading support

        # Resolution label
        res = self.group.resolutions.get(path, (0, 0))
        res_text = f"{res[0]}x{res[1]}" if res[0] > 0 else "Checking resolution..."
        res_label = ctk.CTkLabel(card, text=res_text, font=ctk.CTkFont(size=10), text_color=TEXT_DIM)
        res_label.pack(pady=(0, 2))
        self._res_labels[path] = res_label

        # Bottom row: Checkbox + filename
        bot = ctk.CTkFrame(card, fg_color="transparent")
        bot.pack(fill="x", side="bottom", padx=8, pady=6)

        cb = ctk.CTkCheckBox(
            bot, text="", width=20, height=20, corner_radius=4,
            command=self.on_selection_change
        )
        cb.pack(side="left")
        self._checkboxes[path] = cb
        
        # Filename
        fname = os.path.basename(path)
        if len(fname) > 12: fname = fname[:10] + "..."
        ctk.CTkLabel(bot, text=fname, font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(side="left", padx=4)

        if is_keeper:
            keeper_label = ctk.CTkLabel(card, text="⭐ BEST", font=ctk.CTkFont(size=9, weight="bold"),
                                        text_color=WARM_ORANGE, fg_color="transparent")
            keeper_label.place(relx=0.95, rely=0.05, anchor="ne")
        else:
            cb.select() # Default delete candidates

    def load_thumbnails(self):
        """Triggers thumbnail loading for all images in this card."""
        for path, label in self._img_labels.items():
            # If already loading or loaded, ThumbnailCache handles it
            res_label = self._res_labels.get(path)
            img = self.thumb_cache.get_or_schedule(path, self._on_thumb_ready)
            if img:
                self._update_label(label, img, None, res_label)

    def _on_thumb_ready(self, path: str, ctk_img: Optional[ctk.CTkImage], resolution: Optional[tuple[int, int]] = None):
        """Callback from ThumbnailCache on background thread."""
        if path in self._img_labels:
            label = self._img_labels[path]
            res_label = self._res_labels.get(path)
            # Ensure UI update happens on the main thread
            self.after(0, lambda: self._update_label(label, ctk_img, resolution, res_label))

    def _update_label(self, label: ctk.CTkLabel, ctk_img: Optional[ctk.CTkImage], resolution: Optional[tuple[int, int]] = None, res_label: Optional[ctk.CTkLabel] = None):
        if ctk_img:
            label.configure(image=ctk_img, text="")
            if resolution and res_label:
                res_label.configure(text=f"{resolution[0]}x{resolution[1]}")
        else:
            label.configure(text="❌")
            if res_label:
                res_label.configure(text="Error loading")

    def _select_all(self):
        for cb in self._checkboxes.values():
            cb.select()
        self.on_selection_change()

    def _select_auto(self):
        keep = self.group.suggested_keep
        for path, cb in self._checkboxes.items():
            if path == keep:
                cb.deselect()
            else:
                cb.select()
        self.on_selection_change()

    def _deselect_all(self):
        for cb in self._checkboxes.values():
            cb.deselect()
        self.on_selection_change()

    # ── Public selection API (called by app.py) ──────────────────────────────

    def select_all(self):
        """Select every file in this group."""
        self._select_all()

    def deselect_all(self):
        """Deselect every file in this group."""
        self._deselect_all()

    def select_all_except_keep(self):
        """Select all files except the suggested 'best' keeper — used by Quick Clean."""
        keep = self.group.suggested_keep
        for path, cb in self._checkboxes.items():
            if path == keep:
                cb.deselect()
            else:
                cb.select()
        self.on_selection_change()

    def _open_viewer(self, starting_path: str):
        try:
            from ..image_viewer import ImageViewer
            app = self.winfo_toplevel()
            # Use the app's _delete_single_file if available, else a no-op
            delete_cb = getattr(app, "_delete_single_file", lambda p: False)
            viewer = ImageViewer(
                app,
                self.group.files,
                self.group.files.index(starting_path),
                delete_cb,
                on_toggle_selection=getattr(app, "_toggle_path_selection", None),
                is_selected_callback=getattr(app, "_is_path_selected", None)
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("ImageViewer failed: %s", e)

    def get_selected_paths(self) -> list[str]:
        return [p for p, cb in self._checkboxes.items() if cb.get()]

    def remove_paths(self, deleted: Set[str]) -> bool:
        """
        Removes widgets for deleted paths.
        Returns True if the entire card should be destroyed (e.g. < 2 files left).
        """
        for path in list(deleted):
            if path in self.group.files:
                self.group.files.remove(path)
                if path in self._thumb_cards:
                    self._thumb_cards[path].destroy()
                    del self._thumb_cards[path]
                if path in self._checkboxes:
                    del self._checkboxes[path]
                if path in self._img_labels:
                    del self._img_labels[path]

        # If only 1 or 0 files remain, this duplicate group is no longer valid
        if len(self.group.files) < 2:
            return True

        # Re-build thumbnails grid
        self.thumbs_container.destroy()
        self.thumbs_container = ctk.CTkFrame(self, fg_color="transparent")
        self.thumbs_container.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        
        # Configure 8 columns with equal weight
        for col in range(8):
            self.thumbs_container.grid_columnconfigure(col, weight=1, minsize=160)
        
        self._thumb_row = 0
        self._thumb_col = 0
        suggested_keep = self.group.suggested_keep
        for path in self.group.files:
            is_keeper = (path == suggested_keep)
            self._add_thumb_card(self.thumbs_container, path, is_keeper)

        return False
