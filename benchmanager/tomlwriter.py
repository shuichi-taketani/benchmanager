"""最小限の TOML 書き出し。

標準ライブラリの ``tomllib`` は読み込み専用のため、manifest / run / measurement の
メタ情報を書き出す用途に限定した簡易シリアライザを持つ(外部依存を増やさないため)。

対応: str / int / float / bool / datetime / None(キーごと省略)/ list / dict(入れ子テーブル)。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _escape(text: str) -> str:
    out = []
    for char in text:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return "".join(out)


def _format_key(key: str) -> str:
    if key and all(c.isalnum() or c in "_-" for c in key):
        return key
    return f'"{_escape(key)}"'


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (datetime, date)):
        return f'"{value.isoformat()}"'
    if isinstance(value, Path):
        return f'"{_escape(str(value))}"'
    if isinstance(value, str):
        return f'"{_escape(value)}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(v) for v in value) + "]"
    if value is None:
        return '""'
    return f'"{_escape(str(value))}"'


def _is_table(value: Any) -> bool:
    return isinstance(value, dict)


def _is_table_array(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) > 0
        and all(isinstance(item, dict) for item in value)
    )


def _dump_table(data: dict[str, Any], prefix: str, lines: list[str]) -> None:
    scalars = {k: v for k, v in data.items() if not _is_table(v) and not _is_table_array(v)}
    tables = {k: v for k, v in data.items() if _is_table(v)}
    table_arrays = {k: v for k, v in data.items() if _is_table_array(v)}

    for key, value in scalars.items():
        if value is None:
            continue  # TOML に null はないためキーごと省略する
        lines.append(f"{_format_key(key)} = {_format_value(value)}")

    for key, value in tables.items():
        name = f"{prefix}.{_format_key(key)}" if prefix else _format_key(key)
        lines.append("")
        lines.append(f"[{name}]")
        _dump_table(value, name, lines)

    for key, items in table_arrays.items():
        name = f"{prefix}.{_format_key(key)}" if prefix else _format_key(key)
        for item in items:
            lines.append("")
            lines.append(f"[[{name}]]")
            _dump_table(item, name, lines)


def dumps(data: dict[str, Any]) -> str:
    """dict を TOML 文字列にする。"""
    lines: list[str] = []
    _dump_table(data, "", lines)
    text = "\n".join(lines).strip()
    return text + "\n" if text else ""


def dump(data: dict[str, Any], path: str | Path) -> None:
    """dict を TOML ファイルへ書き出す。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data), encoding="utf-8")
