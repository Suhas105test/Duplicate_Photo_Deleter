"""
test_pipeline.py
----------------
Lightweight internal test suite for the Smart Photo Cleaner pipeline.
Tests folder scanning, hash generation, duplicate detection, and deletion.

Run:
    cd c:\\Users\\suhas\\AI\\Personal_Apps\\Duplicate_Photo_Deleter
    python -m pytest tests/test_pipeline.py -v
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ─── Path setup (add src to path) ────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from folder_scanner import scan_folder, scan_files, ScanResult, ScanFile
from hash_generator import generate_hashes, find_exact_byte_duplicates, get_exact_md5
from duplicate_detector import detect_duplicates, DuplicateGroup, DetectionResult, BKTree
from delete_manager import delete_files, verify_paths_exist, DeleteResult

from ui.app import SmartPhotoCleanerApp


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_dir():
    """Create a temporary directory that is cleaned up after the test."""
    d = tempfile.mkdtemp(prefix="spc_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_images(temp_dir):
    """
    Create minimal valid JPEG files that are structurally distinct.
    Uses patterns (not just solid colors) so phash can distinguish them.
    """
    from PIL import Image, ImageDraw

    paths = []
    for i in range(3):
        img = Image.new("RGB", (64, 64), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        if i == 0:
            # Horizontal stripes
            for y in range(0, 64, 8):
                draw.rectangle([0, y, 63, y + 3], fill=(255, 0, 0))
        elif i == 1:
            # Vertical stripes
            for x in range(0, 64, 8):
                draw.rectangle([x, 0, x + 3, 63], fill=(0, 255, 0))
        else:
            # Diagonal pattern
            for d in range(0, 128, 8):
                draw.line([(d, 0), (0, d)], fill=(0, 0, 255), width=3)
        path = os.path.join(temp_dir, f"test_image_{i}.jpg")
        img.save(path, "JPEG")
        img.close()
        paths.append(path)
    return paths


@pytest.fixture
def duplicate_images(temp_dir):
    """
    Create two byte-identical copies of the same image.
    """
    from PIL import Image

    img = Image.new("RGB", (20, 20), color=(128, 128, 128))
    path_a = os.path.join(temp_dir, "original.jpg")
    path_b = os.path.join(temp_dir, "copy.jpg")
    img.save(path_a, "JPEG")
    img.save(path_b, "JPEG")
    img.close()
    return [path_a, path_b]


# ═════════════════════════════════════════════════════════════════════════════
# 1. Folder Scanner Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestFolderScanner:

    def test_scan_valid_directory(self, sample_images, temp_dir):
        result = scan_folder(temp_dir)
        assert result.image_count == 3
        assert result.total_files_scanned == 3
        assert result.skipped_files == 0
        assert len(result.errors) == 0
        assert result.total_size_bytes > 0

    def test_scan_empty_directory(self, temp_dir):
        result = scan_folder(temp_dir)
        assert result.image_count == 0
        assert result.total_files_scanned == 0

    def test_scan_nonexistent_directory(self):
        result = scan_folder("/nonexistent/path/xyz123")
        assert result.image_count == 0
        assert len(result.errors) > 0

    def test_scan_skips_non_image_files(self, temp_dir):
        # Create an unsupported file
        exe_path = os.path.join(temp_dir, "program.exe")
        with open(exe_path, "w") as f:
            f.write("fake exe")
        result = scan_folder(temp_dir)
        assert result.image_count == 0
        assert result.skipped_files == 1

    def test_scan_skips_zero_byte_files(self, temp_dir):
        empty_path = os.path.join(temp_dir, "empty.jpg")
        with open(empty_path, "w") as f:
            pass  # create 0-byte file
        result = scan_folder(temp_dir)
        assert result.image_count == 0
        assert result.skipped_files == 1

    def test_scan_files_specific_paths(self, sample_images):
        result = scan_files(sample_images[:2])
        assert result.image_count == 2

    def test_scan_non_recursive(self, temp_dir, sample_images):
        # Create a subdirectory with an image
        from PIL import Image
        sub = os.path.join(temp_dir, "subdir")
        os.makedirs(sub)
        img = Image.new("RGB", (10, 10), color=(0, 0, 0))
        img.save(os.path.join(sub, "nested.jpg"), "JPEG")
        img.close()

        recursive_result = scan_folder(temp_dir, recursive=True)
        flat_result = scan_folder(temp_dir, recursive=False)

        assert recursive_result.image_count == flat_result.image_count + 1

    def test_group_by_size_caching(self, sample_images, temp_dir):
        result = scan_folder(temp_dir)
        groups1 = result.group_by_size()
        groups2 = result.group_by_size()
        # Should be the exact same object (cached)
        assert groups1 is groups2

    def test_screenshot_detection(self, temp_dir):
        from PIL import Image
        img = Image.new("RGB", (10, 10), color=(0, 0, 0))
        img.save(os.path.join(temp_dir, "Screenshot_2024-01-01.png"), "PNG")
        img.close()

        result = scan_folder(temp_dir)
        assert result.image_count == 1
        assert result.images[0].is_screenshot is True

    def test_image_file_model(self):
        img = ScanFile(path="/test/photo.jpg", size_bytes=1024, extension=".jpg", filename="photo.jpg")
        assert img.path == "/test/photo.jpg"
        assert img.size_bytes == 1024
        assert img.is_video is False
        assert img.is_screenshot is False


# ─────────────────────────────────────────────────────────────────────────────
# 7. UI Helpers (non-GUI logic)
# ─────────────────────────────────────────────────────────────────────────────

def test_scan_from_tab_sets_request(monkeypatch):
    """_scan_from_tab should record the requested tab and forward mode to _start_scan and flip to dashboard."""
    # create lightweight dummy instance without invoking full tkinter init
    class Dummy(SmartPhotoCleanerApp):
        def __init__(self):
            # skip super init entirely but ensure attributes used by _scan_from_tab exist
            self._detection_result = None
            self._is_scanning = False
            self._scan_request_tab = None
    app = Dummy()
    calls = {}
    def fake_start(mode):
        calls['mode'] = mode
    app._start_scan = fake_start
    selected = {}
    def fake_select(name):
        selected['name'] = name
    app.select_frame_by_name = fake_select

    app._scan_from_tab("Duplicates", mode="videos")
    assert app._scan_request_tab == "Duplicates"
    assert calls.get('mode') == "videos"
    assert selected.get('name') == "Dashboard"


def test_scan_from_tab_uses_cache(monkeypatch):
    """Tab scan should always perform a scan to show progress and timing."""
    class Dummy(SmartPhotoCleanerApp):
        def __init__(self):
            pass
    app = Dummy()
    app._is_scanning = False
    from duplicate_detector import DetectionResult
    app._detection_result = DetectionResult(total_images_checked=5)
    called = {'start':False, 'select':None, 'status':None}
    app._start_scan = lambda mode: called.update(start=True)
    app.select_frame_by_name = lambda name: called.update(select=name)
    app._set_status = lambda t, c=None: called.update(status=t)

    app._scan_from_tab("Blurry Photos")
    assert called.get('start') is True
    assert called.get('select') == "Dashboard"
    assert called.get('status') is None


def test_set_scanning_ui_disables_tab_buttons():
    """_set_scanning_ui should toggle state of tab-specific scan buttons."""
    class Dummy(SmartPhotoCleanerApp):
        def __init__(self):
            # minimal init
            self._scan_btn = type('B', (), {'configure': lambda self, **kw: setattr(self, 'state', kw.get('state'))})()
            self._scan_videos_btn = type('B', (), {'configure': lambda self, **kw: setattr(self, 'state', kw.get('state'))})()
            self._pause_btn = type('B', (), {'configure': lambda self, **kw: setattr(self, 'state', kw.get('state'))})()
            self._cancel_btn = type('B', (), {'configure': lambda self, **kw: setattr(self, 'state', kw.get('state'))})()
            # create fake tab buttons
            btn1 = type('B', (), {'configure': lambda self, **kw: setattr(self, 'state', kw.get('state'))})()
            btn2 = type('B', (), {'configure': lambda self, **kw: setattr(self, 'state', kw.get('state'))})()
            self._tab_scan_buttons = [btn1, btn2]
    app = Dummy()
    app._set_scanning_ui(True)
    for b in app._tab_scan_buttons:
        assert getattr(b, 'state', None) == 'disabled'
    app._set_scanning_ui(False)
    for b in app._tab_scan_buttons:
        assert getattr(b, 'state', None) == 'normal'


# ═════════════════════════════════════════════════════════════════════════════
# 2. Hash Generator Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestHashGenerator:

    def test_generate_hashes_valid_images(self, sample_images):
        hashes = generate_hashes(sample_images, num_workers=1)
        assert len(hashes) == 3
        for path in sample_images:
            assert path in hashes
            meta = hashes[path]
            assert "hash" in meta
            assert "resolution" in meta
            assert "size" in meta
            assert "blur_score" in meta
            assert meta["resolution"] == (64, 64)
            assert meta["size"] > 0
            assert isinstance(meta["blur_score"], float)

    def test_generate_hashes_empty_list(self):
        hashes = generate_hashes([])
        assert hashes == {}

    def test_generate_hashes_corrupt_file(self, temp_dir):
        corrupt_path = os.path.join(temp_dir, "corrupt.jpg")
        with open(corrupt_path, "wb") as f:
            f.write(b"not a real image file")
        hashes = generate_hashes([corrupt_path], num_workers=1)
        assert len(hashes) == 0  # corrupt file should be silently skipped

    def test_get_exact_md5(self, sample_images):
        md5 = get_exact_md5(sample_images[0])
        assert md5 is not None
        assert len(md5) == 32  # MD5 hex string length

    def test_md5_consistency(self, sample_images):
        md5_a = get_exact_md5(sample_images[0])
        md5_b = get_exact_md5(sample_images[0])
        assert md5_a == md5_b  # same file → same hash

    def test_find_exact_byte_duplicates(self, duplicate_images):
        # Both files have the same content, so same size
        size = os.path.getsize(duplicate_images[0])
        size_groups = {size: duplicate_images}
        exact_groups, phash_candidates = find_exact_byte_duplicates(
            size_groups, num_workers=1
        )
        # Should find 1 group of 2 identical files
        assert len(exact_groups) == 1
        assert len(exact_groups[0]) == 2

    def test_progress_callback_called(self, sample_images):
        callback_calls = []
        def cb(done, total):
            callback_calls.append((done, total))

        generate_hashes(sample_images, progress_callback=cb, num_workers=1)
        assert len(callback_calls) == len(sample_images)


# ═════════════════════════════════════════════════════════════════════════════
# 3. Duplicate Detector Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestDuplicateDetector:

    def test_detect_exact_hash_duplicates(self):
        # Two images with the same phash
        hash_map = {
            "/img/a.jpg": {"hash": "abcdef0123456789", "resolution": (100, 100), "size": 1000},
            "/img/b.jpg": {"hash": "abcdef0123456789", "resolution": (100, 100), "size": 900},
            "/img/c.jpg": {"hash": "0000000000000000", "resolution": (200, 200), "size": 2000},
        }
        result = detect_duplicates(hash_map, hash_tolerance=0)
        assert result.duplicate_group_count == 1
        assert result.groups[0].count == 2
        assert result.total_duplicates == 1

    def test_detect_no_duplicates(self):
        hash_map = {
            "/img/a.jpg": {"hash": "aaaa", "resolution": (100, 100), "size": 1000},
            "/img/b.jpg": {"hash": "bbbb", "resolution": (100, 100), "size": 900},
        }
        result = detect_duplicates(hash_map, hash_tolerance=0)
        assert result.duplicate_group_count == 0

    def test_detect_empty_input(self):
        result = detect_duplicates({}, hash_tolerance=0)
        assert result.duplicate_group_count == 0
        assert result.total_images_checked == 0

    def test_detect_with_exact_byte_dupes_injected(self):
        hash_map = {
            "/img/a.jpg": {"hash": "1111", "resolution": (100, 100), "size": 1000},
        }
        exact_byte_dupes = [["/img/x.jpg", "/img/y.jpg"]]
        result = detect_duplicates(hash_map, exact_byte_dupes=exact_byte_dupes)
        # Should have the exact byte group
        byte_groups = [g for g in result.groups if g.match_type == "exact_bytes"]
        assert len(byte_groups) == 1
        assert byte_groups[0].count == 2

    def test_fuzzy_detection(self):
        # Two hashes that differ by 1 bit (Hamming distance 1)
        hash_map = {
            "/img/a.jpg": {"hash": "0000000000000000", "resolution": (100, 100), "size": 1000},
            "/img/b.jpg": {"hash": "0000000000000001", "resolution": (100, 100), "size": 900},
        }
        result = detect_duplicates(hash_map, hash_tolerance=2)
        assert result.duplicate_group_count == 1
        assert result.groups[0].match_type == "near_duplicate"

    def test_duplicate_group_properties(self):
        group = DuplicateGroup(
            group_id=0,
            hash_value="abc",
            match_type="exact_hash",
            files=["/a.jpg", "/b.jpg", "/c.jpg"],
            file_sizes={"/a.jpg": 5000, "/b.jpg": 3000, "/c.jpg": 1000},
        )
        assert group.count == 3
        assert group.suggested_keep == "/a.jpg"  # largest file
        assert set(group.suggested_delete) == {"/b.jpg", "/c.jpg"}
        assert group.wasted_bytes() == 4000  # 3000 + 1000
        assert group.similarity_pct == 100  # hamming_distance=0

    def test_bk_tree_insert_and_search(self):
        tree = BKTree()
        tree.insert(0b1111, "/a.jpg")
        tree.insert(0b1110, "/b.jpg")  # distance 1 from first
        tree.insert(0b0000, "/c.jpg")  # distance 4 from first

        results = tree.search(0b1111, tolerance=1)
        paths = [p for _, p in results]
        assert "/a.jpg" in paths
        assert "/b.jpg" in paths
        assert "/c.jpg" not in paths


# ═════════════════════════════════════════════════════════════════════════════
# 4. Delete Manager Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestDeleteManager:

    def test_verify_paths_exist(self, sample_images, temp_dir):
        fake_path = os.path.join(temp_dir, "nonexistent.jpg")
        all_paths = sample_images + [fake_path]
        existing, missing = verify_paths_exist(all_paths)
        assert len(existing) == 3
        assert len(missing) == 1
        assert fake_path in missing

    def test_verify_empty_list(self):
        existing, missing = verify_paths_exist([])
        assert existing == []
        assert missing == []

    def test_delete_files_with_mock(self, sample_images):
        """Test deletion using mock to avoid actual Recycle Bin interaction."""
        with patch("delete_manager.send2trash") as mock_trash:
            result = delete_files(sample_images)
            assert len(result.deleted) == 3
            assert result.failure_count == 0
            assert mock_trash.call_count == 3

    def test_delete_files_handles_failure(self, sample_images):
        """Test that failed deletions are properly tracked."""
        with patch("delete_manager.send2trash", side_effect=OSError("permission denied")):
            result = delete_files(sample_images)
            assert len(result.deleted) == 0
            assert result.failure_count == 3
            for path, msg in result.failed:
                assert "permission denied" in msg

    def test_delete_result_summary(self):
        result = DeleteResult(deleted=["/a.jpg", "/b.jpg"], failed=[])
        assert "2 file(s)" in result.summary()
        assert "Successfully" in result.summary()

        result2 = DeleteResult(deleted=["/a.jpg"], failed=[("/b.jpg", "err")])
        assert "1 failed" in result2.summary()


# ═════════════════════════════════════════════════════════════════════════════
# 5. Integration (end-to-end pipeline)
# ═════════════════════════════════════════════════════════════════════════════

class TestPipelineIntegration:

    def test_full_pipeline(self, duplicate_images, temp_dir):
        """Run the complete scan → hash → detect pipeline on real images."""
        # Stage 1: Scan
        scan_result = scan_folder(temp_dir)
        assert scan_result.image_count == 2

        # Stage 2: MD5 pre-filter
        candidates = scan_result.size_candidate_groups
        if candidates:
            exact_groups, phash_paths = find_exact_byte_duplicates(
                candidates, num_workers=1
            )
        else:
            exact_groups = []
            phash_paths = scan_result.image_paths

        # Stage 3: Perceptual hashing
        hash_map = generate_hashes(phash_paths, num_workers=1)

        # Stage 4: Detection
        result = detect_duplicates(
            hash_map,
            hash_tolerance=0,
            exact_byte_dupes=exact_groups,
        )

        # The two identical images should form at least 1 group
        total_groups = result.duplicate_group_count
        assert total_groups >= 1

    def test_scan_files_pipeline(self, sample_images):
        """Test the scan_files → hash → detect path."""
        scan_result = scan_files(sample_images)
        assert scan_result.image_count == len(sample_images)

        hash_map = generate_hashes(scan_result.image_paths, num_workers=1)
        assert len(hash_map) == len(sample_images)

        result = detect_duplicates(hash_map, hash_tolerance=0)
        # 3 different colored images should not be duplicates
        assert result.duplicate_group_count == 0
