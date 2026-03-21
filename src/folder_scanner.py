"""
folder_scanner.py
-----------------
Stage 1 of the scanning pipeline.

Improvements over v1:
  • ImageFile uses __slots__ — saves ~50 bytes per object (critical at 20k+ images)
  • ScanResult.group_by_size() — O(n) bucketing used by the MD5 pre-filter stage
  • size_candidate_groups / size_singleton_paths — expose pre-filter split
  • Symlink-safe: followlinks=False prevents infinite loops on circular symlinks
  • Progress callback now includes total_scanned so the UI can show raw file count

v3 fixes:
  • group_by_size() result is now cached to avoid triple-recomputation
  • Logging replaces silent error accumulation
"""

import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Generator

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".arw", ".cr2", ".nef", ".dng", ".tif", ".tiff", ".raw"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"})
TEXT_EXTENSIONS = frozenset({".txt", ".csv", ".rtf"})
DOC_EXTENSIONS = frozenset({".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"})
DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS | DOC_EXTENSIONS
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | DOCUMENT_EXTENSIONS


# ─── Data Model ───────────────────────────────────────────────────────────────

class ScanFile:
    """
    Lightweight metadata record for a discovered file.

    __slots__ drops per-instance overhead from ~160 bytes (plain object) to
    ~112 bytes — a saving of ~1 MB per 20 000 items before any hashing begins.
    """
    __slots__ = ("path", "size_bytes", "extension", "filename", "is_video", "is_document", "is_screenshot", "is_whatsapp", "is_telegram", "timestamp")

    def __init__(self, path: str, size_bytes: int, extension: str, filename: str, timestamp: float = 0.0):
        self.path: str = path
        self.size_bytes: int = size_bytes
        self.extension: str = extension
        self.filename: str = filename
        self.timestamp: float = timestamp
        self.is_video: bool = extension in VIDEO_EXTENSIONS
        self.is_document: bool = extension in DOCUMENT_EXTENSIONS
        
        lower_name = filename.lower()
        lower_path = path.lower()
        
        # Simple heuristic for screenshots
        self.is_screenshot: bool = not self.is_video and not self.is_document and (
            "screenshot" in lower_name or 
            "screen shot" in lower_name or 
            "snip" in lower_name
        )
        
        # WhatsApp/Telegram heuristics
        # WhatsApp: IMG-20230101-WA0001 or folder name
        self.is_whatsapp: bool = (
            "whatsapp" in lower_path or 
            "-wa" in lower_name or 
            "wa-" in lower_name
        )
        # Telegram: filename usually doesn't have a prefix, but often in Telegram folders
        self.is_telegram: bool = "telegram" in lower_path or "t.me" in lower_path

    def __repr__(self) -> str:  # pragma: no cover
        return f"ScanFile({self.filename!r}, {self.size_bytes} B)"


class ScanResult:
    """Aggregated output from a folder scan."""

    __slots__ = (
        "images", "total_files_scanned", "skipped_files",
        "total_size_bytes", "errors", "folder_sizes",
        "_size_groups_cache",  # cached group_by_size result
    )

    def __init__(self):
        self.images: list[ScanFile] = []
        self.total_files_scanned: int = 0
        self.skipped_files: int = 0
        self.total_size_bytes: int = 0
        self.errors: list[str] = []
        self.folder_sizes: dict[str, int] = defaultdict(int)
        self._size_groups_cache: dict[int, list[str]] | None = None

    # ── Convenience ────────────────────────────────────────────────────────

    @property
    def image_count(self) -> int:
        return len(self.images)

    @property
    def image_paths(self) -> list[str]:
        return [img.path for img in self.images]

    def total_size_mb(self) -> float:
        return self.total_size_bytes / (1024 * 1024)

    # ── Pre-filter helpers ──────────────────────────────────────────────────

    def _invalidate_cache(self):
        """Called whenever images list is modified."""
        self._size_groups_cache = None

    def group_by_size(self) -> dict[int, list[str]]:
        """
        Return {size_bytes: [path, …]} for every file discovered.

        Used by hash_generator.find_exact_byte_duplicates() to cheaply identify
        groups worth MD5-checking before the expensive perceptual-hash stage.
        O(n) time and space. Result is cached until images change.
        """
        if self._size_groups_cache is not None:
            return self._size_groups_cache

        buckets: defaultdict[int, list[str]] = defaultdict(list)
        for img in self.images:
            buckets[img.size_bytes].append(img.path)
        
        # Convert to plain dict and cache it
        res = dict(buckets)
        self._size_groups_cache = res
        return res

    @property
    def size_candidate_groups(self) -> dict[int, list[str]]:
        """
        Subset of group_by_size() containing only buckets with ≥ 2 files.

        These are the only paths worth computing an MD5 for — two files must share
        a file size before they can possibly be byte-identical copies.
        """
        return {sz: paths for sz, paths in self.group_by_size().items()
                if len(paths) >= 2}

    @property
    def size_singleton_paths(self) -> list[str]:
        """
        Paths whose file size is unique in the entire scan.

        No other file can be a byte-identical copy, so we skip MD5 for these.
        They still need perceptual hashing because they can be near-duplicates
        (e.g. resized, re-encoded, or lightly edited versions of another image).
        """
        return [paths[0]
                for paths in self.group_by_size().values()
                if len(paths) == 1]

    def prefilter_savings_pct(self, confirmed_exact_count: int) -> float:
        """Fraction of phash computations avoided by the MD5 pre-filter."""
        if self.image_count == 0:
            return 0.0
        return confirmed_exact_count / self.image_count * 100


# ─── Directory Walker ─────────────────────────────────────────────────────────

def _walk_directory_recursive(folder_path: str) -> Generator[os.DirEntry, None, None]:
    """
    Yield DirEntry objects for every file under folder_path recursively.
    Reimplemented with an explicit stack to avoid Python recursion limits on
    very deep directory trees (crash-proofing for huge scans).
    Uses os.scandir for high performance (metadata cached in entry).
    """
    stack = [folder_path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.startswith('.') or entry.name.startswith('@'):
                            continue
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        yield entry
        except PermissionError:
            log.warning("Permission denied: %s", current)


def _walk_directory_flat(folder_path: str) -> Generator[os.DirEntry, None, None]:
    """Yield DirEntry objects in the top-level directory only."""
    try:
        with os.scandir(folder_path) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False):
                    yield entry
    except PermissionError:
        log.warning("Permission denied scanning: %s", folder_path)


# ─── Public API ───────────────────────────────────────────────────────────────

def _process_entry(entry: os.DirEntry, result: ScanResult, progress_callback=None):
    result.total_files_scanned += 1
    ext = os.path.splitext(entry.name)[1].lower()

    if ext not in SUPPORTED_EXTENSIONS:
        result.skipped_files += 1
        return

    try:
        # entry.stat() is often pre-populated or cached by os.scandir
        stat = entry.stat()
        size = stat.st_size
        if size == 0:
            result.skipped_files += 1
            return
        mtime = stat.st_mtime

        result.images.append(ScanFile(
            path=entry.path,
            size_bytes=size,
            extension=ext,
            filename=entry.name,
            timestamp=mtime,
        ))
        result._invalidate_cache()
        result.total_size_bytes += size
        
        # Accumulate folder heatmap
        folder_dir = os.path.dirname(entry.path)
        result.folder_sizes[folder_dir] += size

        if progress_callback:
            progress_callback(
                result.image_count,
                entry.path,
                result.total_files_scanned,
            )

    except OSError as exc:
        log.warning("Cannot access %s: %s", entry.path, exc)
        result.errors.append(f"Cannot access {entry.path}: {exc}")
        result.skipped_files += 1

def scan_files(
    file_paths: list[str],
    progress_callback=None,
) -> ScanResult:
    """
    Scan a specific list of supported files.
    Note: Still needs os.path.getsize here as we don't have DirEntry.
    """
    result = ScanResult()
    for file_path in file_paths:
        result.total_files_scanned += 1
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            result.skipped_files += 1
            continue
        try:
            stat = os.stat(file_path)
            size = stat.st_size
            if size == 0:
                result.skipped_files += 1
                continue
            result.images.append(ScanFile(
                path=file_path,
                size_bytes=size,
                extension=ext,
                filename=os.path.basename(file_path),
                timestamp=stat.st_mtime,
            ))
            result._invalidate_cache()
            result.total_size_bytes += size
            result.folder_sizes[os.path.dirname(file_path)] += size
            if progress_callback:
                progress_callback(result.image_count, file_path, result.total_files_scanned)
        except OSError:
            result.skipped_files += 1

    log.info("scan_files complete: %d images from %d paths", result.image_count, len(file_paths))
    return result

def scan_folder(
    folder_path: str,
    progress_callback=None,
    recursive: bool = True,
    cancel_event=None,
) -> ScanResult:
    """
    Scan a directory using os.scandir for high performance.
    """
    result = ScanResult()

    if not os.path.isdir(folder_path):
        msg = f"Not a valid directory: {folder_path}"
        log.error(msg)
        result.errors.append(msg)
        return result

    walker = _walk_directory_recursive(folder_path) if recursive else _walk_directory_flat(folder_path)

    for entry in walker:
        if cancel_event and cancel_event.is_set():
            break
        _process_entry(entry, result, progress_callback)

    log.info(
        "scan_folder complete: %d images, %d skipped, %d errors, %.1f MB",
        result.image_count, result.skipped_files, len(result.errors), result.total_size_mb(),
    )
    return result
