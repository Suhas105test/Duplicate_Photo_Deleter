import logging
import os
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable
import customtkinter as ctk
from PIL import Image
import cv2
import numpy as np
from .constants import THUMBNAIL_SIZE, THUMBNAIL_SIZE_CTK
from .disk_cache import DiskCache

log = logging.getLogger(__name__)

class ThumbnailCache:
    """LRU thumbnail cache with persistent disk-based caching."""
    
    def __init__(self, max_size: int = 400, num_threads: int = 8):
        self.max_size = max_size
        self._cache: OrderedDict[str, ctk.CTkImage] = OrderedDict()
        self._pending: dict[str, list[Callable]] = {}
        self._executor = ThreadPoolExecutor(max_workers=num_threads)
        self.disk_cache = DiskCache()

    def get_or_schedule(self, path: str, on_ready: Callable):
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]

        if path in self._pending:
            self._pending[path].append(on_ready)
            return None

        self._pending[path] = [on_ready]
        self._executor.submit(self._load_worker, path)
        return None

    def clear(self):
        self._cache.clear()
        self._pending.clear()

    def shutdown(self):
        self._executor.shutdown(wait=False)

    def _load_worker(self, path: str):
        try:
            # 1. Try Disk Cache
            cached_path = self.disk_cache.get(path)
            if cached_path:
                img_pil = Image.open(cached_path)
                # Ensure it's the right size in case constants changed
                img_pil.thumbnail(THUMBNAIL_SIZE)
                ctk_img = ctk.CTkImage(img_pil, size=THUMBNAIL_SIZE_CTK)
                self._store(path, ctk_img)
                return

            # 2. Generate if not cached
            ext = os.path.splitext(path)[1].lower()
            from folder_scanner import VIDEO_EXTENSIONS
            
            if ext in VIDEO_EXTENSIONS:
                # Video handling: extract frame with OpenCV
                cap = cv2.VideoCapture(path)
                if not cap.isOpened():
                    raise Exception("Could not open video file")
                
                # Try to grab frame at 1 second or first frame
                fps = cap.get(cv2.CAP_PROP_FPS)
                if fps > 0:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, int(fps))
                
                ret, frame = cap.read()
                cap.release()
                
                if not ret:
                    raise Exception("Could not read frame from video")
                
                # Convert BGR (OpenCV) to RGB (PIL)
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img_pil = Image.fromarray(frame_rgb)
            else:
                # Standard image handling
                img_pil = Image.open(path)

            img_pil.thumbnail(THUMBNAIL_SIZE)
            
            # Save to disk cache for next time
            self.disk_cache.save(path, img_pil)
            
            ctk_img = ctk.CTkImage(img_pil, size=THUMBNAIL_SIZE_CTK)
            self._store(path, ctk_img)
        except Exception as e:
            log.warning("Failed to load thumbnail for %s: %s", path, e)
            self._store(path, None)

    def _store(self, path: str, image: Optional[ctk.CTkImage]):
        if image:
            self._cache[path] = image
            if len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

        callbacks = self._pending.pop(path, [])
        for cb in callbacks:
            try:
                cb(path, image)
            except Exception:
                pass
