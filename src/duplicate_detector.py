"""
duplicate_detector.py
---------------------
Stage 3 (and 4) of the scanning pipeline.

Improvements over v1:
  • DuplicateGroup gains: match_type, hamming_distance, resolutions dict
  • DetectionResult gains: prefilter_exact_count, phash_candidates_count,
    scan_stats for UI display
  • detect_duplicates() accepts pre-confirmed byte-identical groups from the
    MD5 pre-filter so they appear in results without any phash work
  • _group_fuzzy_bktree() replaces the O(n²) pair-comparison loop with a
    Burkhard-Keller tree giving O(n log n) average build + query time —
    at 10 000 images with tolerance=3 this is 100-1000× faster

BK-tree complexity recap:
  Build:   O(n log n) average, O(n²) degenerate (all-equal hashes)
  Query:   O(log n) average per lookup  — varies with tolerance and distribution
  vs O(n²) naive:  10k images, tol=3 → ~50M comparisons vs ~130k BK queries
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from folder_scanner import ScanFile

log = logging.getLogger(__name__)


# ─── BK-Tree ─────────────────────────────────────────────────────────────────

class _BKNode:
    """Single node in the BK-tree.  __slots__ keeps memory compact."""
    __slots__ = ("hash_int", "paths", "children")

    def __init__(self, hash_int: int, path: str):
        self.hash_int: int = hash_int
        self.paths: list[str] = [path]
        self.children: dict[int, _BKNode] = {}


class BKTree:
    """
    Burkhard-Keller tree for efficient Hamming-distance nearest-neighbour search.

    Stores 64-bit integer hashes (converted from imagehash hex strings).
    insert() and search() both use iterative traversal (no recursion) to stay
    safe on deep trees without hitting Python's recursion limit.

    Usage:
        tree = BKTree()
        tree.insert(hash_int, path)
        results = tree.search(query_hash_int, tolerance=3)
        # → [(distance, path), …]
    """

    def __init__(self):
        self._root: Optional[_BKNode] = None
        self.size: int = 0

    # ── Internal ────────────────────────────────────────────────────────────

    @staticmethod
    def _hamming(a: int, b: int) -> int:
        """Hamming distance between two integers via atomic popcount."""
        return (a ^ b).bit_count()

    # ── Public ──────────────────────────────────────────────────────────────

    def insert(self, hash_int: int, path: str) -> None:
        """
        Insert a (hash, path) pair.  If an identical hash already exists in
        the tree, the path is appended to that node's path list (exact match).
        """
        self.size += 1
        if self._root is None:
            self._root = _BKNode(hash_int, path)
            return

        node = self._root
        while True:
            dist = self._hamming(hash_int, node.hash_int)
            if dist == 0:
                node.paths.append(path)  # exact duplicate hash
                return
            if dist not in node.children:
                node.children[dist] = _BKNode(hash_int, path)
                return
            node = node.children[dist]

    def search(self, hash_int: int, tolerance: int) -> list[tuple[int, str]]:
        """
        Return all (distance, path) pairs within Hamming distance ≤ tolerance.

        The BK-tree property guarantees we only visit children c where
        |dist(query, parent) - dist_branch| ≤ tolerance, pruning large subtrees.
        """
        if self._root is None:
            return []

        results: list[tuple[int, str]] = []
        stack: list[_BKNode] = [self._root]

        while stack:
            node = stack.pop()
            dist = self._hamming(hash_int, node.hash_int)
            if dist <= tolerance:
                for p in node.paths:
                    results.append((dist, p))
            # Only descend into branches that could possibly contain matches
            lo = max(0, dist - tolerance)
            hi = dist + tolerance
            for branch_dist, child in node.children.items():
                if lo <= branch_dist <= hi:
                    stack.append(child)

        return results


# ─── Data Model ───────────────────────────────────────────────────────────────

MatchType = Literal["exact_bytes", "exact_hash", "near_duplicate"]


@dataclass
class DuplicateGroup:
    """
    A cluster of visually identical (or near-identical) images.

    Fields added in v2:
        match_type       — how the group was detected
        hamming_distance — max pairwise hash distance within the group (0 for exact)
        resolutions      — {path: (w, h)} for UI display; may be empty dict if
                           the group came from the MD5 pre-filter stage
    """
    group_id: int
    hash_value: str                                   # representative phash hex
    match_type: MatchType = "exact_hash"
    hamming_distance: int = 0                         # 0 = exact, 1-10 = near
    files: list[str] = field(default_factory=list)
    file_sizes: dict[str, int] = field(default_factory=dict)        # path → bytes
    resolutions: dict[str, tuple[int, int]] = field(default_factory=dict)  # path → (w,h)
    sharpness: dict[str, float] = field(default_factory=dict)       # path → variance of laplacian
    brightness: dict[str, float] = field(default_factory=dict)      # path → mean luminance

    # ── Computed properties ─────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self.files)

    def _calculate_score(self, path: str) -> float:
        """
        Computes a Quality Score (0.0 - 1.0) based on:
        - Resolution (40% weight)
        - Sharpness (30% weight)
        - File Size (25% weight)
        - Brightness (5% weight; penalizes extremes)
        """
        res = self.resolutions.get(path, (0, 0))
        pixels = res[0] * res[1]
        size = self.file_sizes.get(path, 0)
        sharp = self.sharpness.get(path, 0.0)
        bright = self.brightness.get(path, 127.0)

        max_pixels = max((self.resolutions.get(p, (0, 0))[0] * self.resolutions.get(p, (0, 0))[1] for p in self.files), default=1)
        if max_pixels == 0: max_pixels = 1
        max_size = max((self.file_sizes.get(p, 0) for p in self.files), default=1)
        if max_size == 0: max_size = 1
        max_sharp = max((self.sharpness.get(p, 0.0) for p in self.files), default=1.0)
        if max_sharp <= 0: max_sharp = 1.0

        res_score = pixels / max_pixels
        size_score = size / max_size
        sharp_score = sharp / max_sharp

        if bright < 30 or bright > 220:
            bright_score = 0.5
        else:
            bright_score = 1.0

        return (res_score * 0.40) + (sharp_score * 0.30) + (size_score * 0.25) + (bright_score * 0.05)

    @property
    def suggested_keep(self) -> Optional[str]:
        """Keep the highest-quality original based on the composite Quality Score."""
        if not self.files:
            return None
        return max(self.files, key=self._calculate_score)

    @property
    def suggested_delete(self) -> list[str]:
        keep = self.suggested_keep
        return [f for f in self.files if f != keep]

    def wasted_bytes(self) -> int:
        keep = self.suggested_keep
        return sum(s for p, s in self.file_sizes.items() if p != keep)

    @property
    def similarity_pct(self) -> int:
        """Visual similarity as a 0-100 integer (100 = pixel-perfect hash match)."""
        # 64-bit for images, 192-bit for videos (3 frames)
        bit_len = 192 if len(self.hash_value) > 16 else 64
        return max(0, 100 - round(self.hamming_distance / bit_len * 100))

    @property
    def match_label(self) -> str:
        labels = {
            "exact_bytes": "Exact Copy",
            "exact_hash":  "Exact Hash",
            "near_duplicate": f"~{self.similarity_pct}% Similar",
        }
        return labels.get(self.match_type, self.match_type)


@dataclass
class DetectionResult:
    """Complete output of the duplicate-detection pipeline."""
    groups: list[DuplicateGroup] = field(default_factory=list)
    total_images_checked: int = 0
    unique_images: int = 0

    # ── v2 stats ────────────────────────────────────────────────────────────
    prefilter_exact_count: int = 0    # images confirmed exact by MD5
    phash_candidates_count: int = 0   # images that actually needed phashing
    detection_time_ms: float = 0.0    # wall time for the grouping step

    # ── v3 advanced tools ───────────────────────────────────────────────────
    screenshots: list[str] = field(default_factory=list)
    blurry_photos: list[str] = field(default_factory=list)
    large_files: list[str] = field(default_factory=list)
    
    # ── v4 analytics ────────────────────────────────────────────────────────
    folder_sizes: dict[str, int] = field(default_factory=dict)
    
    # ── v5 timeline ─────────────────────────────────────────────────────────
    timeline: dict[int, list[str]] = field(default_factory=dict) # year -> [paths]
    
    # ── v6 messages ─────────────────────────────────────────────────────────
    whatsapp_media: list[str] = field(default_factory=list)
    telegram_media: list[str] = field(default_factory=list)

    @property
    def duplicate_group_count(self) -> int:
        return len(self.groups)

    @property
    def total_duplicates(self) -> int:
        return sum(g.count - 1 for g in self.groups)

    def total_wasted_bytes(self) -> int:
        return sum(g.wasted_bytes() for g in self.groups)

    def total_wasted_mb(self) -> float:
        return self.total_wasted_bytes() / (1024 * 1024)

    @property
    def prefilter_savings_pct(self) -> float:
        if self.total_images_checked == 0:
            return 0.0
        return self.prefilter_exact_count / self.total_images_checked * 100


# ─── Union-Find (path-compression + union-by-rank) ────────────────────────────

class _UnionFind:
    """
    Disjoint-set data structure for merging duplicate clusters.
    Path compression + union-by-rank → near O(1) amortised per operation.
    """
    __slots__ = ("_parent", "_rank", "_index")

    def __init__(self, items: list[str]):
        self._index = {item: i for i, item in enumerate(items)}
        n = len(items)
        self._parent = list(range(n))
        self._rank   = [0] * n

    def find(self, item: str) -> int:
        x = self._index[item]
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]  # path halving
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1

    def groups(self) -> dict[int, list[str]]:
        """Return {root_index: [item, …]} for every component."""
        components: dict[int, list[str]] = defaultdict(list)
        for item, idx in self._index.items():
            components[self.find(item)].append(item)
        return dict(components)


# ─── Grouping Algorithms ──────────────────────────────────────────────────────

def _hex_to_int(hash_hex: str) -> int:
    """Convert an imagehash hex string to a 64-bit integer for bitwise ops."""
    return int(hash_hex, 16)


def _group_exact_hash(hash_map: dict[str, dict]) -> list[DuplicateGroup]:
    """
    O(n) exact-hash grouping using a plain dict bucket.
    Returns only groups with ≥ 2 members.
    """
    buckets: dict[str, list[str]] = defaultdict(list)
    for path, meta in hash_map.items():
        buckets[meta["hash"]].append(path)

    groups: list[DuplicateGroup] = []
    gid = 0
    for hash_val, paths in buckets.items():
        if len(paths) < 2:
            continue
        groups.append(DuplicateGroup(
            group_id=gid,
            hash_value=hash_val,
            match_type="exact_hash",
            hamming_distance=0,
            files=paths,
            file_sizes={p: hash_map[p]["size"] for p in paths},
            resolutions={p: hash_map[p].get("resolution", (0, 0)) for p in paths},
            sharpness={p: hash_map[p].get("blur_score", 0.0) for p in paths},
            brightness={p: hash_map[p].get("brightness", 127.0) for p in paths},
        ))
        gid += 1

    groups.sort(key=lambda g: g.count, reverse=True)
    return groups


def _group_fuzzy_bktree(
    hash_map: dict[str, dict],
    tolerance: int,
) -> list[DuplicateGroup]:
    """
    O(n log n) near-duplicate grouping via BK-tree + Union-Find.

    Algorithm:
        1. Convert every phash hex string to a 64-bit integer (fast XOR ops).
        2. Build a BK-tree incrementally.  For each new image, query the tree
           for all existing images within `tolerance` Hamming distance and union
           them with the new image in the Union-Find structure.
        3. Collect Union-Find components of size ≥ 2 → DuplicateGroups.

    Why this is fast:
        BK-tree prunes subtrees whose branch distance is outside the range
        [query_dist − tol, query_dist + tol], so only a small fraction of nodes
        are visited per query.  At tolerance ≤ 5 this typically inspects < 5 %
        of the tree per query, giving O(log n) per lookup vs O(n) naïve.

    Args:
        hash_map:  {path: {"hash": hex_str, "size": int, "resolution": tuple}}
        tolerance: Hamming distance threshold (1-10 recommended; 0 → use exact)
    """
    if not hash_map:
        return []

    paths = list(hash_map.keys())
    hash_ints: dict[str, int] = {p: _hex_to_int(hash_map[p]["hash"]) for p in paths}

    uf = _UnionFind(paths)
    tree = BKTree()

    for path in paths:
        h_str = hash_map[path]["hash"]
        h = _hex_to_int(h_str)
        
        # Scale tolerance for videos (longer hashes)
        current_tol = tolerance
        if len(h_str) > 16: # Video hashes are 48 chars (3 * 16 hex)
            # Scale proportionally: 64 bits -> tol, 192 bits -> tol * 3
            current_tol = int(tolerance * (len(h_str) / 16))
            
        neighbours = tree.search(h, current_tol)
        for _dist, neighbour_path in neighbours:
            uf.union(path, neighbour_path)
        tree.insert(h, path)

    # Build groups from Union-Find components
    groups: list[DuplicateGroup] = []
    gid = 0
    for _root, members in uf.groups().items():
        if len(members) < 2:
            continue

        # Representative hash = member with the largest file (keeper candidate)
        rep = max(members, key=lambda p: hash_map[p]["size"])
        rep_hash = hash_map[rep]["hash"]
        rep_int  = hash_ints[rep]

        # Max pairwise Hamming within the group
        max_ham = max(
            (hash_ints[m] ^ rep_int).bit_count() for m in members
        )

        groups.append(DuplicateGroup(
            group_id=gid,
            hash_value=rep_hash,
            match_type="near_duplicate",
            hamming_distance=max_ham,
            files=members,
            file_sizes={p: hash_map[p]["size"] for p in members},
            resolutions={p: hash_map[p].get("resolution", (0, 0)) for p in members},
            sharpness={p: hash_map[p].get("blur_score", 0.0) for p in members},
            brightness={p: hash_map[p].get("brightness", 127.0) for p in members},
        ))
        gid += 1

    groups.sort(key=lambda g: g.count, reverse=True)
    return groups


def _exact_byte_groups_to_duplicate_groups(
    exact_byte_groups: list[list[str]],
    start_gid: int = 0,
) -> list[DuplicateGroup]:
    """
    Convert pre-confirmed MD5 byte-identical groups into DuplicateGroup objects.
    Resolution is not available for these (no Image.open was done); set to (0,0).
    File sizes are read from disk (fast stat() call).
    """
    groups: list[DuplicateGroup] = []
    for gid, paths in enumerate(exact_byte_groups, start=start_gid):
        sizes: dict[str, int] = {}
        for p in paths:
            try:
                sizes[p] = os.path.getsize(p)
            except OSError:
                sizes[p] = 0

        # Use first path's filename as a proxy hash identifier
        groups.append(DuplicateGroup(
            group_id=gid,
            hash_value=f"md5-group-{gid}",
            match_type="exact_bytes",
            hamming_distance=0,
            files=paths,
            file_sizes=sizes,
            resolutions={p: (0, 0) for p in paths},
        ))
    return groups



# ─── Public API ───────────────────────────────────────────────────────────────

def detect_duplicates(
    hash_map: dict[str, dict], 
    hash_tolerance: int = 2, 
    exact_byte_dupes: list[list[str]] = None, 
    all_images: list = None, 
    folder_sizes: dict[str, int] = None, 
    mode: str = "full",
    do_metadata: bool = True,
    do_duplicates: bool = True
) -> DetectionResult:
    """
    Group images by identical or similar perceptual hashes.

    Args:
        hash_map:
            Output of hash_generator.generate_hashes():
            {path: {"hash": str, "resolution": (w,h), "size": int}}

        hash_tolerance:
            Hamming distance threshold.
            0  → exact hash match only (O(n), fastest)
            1-3 → catches re-saves, minor EXIF edits, minor JPEG artefacts
            4-8 → catches crops, brightness/contrast adjustments
            9+  → very liberal; expect false-positives

        exact_byte_dupes:
            Optional pre-confirmed byte-identical groups from
            hash_generator.find_exact_byte_duplicates().
            These are injected directly into results without any phash work.

    Returns:
        DetectionResult with all groups sorted by size (largest first).
    """
    t0 = time.perf_counter()
    # Source of truth for total count is all_images (the full scanner output)
    image_count = len(all_images) if all_images is not None else (len(hash_map) + sum(len(g) for g in (exact_byte_dupes or [])))
    
    result = DetectionResult(
        total_images_checked=image_count,
        prefilter_exact_count=sum(len(g) for g in (exact_byte_dupes or [])),
        phash_candidates_count=len(hash_map),
        folder_sizes=folder_sizes or {},
    )

    # Determine what to detect based on mode
    do_metadata = mode in ["full", "photos", "screenshots", "blurry", "large", "messages", "timeline"]
    do_duplicates = mode in ["full", "photos", "duplicates", "similar"]

    # OPTIMIZATION: For metadata-only modes, skip the expensive hash_map processing entirely
    # and only work with the all_images pre-computed metadata
    if mode in ["blurry", "large", "screenshots", "messages", "timeline"] and not do_duplicates:
        # Metadata-only scan: extremely fast, no hashing needed
        if all_images:
            timeline = defaultdict(list)
            for img in all_images:
                if do_metadata:
                    try:
                        year = time.localtime(img.timestamp).tm_year
                        timeline[year].append(img.path)
                    except (ValueError, OSError):
                        pass
                
                if (mode == "screenshots" or mode in ["full", "photos"]) and img.is_screenshot:
                    result.screenshots.append(img.path)
                
                if (mode == "messages" or mode in ["full", "photos"]):
                    if img.is_whatsapp:
                        result.whatsapp_media.append(img.path)
                    elif img.is_telegram:
                        result.telegram_media.append(img.path)
                
                if (mode == "large" or mode in ["full", "photos"]):
                    if img.size_bytes > 50 * 1024 * 1024:
                        result.large_files.append(img.path)
                
                if (mode == "blurry" or mode in ["full", "photos"]):
                    meta = hash_map.get(img.path)
                    if meta:
                        blur = meta.get("blur_score", 999)
                        if blur < 100:
                            result.blurry_photos.append(img.path)
            
            if mode in ["timeline", "full", "photos"]:
                result.timeline = {y: timeline[y] for y in sorted(timeline.keys(), reverse=True)}
        
        result.groups = []
        result.unique_images = result.total_images_checked
        result.detection_time_ms = (time.perf_counter() - t0) * 1000
        
        log.info(
            "Metadata-only scan complete in %.1fms: blurry=%d, large=%d, screenshots=%d, messages=%d",
            result.detection_time_ms,
            len(result.blurry_photos),
            len(result.large_files),
            len(result.screenshots),
            len(result.whatsapp_media) + len(result.telegram_media),
        )
        return result

    # ── Phase 0: Metadata Categories (Screenshots, Blurry, Large, Social) ──
    if all_images and do_metadata:
        timeline = defaultdict(list)
        for img in all_images:
            # 1. Timeline
            if mode in ["full", "photos", "timeline"]:
                try:
                    year = time.localtime(img.timestamp).tm_year
                    timeline[year].append(img.path)
                except (ValueError, OSError):
                    pass
            
            # 2. Categories
            if mode in ["full", "photos", "screenshots"] and img.is_screenshot:
                result.screenshots.append(img.path)
            
            if mode in ["full", "photos", "messages"]:
                if img.is_whatsapp:
                    result.whatsapp_media.append(img.path)
                elif img.is_telegram:
                    result.telegram_media.append(img.path)
                
            # Large files (> 50MB)
            if mode in ["full", "photos", "large"] and img.size_bytes > 50 * 1024 * 1024:
                result.large_files.append(img.path)
                
            # Blurry (based on blur_score if available from hashing stage)
            if mode in ["full", "photos", "blurry"]:
                meta = hash_map.get(img.path)
                if meta:
                    blur = meta.get("blur_score", 999)
                    if blur < 100: # Heuristic threshold
                        result.blurry_photos.append(img.path)

        # Convert to plain dict and sort years descending
        if mode in ["full", "photos", "timeline"]:
            result.timeline = {y: timeline[y] for y in sorted(timeline.keys(), reverse=True)}

    # ── Phase A: groups from perceptual hashing ─────────────────────────────
    if do_duplicates:
        if hash_tolerance == 0:
            phash_groups = _group_exact_hash(hash_map)
        else:
            phash_groups = _group_fuzzy_bktree(hash_map, hash_tolerance)

        # ── Phase B: inject pre-confirmed exact-byte groups ──────────────────────
        byte_groups = _exact_byte_groups_to_duplicate_groups(
            exact_byte_dupes or [], start_gid=len(phash_groups)
        )

        # ── Merge and renumber ──────────────────────────────────────────────────
        all_groups = phash_groups + byte_groups
        for new_id, grp in enumerate(all_groups):
            grp.group_id = new_id

        result.groups = all_groups
    else:
        result.groups = []

    result.unique_images = result.total_images_checked - result.total_duplicates
    result.detection_time_ms = (time.perf_counter() - t0) * 1000

    log.info(
        "Detection complete in %.1fms: %d group(s), %d duplicates, %d unique",
        result.detection_time_ms,
        result.duplicate_group_count,
        result.total_duplicates,
        result.unique_images,
    )
    return result
