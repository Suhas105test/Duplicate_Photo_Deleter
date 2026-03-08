import os
import customtkinter as ctk
from typing import Optional, Set
from ..constants import BG_CARD, BG_THUMB, TEXT_MUTED, TEXT_DIM, ACCENT
from ..thumbnail_cache import ThumbnailCache

class FileGridCard(ctk.CTkFrame):
    """Widget displaying a grid of single file thumbnails."""
    def __init__(self, parent, title: str, paths: list[str], thumb_cache: ThumbnailCache, on_selection_change, **kwargs):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=12, **kwargs)
        self.paths = paths
        self.thumb_cache = thumb_cache
        self.on_selection_change = on_selection_change
        self._checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self._images_labels: dict[str, ctk.CTkLabel] = {}
        self._build(title)

    def _build(self, title: str):
        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.pack(fill="x", padx=16, pady=(12, 8))
        
        title_label = ctk.CTkLabel(self.header, text=title, font=ctk.CTkFont(size=14, weight="bold"))
        title_label.pack(side="left")
        
        # Select All and Delete All buttons
        btn_box = ctk.CTkFrame(self.header, fg_color="transparent")
        btn_box.pack(side="right")
        ctk.CTkButton(btn_box, text="Select All", width=80, height=22, font=ctk.CTkFont(size=10), command=self._select_all).pack(side="left", padx=2)
        ctk.CTkButton(btn_box, text="Deselect All", width=80, height=22, font=ctk.CTkFont(size=10), fg_color="transparent", border_width=1, border_color=ACCENT, command=self._deselect_all).pack(side="left", padx=2)
        
        # Grid container
        self.grid_container = ctk.CTkFrame(self, fg_color="transparent")
        self.grid_container.pack(fill="x", padx=16, pady=(0, 16))
        
        # Create all thumb widgets upfront
        self._thumb_widgets = {}
        for path in self.paths:
            self._thumb_widgets[path] = self._add_thumb(path)

        self.after(100, self._re_layout)
        self.bind("<Configure>", lambda e: self._re_layout())
        self._loaded_paths = set()

    def add_paths(self, new_paths: list[str]):
        """Adds more paths to this grid for lazy loading."""
        for path in new_paths:
            if path not in self.paths:
                self.paths.append(path)
                self._thumb_widgets[path] = self._add_thumb(path)
        self._re_layout()

    def _add_thumb(self, path: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.grid_container, fg_color=BG_THUMB, corner_radius=8, width=150, height=180)
        card.pack_propagate(False)

        # Image label — clickable to open fullscreen viewer
        img_label = ctk.CTkLabel(card, text="⌛", fg_color="transparent", cursor="hand2")
        img_label.pack(fill="both", expand=True, padx=4, pady=4)
        img_label.bind("<Button-1>", lambda e, p=path: self._open_viewer(p))
        self._images_labels[path] = img_label

        # Bottom row: Checkbox + filename
        bot = ctk.CTkFrame(card, fg_color="transparent")
        bot.pack(fill="x", side="bottom", padx=6, pady=4)

        cb = ctk.CTkCheckBox(bot, text="", width=20, height=20, corner_radius=4, command=self.on_selection_change)
        cb.pack(side="left")
        self._checkboxes[path] = cb

        fname = os.path.basename(path)
        if len(fname) > 12: fname = fname[:10] + "..."
        return card

    def load_thumbnails(self):
        """Triggers thumbnail loading for images in this card that haven't been loaded yet."""
        for path, label in self._images_labels.items():
            if path in self._loaded_paths: continue
            img = self.thumb_cache.get_or_schedule(path, self._on_thumb_ready)
            if img:
                self._update_label(label, img)
                self._loaded_paths.add(path)

    def _open_viewer(self, starting_path: str):
        try:
            from ..image_viewer import ImageViewer
            app = self.winfo_toplevel()
            delete_cb = getattr(app, "_delete_single_file", lambda p: False)
            all_paths = list(self.paths)
            idx = all_paths.index(starting_path) if starting_path in all_paths else 0
            ImageViewer(app, all_paths, idx, 
                        on_delete_request=delete_cb,
                        on_toggle_selection=getattr(app, "_toggle_path_selection", None),
                        is_selected_callback=getattr(app, "_is_path_selected", None))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("ImageViewer failed to open: %s", e)

    def _on_thumb_ready(self, path: str, ctk_img: Optional[ctk.CTkImage]):
        if path in self._images_labels:
            label = self._images_labels[path]
            self._loaded_paths.add(path)
            self.after(0, lambda: self._update_label(label, ctk_img))

    def _update_label(self, label: ctk.CTkLabel, ctk_img: Optional[ctk.CTkImage]):
        if ctk_img:
            label.configure(image=ctk_img, text="")
        else:
            label.configure(text="❌")

    def _re_layout(self, event=None):
        width = self.winfo_width()
        if width < 100: return
        
        # Hide all first to avoid ghosting on resize
        for w in self._thumb_widgets.values():
            w.grid_forget()

        cols = max(1, min((width - 40) // 160, 8))
        for i, path in enumerate(self.paths):
            if path in self._thumb_widgets:
                row = i // cols
                col = i % cols
                self._thumb_widgets[path].grid(row=row, column=col, padx=4, pady=4, sticky="ew")

    def get_selected_paths(self) -> list[str]:
        return [p for p, cb in self._checkboxes.items() if cb.get()]

    def _select_all(self):
        for cb in self._checkboxes.values():
            cb.select()
        self.on_selection_change()

    def _deselect_all(self):
        for cb in self._checkboxes.values():
            cb.deselect()
        self.on_selection_change()

    def remove_paths(self, deleted: Set[str]) -> bool:
        for path in list(deleted):
            if path in self.paths:
                self.paths.remove(path)
                if path in self._thumb_widgets:
                    self._thumb_widgets[path].destroy()
                    del self._thumb_widgets[path]
                if path in self._checkboxes:
                    del self._checkboxes[path]
                if path in self._images_labels:
                    del self._images_labels[path]
        
        # Force re-layout after deletion
        self.after(0, self._re_layout)
        return len(self.paths) == 0
