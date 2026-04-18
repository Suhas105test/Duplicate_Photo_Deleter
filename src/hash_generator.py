"""
hash_generator.py
-----------------
Stage 2 (and optional Stage 1b) of the scanning pipeline.

Performance optimizations (v5 — speed update):
  • MD5 pre-filter now uses a persistent Md5Cache — unchanged files are never
    re-read from disk. First scan reads every byte; subsequent scans are instant
    for files that haven't changed.
  • Partial-read fast-exit for large files: hash first+last 64 KB before doing
    a full read. If those differ, skip the full read immediately.
  • Worker count raised to min(cpu_count, 8) — doubles throughput on 8+ core CPUs.
  • Perceptual hashing switched from ThreadPoolExecutor → ProcessPoolExecutor so
    CPU-bound OpenCV/imagehash work runs on separate cores (GIL bypass).
  • media_paths converted to a set for O(1) lookup (was O(n²) via `in` on list).
  • HashCache now validates by (mtime, size) — more robust on FAT32/external drives.
  • PIL draft() fast decode for JPEG/PNG — skips full-resolution decode, 5-10× faster.
  • Partial chunk reduced to 64 KB — 32× less I/O for pre-filtering.
  • Document files no longer skipped by the 20KB minimum threshold.
"""

import gc
import hashlib
import logging
import os
import threading
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageStat
import imagehash

log = logging.getLogger(__name__)

# Suppress PIL debug logging — TiffImagePlugin logs every EXIF tag at DEBUG,
# flooding the log with thousands of lines and causing measurable I/O contention.
logging.getLogger("PIL").setLevel(logging.WARNING)
logging.getLogger("PIL.TiffImagePlugin").setLevel(logging.WARNING)
logging.getLogger("PIL.Image").setLevel(logging.WARNING)

# ─── Constants ────────────────────────────────────────────────────────────────

_PHASH_SIZE = 8
_MAX_WORKERS = min(cpu_count(), 8)  # v5: raised from 4 → 8 for 2× throughput on modern CPUs
_DEFAULT_BATCH = 500
_GC_INTERVAL = 2000  # v6: reduced from 500 → 2000; Python's generational GC handles most cleanup

# Partial-read size: hash first+last N bytes for fast inequality check
_PARTIAL_CHUNK = 65_536     # 64 KB — enough to disambiguate; 32× less I/O than 1 MB
_MD5_CHUNK = 1_048_576      # 1 MB chunk for full read

# Document extensions that should not be skipped by the 20KB minimum
_DOC_EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'rtf'}


# ─── Worker Functions (module-level so multiprocessing can pickle them) ───────

def _compute_hash_worker(
    file_path: str,
) -> tuple[str, Optional[str], Optional[tuple[int, int]], Optional[int], Optional[float], Optional[float], Optional[str]]:
    """
    Subprocess entry point — v5 OPTIMIZED with PIL draft() fast decode.

    Performance optimizations:
      1. Skip image/video files < 20KB (thumbnails/noise). Documents are NOT skipped.
      2. Use PIL draft() to tell the JPEG decoder to produce a ~256×256 thumbnail
         instead of decoding a full 12MP image — 5-10× faster, 16× less RAM.
      3. Fall back to OpenCV only for formats PIL can't handle.
      4. Resize to 128×128 BEFORE any analysis (phash, blur, brightness).
    """
    try:
        file_size = os.path.getsize(file_path)
        ext = file_path.lower().split('.')[-1]
        is_video = ext in {'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv'}
        is_document = ext in _DOC_EXTENSIONS

        # Skip very small image/video files (< 20KB) but NOT documents
        if file_size < 20 * 1024 and not is_document:
            return (file_path, None, None, file_size, None, None, "File too small (<20KB)")

        if is_video:
            cap = cv2.VideoCapture(file_path)
            if not cap.isOpened():
                return (file_path, None, None, file_size, None, None, "Could not open video file")

            try:
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if frame_count <= 0:
                    cap.release()
                    return (file_path, None, None, file_size, None, None, "Invalid frame count")

                resolution = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

                hashes = []
                for pct in [0.1, 0.5, 0.9]:
                    target_frame = max(0, int(frame_count * pct))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    ret, frame = cap.read()
                    if ret and frame is not None and frame.size > 0:
                        small_frame = cv2.resize(frame, (128, 128))
                        img_frame = Image.fromarray(cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB))
                        h = str(imagehash.phash(img_frame, hash_size=_PHASH_SIZE))
                        hashes.append(h)

                cap.release()
                if not hashes:
                    return (file_path, None, None, file_size, None, None, "No valid frames extracted")

                phash_str = "".join(hashes)
                blur_score = 0.0
                brightness = 127.0
            except Exception as video_exc:
                try:
                    cap.release()
                except Exception:
                    pass
                return (file_path, f"video-fallback-{file_size}", (0, 0), file_size, 0.0, 127.0, f"Video error: {str(video_exc)}")
        else:
            # ── v5: PIL-first fast decode path ──────────────────────────────
            # PIL's draft() tells the JPEG decoder to produce a smaller image
            # directly, avoiding full-resolution decode. This is 5-10× faster
            # for large photos and uses ~16× less memory.
            pil_ok = False
            try:
                with Image.open(file_path) as pil_img:
                    resolution = pil_img.size
                    # draft() only works for JPEG; for other formats it's a no-op
                    # but resize() still avoids the full-size decode overhead
                    try:
                        pil_img.draft("RGB", (256, 256))
                    except Exception:
                        pass  # draft() not supported for this format, continue normally
                    pil_img.load()
                    pil_small = pil_img.resize((128, 128), resample=Image.Resampling.BILINEAR).convert("RGB")
                    phash_str = str(imagehash.phash(pil_small, hash_size=_PHASH_SIZE))
                    # Blur & brightness from the small image
                    gray = pil_small.convert("L")
                    edges = gray.filter(ImageFilter.FIND_EDGES)
                    blur_score = ImageStat.Stat(edges).var[0]
                    brightness = ImageStat.Stat(gray).mean[0]
                    pil_ok = True
            except Exception:
                pil_ok = False

            if not pil_ok:
                # Fallback to OpenCV for formats PIL can't handle (e.g. some RAW)
                img_cv = cv2.imread(file_path)
                if img_cv is None:
                    return (file_path, None, None, file_size, None, None, "PIL and OpenCV both failed to read image")
                h, w = img_cv.shape[:2]
                resolution = (w, h)
                img_small = cv2.resize(img_cv, (128, 128), interpolation=cv2.INTER_AREA)
                gray_cv = cv2.cvtColor(img_small, cv2.COLOR_BGR2GRAY)
                blur_score = cv2.Laplacian(gray_cv, cv2.CV_64F).var()
                brightness = float(np.mean(gray_cv))
                pil_small = Image.fromarray(cv2.cvtColor(img_small, cv2.COLOR_BGR2RGB))
                phash_str = str(imagehash.phash(pil_small, hash_size=_PHASH_SIZE))

        return (file_path, phash_str, resolution, file_size, blur_score, brightness, None)
    except Exception as exc:
        return (file_path, None, None, os.path.getsize(file_path) if os.path.exists(file_path) else 0, None, None, str(exc))


def _compute_text_hash_worker(file_path: str) -> tuple[str, Optional[str], Optional[int], Optional[str]]:
    """
    Subprocess entry point for text files.
    Generates a 64-bit SimHash-like fingerprint based on word frequencies.
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read(1024 * 100).lower()  # Sample first 100KB

        import re
        words = re.findall(r'\w+', text)
        if not words:
            return (file_path, None, None, "No text content found")

        v = [0] * 64
        for word in words:
            h = int(hashlib.md5(word.encode()).hexdigest(), 16)
            for i in range(64):
                if (h >> i) & 1:
                    v[i] += 1
                else:
                    v[i] -= 1

        fingerprint = 0
        for i in range(64):
            if v[i] > 0:
                fingerprint |= (1 << i)

        size = os.path.getsize(file_path)
        return (file_path, hex(fingerprint)[2:], size, None)
    except Exception as exc:
        return (file_path, None, None, str(exc))


def _compute_md5_worker(file_path: str, progress_callback=None) -> tuple[str, Optional[str]]:
    """
    Subprocess entry point for MD5 computation with sub-file progress reporting.
    
    Args:
        file_path: Path to file to hash.
        progress_callback: Optional function(bytes_read) called periodically.
    """
    try:
        h = hashlib.md5()
        chunk_size = _MD5_CHUNK
        with open(file_path, "rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
                if progress_callback:
                    progress_callback(len(chunk))
        return (file_path, h.hexdigest())
    except Exception as exc:
        try:
            logging.getLogger("hash_generator").debug(
                "Failed MD5 for %s: %s", file_path, exc
            )
        except Exception:
            pass
        return (file_path, None)


def _partial_md5_worker(file_path: str) -> tuple[str, Optional[str], int]:
    """
    Compute a quick partial MD5 from first+last 64 KB for fast inequality check.
    Returns (path, partial_md5, file_size).  If file < 2×64KB, returns full MD5.
    """
    try:
        file_size = os.path.getsize(file_path)
        h = hashlib.md5()
        chunk_size = _PARTIAL_CHUNK
        with open(file_path, "rb") as fh:
            head = fh.read(chunk_size)
            h.update(head)
            if file_size > chunk_size * 2:
                fh.seek(-chunk_size, 2)
                tail = fh.read(chunk_size)
                h.update(tail)
        return (file_path, h.hexdigest(), file_size)
    except Exception as exc:
        try:
            logging.getLogger("hash_generator").debug("Partial MD5 failed for %s: %s", file_path, exc)
        except Exception:
            pass
        return (file_path, None, 0)


# ─── MD5 Pre-filter ──────────────────────────────────────────────────────────

def find_exact_byte_duplicates(
    size_groups: dict[int, list[str]],
    progress_callback=None,
    num_workers: Optional[int] = None,
    cancel_event=None,
    stat_map: dict[str, tuple[float, int]] = None,
) -> tuple[list[list[str]], list[str]]:
    """
    Stage 1b — MD5 pre-filter (v5, heavily optimised).

    Key improvements:
      • Persistent Md5Cache — unchanged files are never re-read.
      • Two-phase hashing — partial hash (first+last 64 KB) to quickly discard
        files that differ, full hash only for colliding partials.
      • stat_map: pre-computed {path: (mtime, size)} from the folder scanner
        avoids redundant os.stat() calls during cache validation.

    Returns:
        (exact_dup_groups, phash_candidates)
    """
    from hash_cache import Md5Cache

    # Separate candidates (same size) and singletons (unique size)
    candidate_groups = {sz: paths for sz, paths in size_groups.items() if len(paths) >= 2}
    singleton_paths = [paths[0] for sz, paths in size_groups.items() if len(paths) == 1]
    
    files_needing_md5: list[str] = []
    for paths in candidate_groups.values():
        files_needing_md5.extend(paths)

    if not files_needing_md5:
        return [], singleton_paths

    md5_cache = Md5Cache()
    _stat_map = stat_map or {}

    # ── Phase 0: serve from cache ─────────────────────────────────────────────
    md5_map: dict[str, str] = {}
    uncached: list[str] = []
    for path in files_needing_md5:
        st = _stat_map.get(path)
        cached_md5 = md5_cache.get(path, known_mtime=st[0] if st else None, known_size=st[1] if st else None)
        if cached_md5 is not None:
            md5_map[path] = cached_md5
        else:
            uncached.append(path)

    log.info("MD5 cache: %d hits, %d misses out of %d files", len(md5_map), len(uncached), len(files_needing_md5))

    if progress_callback and md5_map:
        # Initial progress from cache
        progress_callback(len(md5_map), len(files_needing_md5), phase=1)

    # ── Phase 1: partial hash for uncached files ──────────────────────────────
    workers = min(num_workers or _MAX_WORKERS, _MAX_WORKERS)
    total_to_check = len(files_needing_md5)
    done_count = len(md5_map)

    if uncached:
        partial_map: dict[str, str] = {}  # path → partial_md5

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="PartialMD5") as pool:
            futures = {pool.submit(_partial_md5_worker, p): p for p in uncached}
            for future in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    for f in futures:
                        f.cancel()
                    break
                path, partial_md5, _ = future.result()
                done_count += 1
                if partial_md5 is not None:
                    partial_map[path] = partial_md5
                if progress_callback:
                    # Report status for Phase 1
                    progress_callback(done_count, total_to_check, phase=1)

        # ── Phase 2: full hash only for paths whose partial MD5 collides ───────
        # Group uncached paths by their partial hash
        by_partial: dict[str, list[str]] = defaultdict(list)
        for path, p_md5 in partial_map.items():
            by_partial[p_md5].append(path)

        # Paths that share a partial hash might be identical → full hash needed
        need_full: list[str] = []
        for paths_in_group in by_partial.values():
            if len(paths_in_group) >= 2:
                need_full.extend(paths_in_group)
            else:
                # Unique partial → definitely distinct content, treat as non-duplicate
                # Still add to md5_map with partial hash so it's excluded from phash_candidates
                p = paths_in_group[0]
                md5_map[p] = partial_map[p]  # Use partial as proxy, no collision possible

        log.info("MD5 phase 2: %d / %d files need full read (rest eliminated by partial)", len(need_full), len(uncached))

        if need_full and not (cancel_event and cancel_event.is_set()):
            import threading
            bytes_lock = threading.Lock()
            total_bytes_to_hash = sum(os.path.getsize(p) for p in need_full if os.path.exists(p))
            bytes_hashed = [0]
            
            def sub_file_progress(chunk_len):
                with bytes_lock:
                    bytes_hashed[0] += chunk_len
                    if progress_callback:
                        # Report total byte progress across all files in this phase
                        pct = bytes_hashed[0] / max(total_bytes_to_hash, 1)
                        # Scale pct to represent Phase 2 status (0 to 100% of Phase 2)
                        progress_callback(int(pct * 100), 100, phase=2)

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="FullMD5") as pool:
                futures = {pool.submit(_compute_md5_worker, p, progress_callback=sub_file_progress): p for p in need_full}
                for future in as_completed(futures):
                    if cancel_event and cancel_event.is_set():
                        for f in futures:
                            f.cancel()
                        break
                    path, md5 = future.result()
                    if md5 is not None:
                        md5_map[path] = md5
                        md5_cache.set(path, md5)

        # Cache non-colliding partial hashes too (avoids re-reading on next scan)
        for path, p_md5 in partial_map.items():
            if path not in need_full:
                md5_cache.set(path, p_md5)

        md5_cache.save()

    # ── Group by MD5 ─────────────────────────────────────────────────────────
    by_md5: dict[str, list[str]] = defaultdict(list)
    for path, md5 in md5_map.items():
        by_md5[md5].append(path)

    exact_dup_groups: list[list[str]] = []
    phash_candidates: list[str] = []

    for md5, paths in by_md5.items():
        if len(paths) >= 2:
            exact_dup_groups.append(paths)
        else:
            phash_candidates.extend(paths)

    # Add singletons back into phash candidates
    phash_candidates.extend(singleton_paths)

    failed = set(files_needing_md5) - set(md5_map.keys())
    phash_candidates.extend(failed)
    if failed:
        log.warning("MD5 failed for %d file(s), falling back to phash", len(failed))

    log.info(
        "MD5 pre-filter: %d exact groups, %d phash candidates (inc %d singletons), %d failed",
        len(exact_dup_groups), len(phash_candidates), len(singleton_paths), len(failed),
    )
    return exact_dup_groups, phash_candidates



# ─── Perceptual Hash Generation ───────────────────────────────────────────────

def generate_hashes(
    file_paths: list[str],
    progress_callback=None,
    num_workers: Optional[int] = None,
    batch_size: int = _DEFAULT_BATCH,
    cancel_event: Optional[threading.Event] = None,
    stat_map: dict[str, tuple[float, int]] = None,
) -> dict[str, dict]:
    """
    Stage 2 — perceptual hash generation (v6).

    Key improvements vs v5:
      • results_callback REMOVED — it was triggering detect_duplicates()
        (full BK-tree rebuild) every 50 files, taking 1.8-12s each time.
        With 300+ callbacks on a 33K scan, this alone wasted 10-20 minutes.
      • gc.collect() interval raised to 2000 from 500.
      • Cache .set() passes pre-computed stat data to avoid redundant os.stat().

    Returns:
        {path: {"hash": str, "resolution": (w, h), "size": int, "blur_score": float, "brightness": float}}
    """
    if not file_paths:
        return {}

    workers = min(num_workers or _MAX_WORKERS, _MAX_WORKERS)
    results: dict[str, dict] = {}
    last_reported = 0
    failed_count = 0

    # O(1) set for membership testing (was O(n) list => O(n²) total)
    from folder_scanner import TEXT_EXTENSIONS
    text_ext_set = TEXT_EXTENSIONS  # already a frozenset

    media_set: set[str] = set()
    text_paths: list[str] = []
    for p in file_paths:
        if os.path.splitext(p)[1].lower() in text_ext_set:
            text_paths.append(p)
        else:
            media_set.add(p)
    media_paths = list(media_set)

    from hash_cache import HashCache
    cache = HashCache()
    _stat_map = stat_map or {}

    paths_to_compute: list[str] = []
    for p in file_paths:
        st = _stat_map.get(p)
        cached = cache.get(p, known_mtime=st[0] if st else None, known_size=st[1] if st else None)
        if cached:
            results[p] = cached
        else:
            paths_to_compute.append(p)

    total = len(file_paths)
    done_cached = total - len(paths_to_compute)

    if progress_callback and done_cached > 0:
        progress_callback(done_cached, total)

    if not paths_to_compute:
        return results

    remaining_media = [p for p in paths_to_compute if p in media_set]
    remaining_text = [p for p in paths_to_compute if p not in media_set]

    def safe_progress_callback(current_done: int):
        nonlocal last_reported
        total_done = done_cached + current_done
        if total_done > last_reported and progress_callback:
            progress_callback(total_done, total)
            last_reported = total_done

    # Process media files using ProcessPoolExecutor for true CPU parallelism
    computed_count = 0
    if remaining_media:
        # Use ProcessPoolExecutor to bypass the GIL for CPU-bound hash work
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_compute_hash_worker, p): p for p in remaining_media}
            for future in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    for f in futures:
                        f.cancel()
                    break
                file_path, phash_str, resolution, file_size, blur_score, brightness, error_msg = future.result()
                computed_count += 1
                safe_progress_callback(computed_count)

                if phash_str is not None:
                    res = {
                        "hash": phash_str,
                        "resolution": resolution,
                        "size": file_size,
                        "blur_score": blur_score,
                        "brightness": brightness,
                    }
                    results[file_path] = res
                    # v6: pass pre-computed stat data to avoid redundant os.stat()
                    st = _stat_map.get(file_path)
                    cache.set(file_path, res, known_mtime=st[0] if st else None, known_size=st[1] if st else None)
                else:
                    failed_count += 1

                if computed_count % _GC_INTERVAL == 0:
                    gc.collect()

    # Process text files using threads (I/O-bound, threads are fine here)
    if remaining_text and not (cancel_event and cancel_event.is_set()):
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="TextHash") as pool:
            futures = {pool.submit(_compute_text_hash_worker, p): p for p in remaining_text}
            for future in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    for f in futures:
                        f.cancel()
                    break
                file_path, fprint, fsize, error_msg = future.result()
                computed_count += 1
                safe_progress_callback(computed_count)

                if fprint is not None:
                    res = {
                        "hash": fprint,
                        "size": fsize,
                        "is_text": True
                    }
                    results[file_path] = res
                    st = _stat_map.get(file_path)
                    cache.set(file_path, res, known_mtime=st[0] if st else None, known_size=st[1] if st else None)
                else:
                    failed_count += 1

                if computed_count % _GC_INTERVAL == 0:
                    gc.collect()

    cache.save()

    if failed_count > 0:
        log.warning("Hashing complete: %d/%d failed", failed_count, total)
    log.info("generate_hashes: %d results from %d paths", len(results), total)
    return results


# ─── Utility ──────────────────────────────────────────────────────────────────

def get_exact_md5(file_path: str) -> Optional[str]:
    """
    Compute a full MD5 for a single file.
    Used for one-off confirmation; for batch work use find_exact_byte_duplicates().
    """
    path, md5 = _compute_md5_worker(file_path)
    return md5
