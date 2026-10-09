import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY")

DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "IoT_ Database.accdb"))
DATABASE_DRIVER = os.getenv(
    "DATABASE_DRIVER",
    "{Microsoft Access Driver (*.mdb, *.accdb)}",
)


def database_connection_string() -> str:
    return f"DRIVER={DATABASE_DRIVER};DBQ={DATABASE_PATH};"


SERIAL_PORT = os.getenv("SERIAL_PORT", "COM4")
BAUD_RATE = int(os.getenv("BAUD_RATE", "9600"))
