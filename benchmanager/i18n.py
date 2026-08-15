"""最小限の多言語対応(§8)。

gettext は使わず ``locales/<lang>.toml`` + ``t(key, **kwargs)`` で解決する。

- 既定ロケールは ``BENCHMAN_LANG`` 環境変数、なければ ``ja``
- 指定ロケールにキーが無ければ英語(``en``)へフォールバック
- 英語にも無ければキー文字列そのものを返す(欠落を検知しやすくするため)

ユーザー向け文言(CLI メッセージ、エラー、レポート内ラベル)は
ハードコードせず必ずこのモジュール経由にすること。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

DEFAULT_LANG = "ja"
FALLBACK_LANG = "en"

_ENV_VAR = "BENCHMAN_LANG"

_cache: dict[str, dict[str, str]] = {}
_current_lang: str | None = None


def _locale_dirs() -> list[Path]:
    """locales/ の探索パス。

    インストール後はパッケージ同梱(``benchmanager/locales``)、
    リポジトリ実行時はリポジトリルートの ``locales`` を見る。
    """
    here = Path(__file__).resolve().parent
    return [here / "locales", here.parent / "locales"]


def _flatten(data: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, prefix=f"{full}."))
        else:
            out[full] = str(value)
    return out


def _load(lang: str) -> dict[str, str]:
    if lang in _cache:
        return _cache[lang]
    messages: dict[str, str] = {}
    for directory in _locale_dirs():
        path = directory / f"{lang}.toml"
        if path.is_file():
            with path.open("rb") as fh:
                messages = _flatten(tomllib.load(fh))
            break
    _cache[lang] = messages
    return messages


def get_lang() -> str:
    """現在のロケール。"""
    if _current_lang is not None:
        return _current_lang
    return os.environ.get(_ENV_VAR) or DEFAULT_LANG


def set_lang(lang: str | None) -> None:
    """ロケールを明示設定する(``None`` で環境変数/既定に戻す)。"""
    global _current_lang
    _current_lang = lang


def t(key: str, /, **kwargs) -> str:
    """メッセージキーを解決して整形する。

    ``key`` は位置専用引数。文言側のプレースホルダ名に ``key`` を使えるようにするため。
    """
    lang = get_lang()
    template = _load(lang).get(key)
    if template is None and lang != FALLBACK_LANG:
        template = _load(FALLBACK_LANG).get(key)
    if template is None:
        template = key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        # 文言側のプレースホルダ不整合で落とさない
        return template


def clear_cache() -> None:
    """テスト用: 読み込み済みロケールを破棄する。"""
    _cache.clear()
