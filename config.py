"""Environment settings. Existing Access files are never opened or modified."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SECRET_KEY = os.getenv("SECRET_KEY")
SQLITE_DATABASE = os.getenv("SQLITE_DATABASE", str(BASE_DIR / "instance" / "sentinel.sqlite3"))
SEED_DEMO = os.getenv("SEED_DEMO", "true").lower() == "true"
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "false").lower() == "true"

# Optional standalone serial diagnostic; not used by the simulator.
SERIAL_PORT = os.getenv("SERIAL_PORT", "COM4")
BAUD_RATE = int(os.getenv("BAUD_RATE", "9600"))
