import customtkinter as ctk
from typing import Callable, Any, List

class VirtualizedScrollFrame(ctk.CTkScrollableFrame):
    """
    A scrollable frame that only renders widgets currently in view.
    
    This is a simplified virtualization that works well for items of 
    roughly equal height (like DuplicateGroupCard).
    """
    def __init__(self, *args, item_height: int = 250, **kwargs):
        super().__init__(*args, **kwargs)
        self.item_height = item_height
        self.all_data: List[Any] = []
        self.render_callback: Callable[[ctk.CTkFrame, Any], None] = None
        self._visible_widgets: List[ctk.CTkFrame] = []
        self._container = ctk.CTkFrame(self, fg_color="transparent")
        self._container.pack(fill="x", expand=True)
        
        # Bind scrolling to update visibility
        self._parent_canvas.bind("<Configure>", lambda e: self._update_view())
        self._parent_canvas.bind("<MouseWheel>", lambda e: self._update_view(), add="+")
        
    def set_data(self, data: List[Any], render_callback: Callable[[ctk.CTkFrame, Any], None]):
        """Sets the data source and the function to render each item."""
        self.all_data = data
        self.render_callback = render_callback
        self._rebuild_placeholder()
        self._update_view()

    def _rebuild_placeholder(self):
        """Creates a spacer to maintain the total height of the scroll area."""
        total_height = len(self.all_data) * self.item_height
        # In a real CTk virtual list, we'd use a canvas or a giant frame.
        # For simplicity in this UI, we'll just clear and redraw for now, 
        # or implement a sliding window if performance lags.
        for child in self._container.winfo_children():
            child.destroy()
        
    def _update_view(self):
        """Recalculates which items should be visible based on scroll position."""
        # Note: This is a placeholder for the full sliding-window logic.
        # For the first version, we'll render all to ensure functional parity,
        # then optimize the coordinate-based clipping in the next step.
        if not self.all_data or not self.render_callback:
            return
            
        # Full virtualization implementation would go here.
        # Currently, we'll render segments to prove the component structure.
        for item in self.all_data:
            frame = ctk.CTkFrame(self._container, fg_color="transparent")
            frame.pack(fill="x", pady=5)
            self.render_callback(frame, item)
