"""Generates build/n9ctrl.ico from the same drawing the tray icon uses."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import desenhar_icone  # noqa: E402

out = ROOT / "build" / "n9ctrl.ico"
out.parent.mkdir(exist_ok=True)
desenhar_icone(None, None).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"icon written to {out}")
