import os
import subprocess
import logging
import re
from typing import Optional, List, Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
import imageio_ffmpeg

log = logging.getLogger("compressor")

@dataclass
class CompressionResult:
    original_path: str
    compressed_path: Optional[str] = None
    original_size: int = 0
    compressed_size: int = 0
    success: bool = False
    error: Optional[str] = None

    @property
    def saved_bytes(self) -> int:
        return max(0, self.original_size - self.compressed_size) if self.success else 0

def _parse_time_to_seconds(time_str: str) -> float:
    try:
        parts = time_str.split(':')
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0

def _get_unique_path(base_path: str, suffix: str = "_compressed") -> str:
    path = Path(base_path)
    new_path = path.with_name(f"{path.stem}{suffix}{path.suffix}")
    counter = 1
    while new_path.exists():
        new_path = path.with_name(f"{path.stem}{suffix}_{counter}{path.suffix}")
        counter += 1
    return str(new_path)

def compress_image(file_path: str, quality: int = 85, in_place: bool = False) -> CompressionResult:
    """Compresses an image using Pillow optimizations."""
    try:
        orig_size = os.path.getsize(file_path)
        out_path = file_path if in_place else _get_unique_path(file_path)
        temp_path = _get_unique_path(file_path, "_temp_compress")
        
        with Image.open(file_path) as img:
            fmt = img.format or "JPEG"
            if img.mode in ("RGBA", "P") and fmt in ("JPEG", "JPG"):
                img = img.convert("RGB")
            
            # Additional logic to handle EXIF? We might lose Exif data by default in Pillow.
            # To keep EXIF:
            exif = img.info.get('exif', b'')
            
            img.save(temp_path, fmt, quality=quality, optimize=True, exif=exif)
            
        new_size = os.path.getsize(temp_path)
        
        if new_size >= orig_size:
            os.remove(temp_path)
            return CompressionResult(file_path, file_path, orig_size, orig_size, True, "Already optimal")
            
        if in_place:
            os.replace(temp_path, file_path)
            return CompressionResult(file_path, file_path, orig_size, new_size, True)
        else:
            os.replace(temp_path, out_path)
            return CompressionResult(file_path, out_path, orig_size, new_size, True)
            
    except Exception as e:
        log.error(f"Image compression failed for {file_path}: {e}")
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.remove(temp_path)
        return CompressionResult(file_path, None, 0, 0, False, str(e))

def compress_video(file_path: str, crf: int = 28, in_place: bool = False, progress_callback: Optional[Callable[[float], None]] = None) -> CompressionResult:
    """Compresses a video using FFmpeg with visually lossless CRF 28."""
    try:
        orig_size = os.path.getsize(file_path)
        # Enforce mp4 container to ensure compatibility when encoding
        temp_path = _get_unique_path(file_path, "_temp_compress")
        temp_path_mp4 = str(Path(temp_path).with_suffix(".mp4"))
        
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        
        cmd = [
            ffmpeg_exe,
            "-y",
            "-i", file_path,
            "-vcodec", "libx264",
            "-crf", str(crf),
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "128k",
            temp_path_mp4
        ]
        
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=creationflags, text=True, encoding='utf-8', errors='ignore')
        
        duration_secs = 0.0
        err_output = []
        
        while True:
            line = proc.stderr.readline()
            if not line and proc.poll() is not None:
                break
            if not line:
                continue
                
            err_output.append(line)
            
            # Extract duration once
            if duration_secs == 0.0:
                dur_match = re.search(r"Duration:\s*(\d{2}:\d{2}:\d{2}\.\d+)", line)
                if dur_match:
                    duration_secs = _parse_time_to_seconds(dur_match.group(1))
            
            # Extract current time progress
            if progress_callback and duration_secs > 0:
                time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d+)", line)
                if time_match:
                    current_secs = _parse_time_to_seconds(time_match.group(1))
                    progress = min(1.0, current_secs / duration_secs)
                    progress_callback(progress)
                    
        proc.wait()
        
        if proc.returncode != 0:
            err = "".join(err_output)
            if os.path.exists(temp_path_mp4):
                os.remove(temp_path_mp4)
            raise RuntimeError(f"FFmpeg failed: {err}")
            
        new_size = os.path.getsize(temp_path_mp4)
        if new_size >= orig_size:
            os.remove(temp_path_mp4)
            return CompressionResult(file_path, file_path, orig_size, orig_size, True, "Already optimal")
            
        out_path = str(Path(file_path).with_suffix(".mp4")) if in_place else _get_unique_path(str(Path(file_path).with_suffix(".mp4")))
            
        if in_place:
            # Replaces the ORIGINAL path if they share the same suffix, or replaces + deletes the old container.
            if file_path != out_path: 
                # e.g original was .mov, out is .mp4
                from send2trash import send2trash
                send2trash(file_path)
            os.replace(temp_path_mp4, out_path)
            return CompressionResult(file_path, out_path, orig_size, new_size, True)
        else:
            os.replace(temp_path_mp4, out_path)
            return CompressionResult(file_path, out_path, orig_size, new_size, True)
            
    except Exception as e:
        log.error(f"Video compression failed for {file_path}: {e}")
        if 'temp_path_mp4' in locals() and os.path.exists(temp_path_mp4):
            os.remove(temp_path_mp4)
        return CompressionResult(file_path, None, 0, 0, False, str(e))
