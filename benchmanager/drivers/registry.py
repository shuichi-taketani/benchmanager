"""ドライバのレジストリ。

``test.toml`` の ``tool`` からドライバクラスを解決する。Phase 2 で fio を追加する際は
ここに 1 行足すだけで済むようにしておく(プラグイン化の実証点)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 循環インポート回避
    from .base import BenchDriver

#: ツール名 -> "module:ClassName"
_DRIVERS: dict[str, str] = {
    "vdbench": "benchmanager.drivers.vdbench.driver:VdbenchDriver",
    "mock": "benchmanager.drivers.mock.driver:MockDriver",
}


def available_tools() -> list[str]:
    """登録済みツール名。"""
    return sorted(_DRIVERS)


def register_driver(tool: str, target: str) -> None:
    """ドライバを追加登録する(テストや将来のプラグイン用)。"""
    _DRIVERS[tool] = target


def get_driver_class(tool: str) -> type["BenchDriver"]:
    """ツール名からドライバクラスを取得する。

    :raises KeyError: 未登録のツール名
    """
    import importlib

    target = _DRIVERS[tool]
    module_name, _, class_name = target.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)
