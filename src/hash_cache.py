import json
import os
import logging

log = logging.getLogger(__name__)


class HashCache:
    """Persistent cache for perceptual hashes and metadata.

    Validates entries by both mtime AND file size — more robust on FAT32 /
    external drives where mtime resolution is 2 s (so a modified file may
    share the same mtime but will always differ in size if content changed).
    """

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

    def get(self, path: str, known_mtime: float = None, known_size: int = None) -> dict | None:
        """Returns cached data if path exists and (mtime, size) both match.
        
        If known_mtime and known_size are provided (from the folder scanner),
        skip the os.stat() call entirely — saves 10K+ syscalls on large scans.
        """
        if path not in self.data:
            return None

        cached = self.data[path]
        try:
            if known_mtime is not None and known_size is not None:
                # Fast path: use pre-computed stat data from folder scanner
                if cached.get("mtime") == known_mtime and cached.get("size") == known_size:
                    return cached
            else:
                stat = os.stat(path)
                if (
                    cached.get("mtime") == stat.st_mtime
                    and cached.get("size") == stat.st_size
                ):
                    return cached
        except OSError:
            pass
        return None

    def set(self, path: str, result: dict, known_mtime: float = None, known_size: int = None):
        try:
            if known_mtime is not None and known_size is not None:
                result["mtime"] = known_mtime
                result["size"] = known_size
            else:
                stat = os.stat(path)
                result["mtime"] = stat.st_mtime
                result["size"] = stat.st_size
            self.data[path] = result
        except OSError:
            pass

    def clear(self):
        self.data = {}
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)


class Md5Cache:
    """Persistent cache for MD5 hashes used in the pre-filter stage.

    Keyed by file path; validated by (mtime, size). This avoids re-reading
    every byte of unchanged files on repeat scans — the biggest time sink on
    the pre-filter stage for large libraries.
    """

    def __init__(self, cache_file: str = ".cache/md5.json"):
        self.cache_file = cache_file
        self.data: dict[str, dict] = {}  # path -> {md5, mtime, size}
        self._dirty = False
        self._load()

    def _load(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                log.warning("Failed to load MD5 cache: %s", e)
                self.data = {}

    def get(self, path: str, known_mtime: float = None, known_size: int = None) -> str | None:
        """Return cached MD5 hex string if file is unchanged, else None.
        
        If known_mtime and known_size are provided (from the folder scanner),
        skip the os.stat() call entirely.
        """
        entry = self.data.get(path)
        if entry is None:
            return None
        try:
            if known_mtime is not None and known_size is not None:
                if entry.get("mtime") == known_mtime and entry.get("size") == known_size:
                    return entry["md5"]
            else:
                stat = os.stat(path)
                if entry.get("mtime") == stat.st_mtime and entry.get("size") == stat.st_size:
                    return entry["md5"]
        except OSError:
            pass
        return None

    def set(self, path: str, md5: str, known_mtime: float = None, known_size: int = None):
        try:
            if known_mtime is not None and known_size is not None:
                self.data[path] = {"md5": md5, "mtime": known_mtime, "size": known_size}
            else:
                stat = os.stat(path)
                self.data[path] = {"md5": md5, "mtime": stat.st_mtime, "size": stat.st_size}
            self._dirty = True
        except OSError:
            pass

    def save(self):
        if not self._dirty:
            return
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            self._dirty = False
        except Exception as e:
            log.warning("Failed to save MD5 cache: %s", e)

    def clear(self):
        self.data = {}
        self._dirty = False
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)
