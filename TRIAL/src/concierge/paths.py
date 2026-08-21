"""Project-root path helpers."""

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    path = project_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def results_dir() -> Path:
    path = project_root() / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "concierge.db"


def sample_tickets_path() -> Path:
    return data_dir() / "sample_tickets.json"
