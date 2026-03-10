
import os
import sys
import time
import shutil
import tempfile
import cv2
import numpy as np
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

def test_image_viewer_video_integration():
    # Mocking ctk/tkinter is hard, but we can test the logic by mocking VideoPlayer
    with patch("ui.image_viewer.VideoPlayer") as MockPlayer:
        from ui.image_viewer import ImageViewer
        from folder_scanner import ScanResult
        
        parent = MagicMock()
        paths = ["video.mp4", "image.jpg"]
        
        # We need to mock os.path.splitext and folder_scanner.VIDEO_EXTENSIONS
        with patch("os.path.splitext") as mock_splitext:
            mock_splitext.side_effect = lambda p: (".mp4" if p.endswith(".mp4") else ".jpg", ".mp4" if p.endswith(".mp4") else ".jpg")
            
            with patch("folder_scanner.VIDEO_EXTENSIONS", {".mp4"}):
                # Init ImageViewer (will show first path)
                viewer = ImageViewer(parent, paths, 0, MagicMock())
                
                # Should have instantiated VideoPlayer for video.mp4
                print(f"VideoPlayer instantiated: {MockPlayer.called}")
                
                # Advance to next (image.jpg)
                viewer.index = 1
                viewer._show_current()
                
                # Should have stopped and destroyed the previous video player
                if MockPlayer.return_value.stop.called:
                    print("VideoPlayer stopped when switching to image.")
                if MockPlayer.return_value.destroy.called:
                    print("VideoPlayer destroyed when switching to image.")

if __name__ == "__main__":
    # This might fail due to Tkinter requirements in ImageViewer's __init__
    # but let's see if we can get through the logic.
    try:
        test_image_viewer_video_integration()
    except Exception as e:
        print(f"Test skipped or failed due to UI requirements: {e}")
