# Smart Photo Cleaner

Smart Photo Cleaner is a desktop utility for Windows that helps you reclaim disk space by finding duplicate and similar photos, screenshots, blurry images, large media files, and chat media.

## Key Features

- Multi-folder and file-level scanning: select folders or individual media files.
- Detailed scan progress with percentage, files scanned, elapsed time, and current action.
- Responsive scan start behavior for selected folders and files, including clear progress text.
- Scrollable dashboard layout for full visibility of all components.
- Exact duplicate detection for byte-identical files.
- Similar photo grouping using perceptual hashing with adjustable Hamming tolerance.
- Screenshot discovery to remove clutter from mobile captures.
- Blurry photo detection for removing low-quality shots.
- Large file analysis for videos, RAW files, and oversized images.
- Messages media scanning for WhatsApp and Telegram media items.
- Timeline viewer to browse media by year.
- Media compression for images and videos with JPEG quality and CRF controls.
- Safe deletion flow with recycle bin support and session stats.
- Dark/light theme support and per-tab guidance.

## How to Use

1. Create a virtual environment and install dependencies:
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

2. Run the application:
```powershell
python src/main.py
```

3. Use the Dashboard to add folders or select individual files, then scan for duplicates or media categories.
4. Switch between tabs to explore exact duplicates, similar photos, screenshots, blurry shots, large files, message media, and timeline results.
5. Use the Media Compressor tab to shrink images and videos.

## Documentation

- `feature.md` contains a complete list of application features and behavior.
