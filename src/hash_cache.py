import json
import os
import logging

log = logging.getLogger(__name__)

class HashCache:
    """Persistent cache for perceptual hashes and metadata."""
    
    def __init__(self, cache_file: str = ".cache/hashes.json"):
        self.cache_file = cache_file
        self.data: dict[str, dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                log.warning("Failed to load hash cache: %s", e)
                self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
        except Exception as e:
            log.warning("Failed to save hash cache: %s", e)

    def get(self, path: str) -> dict | None:
        """Returns cached data if path exists and mtime matches."""
        if path not in self.data:
            return None
        
        cached = self.data[path]
        try:
            current_mtime = os.path.getmtime(path)
            if cached.get("mtime") == current_mtime:
                return cached
        except OSError:
            pass
        return None

    def set(self, path: str, result: dict):
        try:
            result["mtime"] = os.path.getmtime(path)
            self.data[path] = result
        except OSError:
            pass

    def clear(self):
        self.data = {}
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)
