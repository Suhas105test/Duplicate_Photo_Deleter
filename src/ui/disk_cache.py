import os
import hashlib
from PIL import Image
import logging

log = logging.getLogger(__name__)

class DiskCache:
    """Persistent disk-based cache for thumbnails."""
    
    def __init__(self, cache_dir: str = ".cache/thumbnails"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    def _get_cache_path(self, path: str) -> str:
        """Generate a unique deterministic filename for a path + mtime."""
        try:
            mtime = os.path.getmtime(path)
            # Hash path + mtime to handle file updates
            key = f"{path}_{mtime}".encode('utf-8')
            h = hashlib.sha256(key).hexdigest()
            return os.path.join(self.cache_dir, f"{h}.webp")
        except OSError:
            return ""

    def get(self, path: str) -> str | None:
        """Returns path to cached thumbnail if it exists."""
        cache_path = self._get_cache_path(path)
        if cache_path and os.path.exists(cache_path):
            return cache_path
        return None

    def save(self, original_path: str, pil_img: Image.Image):
        """Saves a PIL image as a cached thumbnail."""
        cache_path = self._get_cache_path(original_path)
        if not cache_path:
            return
        
        try:
            # We use WebP with quality 80 for good size/quality balance
            pil_img.save(cache_path, "WEBP", quality=80)
        except Exception as e:
            log.warning("Failed to save to disk cache: %s", e)

    def clear(self):
        """Wipes the cache directory."""
        import shutil
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)
            os.makedirs(self.cache_dir, exist_ok=True)
