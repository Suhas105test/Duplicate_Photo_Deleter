# Smart Photo Cleaner

A smart desktop application to scan directories and detect duplicate or similar photos.

**New features:**

- Elapsed time and scanning speed indicators during scans
- Pause/resume and cancel controls for long-running operations
- Per-tab scan buttons with cached results to avoid unnecessary rescanning
- Dashboard statistics automatically stay in sync after deletions
- Improved stability for very large scans, and heatmap feature removed

## Installation

1. Create a virtual environment and install dependencies:
```bash
python -m venv venv
venv\Scripts\activate  # On Windows
pip install -r requirements.txt
```

2. Run the application
```bash
python src/main.py
```
