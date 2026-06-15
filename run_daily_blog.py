#!/usr/bin/env python3
"""
run_daily_blog.py  (repo-root launcher shim)
============================================
The daily-blog automation lives at scripts/maintenance/run_daily_blog.py. It was
relocated there by the "organize untracked files" commit (bc3803d), but the
"LeadFlow - Blog Draft" scheduled task still runs `python run_daily_blog.py` from
the repo root — so after the move the task failed with 0x2 (FILE_NOT_FOUND) and the
blog stopped publishing (last post 2026-06-12).

This shim stays at the repo root so the existing scheduled task keeps working without
needing admin rights to edit it. It forwards all CLI arguments to the real script.
"""
import runpy
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "scripts" / "maintenance" / "run_daily_blog.py"

if not TARGET.exists():
    sys.exit(f"run_daily_blog.py shim: target not found at {TARGET}")

# Run the real script in-process as __main__ so argparse sees the forwarded argv
# and __file__ resolves to scripts/maintenance/ (its REPO_ROOT logic depends on it).
sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")
