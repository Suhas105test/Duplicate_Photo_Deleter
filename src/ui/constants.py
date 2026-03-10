import customtkinter as ctk

# ─── Theme Configuration ──────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ─── Layout Constants ─────────────────────────────────────────────────────────
THUMBNAIL_SIZE = (118, 118)
THUMBNAIL_SIZE_CTK = (118, 118)   # logical size for CTkImage

# ─── Color Palette ───────────────────────────────────────────────────────────
# Format: (LightModeColor, DarkModeColor)
ACCENT      = ("#2563EB", "#3B82F6") # Slightly deeper blue for light mode
DANGER      = ("#DC2626", "#EF4444")
SUCCESS     = ("#16A34A", "#22C55E")
WARN        = ("#D97706", "#F59E0B")
NEAR_DUP    = ("#8B5CF6", "#A78BFA")
EXACT_BYTE  = ("#10B981", "#34D399")

BG_DARK     = ("#F1F5F9", "#0F172A") # Slate 100 for light, Dark Blue for dark
BG_CARD     = ("#FFFFFF", "#1E293B") # White for light, Slate 800 for dark
BG_THUMB    = ("#E2E8F0", "#0D1829")

WARM_ORANGE = ("#EA580C", "#F97316")
TEXT_MUTED  = ("#64748B", "#94A3B8") # Slate 500 for light, Slate 400 for dark
TEXT_DIM    = ("#94A3B8", "#475569")
TEXT_BLACK  = ("#0F172A", "#F8FAFC") # Inverted for text
