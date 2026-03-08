"""
delete_manager.py
-----------------
Handles the safe deletion of duplicate files by moving them to the system's
Recycle Bin / Trash.
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import List, Tuple

import pythoncom
import winshell
from send2trash import send2trash

log = logging.getLogger(__name__)

# Undo history - Stack of deleted batches (max 10)
_undo_stack: List[List[str]] = []


@dataclass
class DeleteResult:
    deleted: List[str]
    failed: List[Tuple[str, str]]

    @property
    def failure_count(self) -> int:
        return len(self.failed)

    def summary(self) -> str:
        if not self.failed:
            return f"Successfully sent {len(self.deleted)} file(s) to Recycle Bin."
        return f"Deleted {len(self.deleted)} file(s); {len(self.failed)} failed."


def verify_paths_exist(paths: List[str]) -> Tuple[List[str], List[str]]:
    """
    Checks which paths still exist on disk.
    Returns (existing_paths, missing_paths).
    """
    existing = []
    missing = []
    for path in paths:
        if os.path.exists(path):
            existing.append(path)
        else:
            missing.append(path)
    if missing:
        log.warning("verify_paths_exist: %d path(s) no longer exist", len(missing))
    return existing, missing


def delete_files(paths: List[str]) -> DeleteResult:
    """
    Sends the given paths to the Recycle Bin.
    """
    deleted = []
    failed = []
    for path in paths:
        try:
            # send2trash on Windows can fail with mixed slashes or long path prefixes
            norm_path = os.path.normpath(path)
            
            # Windows API (SHFileOperation) used by send2trash doesn't support extended paths
            if norm_path.startswith(r"\\?\UNC\\"):
                norm_path = "\\" + norm_path[7:]
            elif norm_path.startswith("\\\\?\\"):
                norm_path = norm_path[4:]
                
            send2trash(norm_path)
            deleted.append(path)
            log.info("Deleted (recycled): %s", norm_path)
        except OSError as e:
            log.error("Failed to delete %s: %s", path, e)
            failed.append((path, str(e)))
        except Exception as e:
            # Catch send2trash-specific non-OSError exceptions
            log.error("Unexpected error deleting %s: %s", path, e, exc_info=True)
            failed.append((path, str(e)))
            
    if deleted:
        global _undo_stack
        _undo_stack.append(deleted.copy())
        if len(_undo_stack) > 10:
            _undo_stack.pop(0) # Keep last 10 batches
        
    log.info("Delete complete: %d succeeded, %d failed", len(deleted), len(failed))
    return DeleteResult(deleted=deleted, failed=failed)

def has_undoable_deletes() -> bool:
    """Returns True if there is a batch of files that can be restored."""
    return len(_undo_stack) > 0


def restore_last_deleted() -> DeleteResult:
    """
    Attempts to restore the last batch of deleted files from the Windows Recycle Bin.
    Uses winshell.recycle_bin() to find items matching the original paths.
    """
    global _undo_stack
    
    if not _undo_stack:
        return DeleteResult(deleted=[], failed=[])
        
    last_batch = _undo_stack.pop()
    restored = []
    failed = []
    
    try:
        # Initialize COM in this thread to avoid "CoInitialize has not been called" errors
        pythoncom.CoInitialize()
        
        rb = winshell.recycle_bin()
        # Create a lowercase mapping for case-insensitive matching in Windows
        target_paths = {os.path.normcase(p): p for p in last_batch}
        
        # Iterate over items in the recycle bin
        for item in rb:
            item_orig_path = item.original_filename()
            norm_item = os.path.normcase(item_orig_path)
            
            if norm_item in target_paths:
                try:
                    # Restore the item
                    item.undelete()
                    restored.append(str(target_paths[norm_item]))
                    log.info("Restored from Recycle Bin: %s", item_orig_path)
                except Exception as e:
                    log.error("Failed to restore %s: %s", item_orig_path, e)
                    failed.append((str(target_paths[norm_item]), str(e)))

    except Exception as e:
        log.error("Failed interacting with Recycle Bin: %s", e, exc_info=True)
        # Mark all as failed if we couldn't even access the bin
        for path in last_batch:
            if path not in restored:
                failed.append((path, f"Recycle Bin API Error: {str(e)}"))
    finally:
        # Clean up COM
        pythoncom.CoUninitialize()
    
    log.info("Restore complete: %d succeeded, %d failed", len(restored), len(failed))
    return DeleteResult(deleted=restored, failed=failed)

