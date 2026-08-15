"""例外定義。CLI はこれらを捕捉してメッセージのみを表示する。"""

from __future__ import annotations


class BenchmanError(Exception):
    """本ツールの基底例外。メッセージは i18n 済みであること。"""


class ConfigError(BenchmanError):
    """設定ファイルの読み込み・検証エラー(§6.4)。"""


class DriverError(BenchmanError):
    """ベンチマークツールドライバのエラー。"""


class RemoteError(BenchmanError):
    """リモート実行(SSH)のエラー。"""


class MeasurementError(BenchmanError):
    """1 測定点の失敗(リトライ対象)。"""


class MaxLoadInsufficientError(BenchmanError):
    """最大負荷計測で飽和に達しなかった(§4.3 の 3 番目の分岐)。"""


class EnvParamError(BenchmanError):
    """環境パラメータの適用・verify 失敗(§6.5)。"""


class StoreError(BenchmanError):
    """ストレージ層(SQLite / 結果ディレクトリ)のエラー。"""


class ReportError(BenchmanError):
    """レポート生成のエラー。"""
