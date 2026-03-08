import customtkinter as ctk

# ─── Theme Configuration ──────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ─── Layout Constants ─────────────────────────────────────────────────────────
THUMBNAIL_SIZE = (118, 118)
THUMBNAIL_SIZE_CTK = (118, 118)   # logical size for CTkImage

# ─── Color Palette ───────────────────────────────────────────────────────────
ACCENT      = "#3B82F6"
DANGER      = "#EF4444"
SUCCESS     = "#22C55E"
WARN        = "#F59E0B"
NEAR_DUP    = "#A78BFA"           # purple for near-duplicate badge
EXACT_BYTE  = "#34D399"           # green for byte-exact badge
BG_CARD     = "#1E293B"
BG_DARK     = "#0F172A"
BG_THUMB    = "#0D1829"
WARM_ORANGE = "#F97316"
TEXT_MUTED  = "#94A3B8"
TEXT_DIM    = "#475569"
