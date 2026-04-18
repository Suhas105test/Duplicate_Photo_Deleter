# Smart Photo Cleaner Features

## Dashboard
- Multi-folder scanning with folder list preview.
- Individual file selection for scanning specific photos or videos.
- Scan progress bar with detailed status updates including percentage, files scanned, elapsed time, processing speed, and current action.
- Scan start now preserves progress state and shows clear active scan messages when a folder is selected.
- Scan refresh support to rerun the current scan mode.
- Quick access to both photo and video scans.
- Session statistics for scanned files, duplicates, space reclaimed, deleted files, and total savings.
- Scrollable dashboard layout to accommodate all UI components.

## Duplicate Detection
- Exact duplicate detection based on byte-identical files.
- Similar photo grouping using perceptual hashing.
- Adjustable similarity threshold via Hamming distance.
- Safe selection helpers to keep the best image in each duplicate group.
- Quick Clean mode to auto-select and remove duplicate copies.

## Media Categories
- Screenshots: identifies screenshots saved on disk.
- Blurry Photos: finds out-of-focus or low-quality images.
- Large Files: highlights the biggest media files taking up space.
- Messages Media: collects social app media such as WhatsApp and Telegram files.
- Timeline Viewer: shows media arranged by year for easy archival cleanup.

## Media Compression
- Compress images with an adjustable JPEG quality slider.
- Compress videos with CRF-based quality control.
- Optional in-place compression to replace originals.
- Compression progress and results output for every file.

## File Deletion & Recovery
- Delete selected files safely to the Windows Recycle Bin.
- Undo support for the most recent delete operation.
- UI refresh after deletions to keep results consistent.

## Settings
- MD5 pre-filter toggle for faster exact duplicate detection.
- Similarity threshold setting for more aggressive or conservative photo grouping.

## UI Improvements
- Empty-state guidance in each tab so users always know what the tab is for.
- Sidebar navigation for fast access to every feature.
- Light/Dark theme toggle.
- Improved state handling for scan buttons and folder selection.
- Scrollable dashboard to ensure all components are visible.
- Enhanced progress details during scans with real-time updates.

## Technical Improvements
- Better thread-safe scan progress handling.
- Cleaner folder-selection workflow with visual list updates.
- Fixed broken refresh scan behavior and missing compressor tab.
- Removed outdated duplicate settings implementation and cleaned UI logic.
