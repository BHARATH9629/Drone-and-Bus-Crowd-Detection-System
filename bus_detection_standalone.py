"""Convenience launcher for the Smart Drone & Bus Crowd Detection System."""

from pathlib import Path
import runpy

ROOT_DIR = Path(__file__).resolve().parent
APP_DETECTOR = ROOT_DIR / "drone_dashboard" / "bus_detection.py"
if not APP_DETECTOR.exists():
    APP_DETECTOR = ROOT_DIR / "bus_dashboard" / "bus_detection.py"

if __name__ == "__main__":
    runpy.run_path(str(APP_DETECTOR), run_name="__main__")
