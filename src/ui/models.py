from typing import Optional, Set

class ScanSettings:
    """
    Plain container for user-configurable scan parameters.
    """
    __slots__ = ("tolerance", "use_prefilter", "recursive", "target_extensions", "mode", "do_duplicates", "do_metadata")

    def __init__(self):
        self.tolerance:    int  = 0      # Hamming distance threshold
        self.use_prefilter: bool = True  # MD5 pre-filter enabled
        self.recursive:    bool = True   # recursive folder scan
        self.target_extensions: Optional[Set[str]] = None
        self.mode: str = "full"  # scan mode: "full", "blurry", etc.
        self.do_duplicates: bool = True
        self.do_metadata: bool = True
