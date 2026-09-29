"""文件写入工具。"""
import os
import tempfile
from pathlib import Path


def atomic_write_text(filepath, text: str, encoding: str = "utf-8") -> None:
    """先写同目录临时文件再替换，避免写到一半崩溃时损坏原文件。"""
    target = Path(filepath)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
        os.replace(temp_path, target)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
