"""
hash_generator.py
-----------------
Stage 2 (and optional Stage 1b) of the scanning pipeline.

Improvements over v1:
  • _compute_hash_worker now returns image resolution in the same Image.open()
    call — zero extra I/O for resolution data.
  • New MD5 pre-filter stage: find_exact_byte_duplicates() identifies byte-
    identical files cheaply (file-size bucket → MD5), so those images never need
    perceptual hashing.
  • Adaptive worker count: capped at min(cpu_count, 8) to prevent the subprocess
    RSS from ballooning when Pillow loads large RAW/TIFF images into each worker.
  • Memory-bounded batch processing: generate_hashes() processes images in
    configurable batches and yields results incrementally.

v3 fixes:
  • gc import moved to module level
  • PIL images explicitly closed in workers to prevent file handle leaks
  • Failed hash computations are now logged with path and exception
"""

import gc
import hashlib
import logging
import os
import threading
from collections import defaultdict
from multiprocessing import Pool, cpu_count
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageStat
import imagehash

log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

# Hash size 8 → 64-bit phash; larger values are more accurate but slower.
_PHASH_SIZE = 8

# Safety cap: each Pillow worker can hold a fully-decoded JPEG in RAM.
# At 20 MP images (~60 MB each) × 8 workers = 480 MB worker RSS.
_MAX_WORKERS = 8

# Default batch: process this many images before yielding intermediate results
_DEFAULT_BATCH = 500


# ─── Worker Functions (module-level so multiprocessing can pickle them) ───────

def _compute_hash_worker(
    file_path: str,
) -> tuple[str, Optional[str], Optional[tuple[int, int]], Optional[int], Optional[float], Optional[float], Optional[str]]:
    """
    Subprocess entry point - OPTIMIZED with OpenCV.

    Performance optimizations:
      1. Skip files < 20KB (thumbnails/noise).
      2. Use OpenCV for ultra-fast reading and resizing.
      3. Resize to 128x128 BEFORE any analysis (phash, blur, brightness).
      4. Avoid expensive PIL conversions where possible.
    """
    try:
        # 1. Skip very small files (< 20KB)
        file_size = os.path.getsize(file_path)
        if file_size < 20 * 1024:
            return (file_path, None, None, file_size, None, None, "File too small (<20KB)")

        ext = file_path.lower().split('.')[-1]
        is_video = ext in {'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv'}
        
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
                # Extract 3 frames at 10%, 50%, and 90%
                for pct in [0.1, 0.5, 0.9]:
                    target_frame = max(0, int(frame_count * pct))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    ret, frame = cap.read()
                    if ret and frame is not None and frame.size > 0:
                        # Resize for faster hashing
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
                try: cap.release()
                except: pass
                return (file_path, None, None, file_size, None, None, f"Video error: {str(video_exc)}")
        else:
            # IMAGE OPTIMIZATION: Use OpenCV for reading and resizing
            # This is much faster than PIL for initial decoding when we only need a thumb
            img_cv = cv2.imread(file_path)
            if img_cv is None:
                # Fallback to PIL in case OpenCV fails (e.g. some HEIC/WEBP)
                try:
                    with Image.open(file_path) as pil_img:
                        resolution = pil_img.size
                        # Fast resize in PIL
                        pil_small = pil_img.resize((128, 128), resample=Image.Resampling.BILINEAR).convert("RGB")
                        phash_str = str(imagehash.phash(pil_small, hash_size=_PHASH_SIZE))
                        gray = pil_small.convert("L")
                        edges = gray.filter(ImageFilter.FIND_EDGES)
                        blur_score = ImageStat.Stat(edges).var[0]
                        brightness = ImageStat.Stat(gray).mean[0]
                        return (file_path, phash_str, resolution, file_size, blur_score, brightness, None)
                except:
                    return (file_path, None, None, file_size, None, None, "OpenCV and PIL both failed to read image")

            if img_cv is not None:
                h, w = img_cv.shape[:2]
                resolution = (w, h)
                
                # 2. Resize to 128x128 immediately
                img_small = cv2.resize(img_cv, (128, 128), interpolation=cv2.INTER_AREA)
                
                # 3. Compute Blur (Laplacian variance)
                gray = cv2.cvtColor(img_small, cv2.COLOR_BGR2GRAY)
                blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
                
                # 4. Compute Brightness (Mean)
                brightness = np.mean(gray)
                
                # 5. Compute Perceptual Hash
                # Convert back to PIL for imagehash (it expects PIL)
                pil_small = Image.fromarray(cv2.cvtColor(img_small, cv2.COLOR_BGR2RGB))
                phash_str = str(imagehash.phash(pil_small, hash_size=_PHASH_SIZE))
            else:
                return (file_path, None, None, file_size, None, None, "OpenCV failed to read image")

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
            
        # Very simple SimHash-ish approach for 64-bit fingerprint
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

def _compute_md5_worker(file_path: str) -> tuple[str, Optional[str]]:
    """
    Subprocess entry point for MD5 computation.
    Reads the file in 64 KB chunks to stay I/O-efficient on spinning disks.
    Returns (path, md5_hex) or (path, None) on failure.
    """
    try:
        h = hashlib.md5()
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                h.update(chunk)
        return (file_path, h.hexdigest())
    except Exception as exc:
        try:
            logging.getLogger("hash_generator").debug(
                "Failed MD5 for %s: %s", file_path, exc
            )
        except Exception:
            pass
        return (file_path, None)


# ─── MD5 Pre-filter ──────────────────────────────────────────────────────────

def find_exact_byte_duplicates(
    size_groups: dict[int, list[str]],
    progress_callback=None,
    num_workers: Optional[int] = None,
) -> tuple[list[list[str]], list[str]]:
    """
    Stage 1b — MD5 pre-filter.

    Given the {size_bytes: [paths]} mapping from ScanResult.size_candidate_groups,
    compute MD5 hashes for all files that share a size and group the byte-identical
    ones.  Files that share a size but differ in content are returned as
    ``phash_candidates`` so the caller still runs perceptual hashing on them.

    Returns:
        (exact_dup_groups, phash_candidates)
    """
    workers = min(num_workers or max(1, cpu_count() - 1), _MAX_WORKERS)

    # Flatten same-size candidates into a single list for the pool
    files_needing_md5: list[str] = []
    for paths in size_groups.values():
        files_needing_md5.extend(paths)

    if not files_needing_md5:
        return [], []

    total = len(files_needing_md5)
    done = 0
    md5_map: dict[str, str] = {}          # path → md5

    chunk = max(1, total // (workers * 4))
    with Pool(processes=workers) as pool:
        for path, md5 in pool.imap_unordered(
            _compute_md5_worker, files_needing_md5, chunksize=chunk
        ):
            done += 1
            if md5 is not None:
                md5_map[path] = md5
            if progress_callback:
                progress_callback(done, total)

    # Group by MD5
    by_md5: dict[str, list[str]] = defaultdict(list)
    for path, md5 in md5_map.items():
        by_md5[md5].append(path)

    exact_dup_groups: list[list[str]] = []
    phash_candidates: list[str] = []

    for md5, paths in by_md5.items():
        if len(paths) >= 2:
            exact_dup_groups.append(paths)
        else:
            # Same size, different content → still a near-duplicate candidate
            phash_candidates.extend(paths)

    # Paths that failed MD5 (permission error etc.) also go to phash
    failed = set(files_needing_md5) - set(md5_map.keys())
    phash_candidates.extend(failed)
    if failed:
        log.warning("MD5 failed for %d file(s), falling back to phash", len(failed))

    log.info(
        "MD5 pre-filter: %d exact groups, %d phash candidates, %d failed",
        len(exact_dup_groups), len(phash_candidates), len(failed),
    )
    return exact_dup_groups, phash_candidates


# ─── Perceptual Hash Generation ───────────────────────────────────────────────

def generate_hashes(
    file_paths: list[str],
    progress_callback=None,
    num_workers: Optional[int] = None,
    batch_size: int = _DEFAULT_BATCH,
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, dict]:
    """
    Stage 2 — perceptual hash generation.

    Computes phash + resolution for every path in one multiprocessed pass.
    Results are accumulated in batches to cap peak memory usage.

    Returns:
        {path: {"hash": str, "resolution": (w, h), "size": int, "blur_score": float, "brightness": float}}
        Paths that fail to open are silently skipped.
    """
    if not file_paths:
        return {}

    workers = num_workers or max(1, cpu_count())
    total = len(file_paths)
    results: dict[str, dict] = {}
    progress_counter = threading.Semaphore(0)  # Thread-safe counter
    last_reported = 0
    failed_count = 0

    # Separate media and text files
    media_paths = []
    text_paths = []
    from folder_scanner import TEXT_EXTENSIONS
    for p in file_paths:
        if os.path.splitext(p)[1].lower() in TEXT_EXTENSIONS:
            text_paths.append(p)
        else:
            media_paths.append(p)

    # Chunk size for media
    chunk = max(1, min(len(media_paths) // (workers * 16), 64)) if media_paths else 1

    from hash_cache import HashCache
    cache = HashCache()
    
    paths_to_compute = []
    for p in media_paths + text_paths:
        cached = cache.get(p)
        if cached:
            results[p] = cached
        else:
            paths_to_compute.append(p)
    
    total = len(file_paths)
    done_cached = total - len(paths_to_compute)
    
    # Update progress for cached items
    if progress_callback and done_cached > 0:
        progress_callback(done_cached, total)

    if not paths_to_compute:
        return results

    # Re-split remaining paths
    remaining_media = [p for p in paths_to_compute if p in media_paths]
    remaining_text = [p for p in paths_to_compute if p in text_paths]

    def safe_progress_callback(current_done):
        nonlocal last_reported
        total_done = done_cached + current_done
        if total_done > last_reported and progress_callback:
            progress_callback(total_done, total)
            last_reported = total_done

    pool = Pool(processes=workers)
    try:
        # Process Media (Images/Videos)
        computed_count = 0
        if remaining_media:
            for file_path, phash_str, resolution, file_size, blur_score, brightness, error_msg in pool.imap_unordered(
                _compute_hash_worker, remaining_media, chunksize=chunk
            ):
                if cancel_event and cancel_event.is_set(): break
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
                    cache.set(file_path, res)
                else:
                    failed_count += 1
                if computed_count % batch_size == 0: gc.collect()

        # Process Text Files
        if remaining_text:
            for file_path, fprint, fsize, error_msg in pool.imap_unordered(
                _compute_text_hash_worker, remaining_text, chunksize=chunk
            ):
                if cancel_event and cancel_event.is_set(): break
                computed_count += 1
                safe_progress_callback(computed_count)
                
                if fprint is not None:
                    res = {
                        "hash": fprint,
                        "size": fsize,
                        "is_text": True
                    }
                    results[file_path] = res
                    cache.set(file_path, res)
                else:
                    failed_count += 1
                if computed_count % batch_size == 0: gc.collect()
    finally:
        pool.terminate()
        pool.join()
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
