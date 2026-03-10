
import os
import sys
import time
import shutil
import tempfile
import cv2
import numpy as np
from PIL import Image

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ui.thumbnail_cache import ThumbnailCache
from ui.constants import THUMBNAIL_SIZE

def create_dummy_video(path, duration=2, fps=30):
    """Creates a dummy mp4 video with a red circle moving."""
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(path, fourcc, fps, (640, 480))
    
    for i in range(duration * fps):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Draw a moving red circle
        center = (100 + i * 5, 240)
        cv2.circle(frame, center, 50, (0, 0, 255), -1)
        out.write(frame)
    
    out.release()

def test_video_thumbnail_generation():
    temp_dir = tempfile.mkdtemp()
    try:
        video_path = os.path.join(temp_dir, "test_video.mp4")
        create_dummy_video(video_path)
        
        print(f"Created dummy video at {video_path}")
        
        # We need a mock for CTK if possible, or just test the PIL part if we refactor.
        # However, ThumbnailCache produces ctk.CTkImage. 
        # Since we are in a headless environment, ctk might fail if it tries to init tk.
        # Let's try to test the _load_worker logic specifically if we can.
        
        cache = ThumbnailCache()
        
        # We'll use a threading event or just wait since it's async
        ready_path = None
        ready_image = None
        
        def on_ready(p, img):
            nonlocal ready_path, ready_image
            ready_path = p
            ready_image = img
            print(f"Thumbnail ready for {p}")

        cache.get_or_schedule(video_path, on_ready)
        
        # Wait up to 5 seconds
        start = time.time()
        while ready_path is None and time.time() - start < 5:
            time.sleep(0.1)
            
        if ready_path:
            print("Successfully generated thumbnail for video!")
            # Check if it's cached on disk
            cached_path = cache.disk_cache.get(video_path)
            if cached_path and os.path.exists(cached_path):
                print(f"Disk cache entry found: {cached_path}")
            else:
                print("Error: Disk cache entry not found!")
        else:
            print("Failed to generate thumbnail within timeout.")
            
    finally:
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    test_video_thumbnail_generation()
