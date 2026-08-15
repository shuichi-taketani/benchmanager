"""ロギング設定(標準 logging、ファイル + コンソール)。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .const import PACKAGE_NAME

DEFAULT_LOG_FILENAME = "benchman.log"

_CONSOLE_FORMAT = "%(message)s"
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(verbose: bool = False, log_file: str | Path | None = DEFAULT_LOG_FILENAME) -> None:
    """パッケージロガーにコンソールとファイルのハンドラを設定する。"""
    logger = logging.getLogger(PACKAGE_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    logger.addHandler(console)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        logger.addHandler(file_handler)
