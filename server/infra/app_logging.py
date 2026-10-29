"""服务端运行日志：控制台 + server/logs/app.log。"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
_configured = False


def log_dir() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def setup_app_logging() -> None:
    """进程内只配一次；uvicorn --reload 子进程会再配一次。"""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 清掉可能重复的 handler（reload）
    root.handlers.clear()

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    fh = RotatingFileHandler(
        log_dir() / "app.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    logging.getLogger("ailearning").info("app logging → %s", log_dir() / "app.log")


def get_logger(name: str = "ailearning") -> logging.Logger:
    return logging.getLogger(name)
