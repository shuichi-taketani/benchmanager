# Benchmark Manager 実装指示書(Claude Code向け)

> ストレージベンチマーク自動化ツールの設計仕様。本書は事前の設計議論の合意事項をまとめたもの。
> ツール名は **Benchmark Manager**(CLIコマンド: `benchman`)とする。名称はパッケージ名定数として一箇所で管理すること。

---

## 1. プロジェクト概要

ストレージのベンチマークツール(初期はvdbench)をラップし、負荷(IOPS)を変化させながら測定を自動実行、IOPS-Latencyカーブを生成・分析するツール。将来的にfio/ior、DB系(sysbench/pgbench/swingbench)、オブジェクトストレージ系(COSBench/Warp/s3-benchmark)への対応、GUI、LLM分析まで拡張する。

### フェーズ計画

- **Phase 1 (今回の実装対象)**: vdbench + 汎用測定戦略(range)+ vdbench固有測定戦略(最大負荷計測・線形降下・2分探索)+ SQLite保存 + Plotly HTMLレポート + CLI
- **Phase 2**: fio対応(プラグイン化の実証)、sysstat収集、時系列ビュー、CSV/Excel/PNG/PowerPoint出力
- **Phase 3**: Web GUI(FastAPI)、Agent方式のFW越え実行、キューイング、Slack/LINE/Teams通知、AG Gridデータ編集
- **Phase 4**: DB/オブジェクトストレージ系ツール、ダッシュボード、LLM分析、多言語UI

**Phase 1のみ実装するが、Phase 2以降を阻害しない抽象化(ドライバ、測定戦略、ストレージ層、実行エンジンと通信層の分離)を最初から入れること。**

---

## 2. 技術スタック(決定事項)

| 項目 | 選定 | 備考 |
|---|---|---|
| Python | 3.11以上 | `tomllib` 標準対応のため |
| 設定ファイル | TOML | ローダーは `load_config(path) -> Config` に隔離し拡張子で分岐、将来YAML併用も可能に |
| 設定検証 | pydantic v2 | `extra="forbid"` で未知キーをエラーに |
| CLI | typer | |
| SSH | asyncssh | 複数サーバ同時実行を見据えて非同期 |
| DB | SQLite(標準ライブラリ `sqlite3` または SQLAlchemy Core) | ORMの重い抽象化は不要。将来DuckDB/Parquetへ時系列部を逃がせるようストレージ層を薄く抽象化 |
| グラフ | Plotly(Python側で生成、自己完結HTML出力) | plotly.jsはHTML内に同梱(オフライン環境要件) |
| ロギング | 標準 `logging`、ファイル+コンソール | |
| パッケージ管理 | uv(なければ pip + venv) | `pyproject.toml` ベース |
| テスト | pytest | モックドライバで実機なしにテスト可能にすること(§10) |

外部ネットワークに依存しない構成にすること(将来オフラインのラボ環境で実行される)。

---

## 3. リポジトリ構成

```
benchmanager/
├── pyproject.toml
├── benchmanager/
│   ├── config/            # TOMLロード + pydanticモデル
│   │   ├── loader.py
│   │   └── models.py
│   ├── drivers/           # ベンチマークツール抽象化層
│   │   ├── base.py        # BenchDriver 抽象クラス
│   │   ├── vdbench/
│   │   │   ├── driver.py
│   │   │   ├── params.toml         # ツール固有パラメータ定義(§7)
│   │   │   └── strategies/         # vdbench固有の測定戦略(§4.2, §9)
│   │   │       ├── max_load.py     # 最大負荷探索(linear_descent/bisectが利用)
│   │   │       ├── linear_descent.py
│   │   │       └── bisect.py
│   │   └── mock/          # テスト用疑似ドライバ(§10)
│   ├── strategy/          # 汎用測定戦略(全ドライバ共通)
│   │   └── range.py       # 開始・終了・ステップの汎用戦略
│   ├── envparams/         # 環境パラメータ(mount等)の適用・検証(§6)
│   ├── remote/            # asyncssh によるリモート実行の抽象化
│   ├── store/             # SQLite + 結果ディレクトリのインポート/エクスポート
│   │   ├── schema.py
│   │   └── importer.py
│   ├── report/            # Plotly HTMLレポート生成
│   ├── engine.py          # マトリクス展開 + テスト実行のオーケストレーション
│   └── cli.py
├── locales/               # ja.toml / en.toml(§8。Phase 1はログ・エラー文言のみ)
├── examples/              # サンプル設定TOML
└── tests/
```

**設計原則: `engine.py`(実行エンジン)は通信層・GUIを一切知らないこと。** Phase 3でAgent化するとき、エンジンを WebSocket 層の下にそのまま置ける構造にする(§9)。CLI実行は「ローカルのジョブ定義でエンジンを直接呼ぶ」だけの薄い皮とする。

---

## 4. 測定ロジック(Phase 1の中核)

### 4.1 前提

- vdbenchは**SSH経由でリモートの負荷サーバ上で実行**する(ローカル実行は対象外でよいが、リモート実行層を抽象化しテストではモック化する)
- vdbench自体のcurve機能は**使用しない**。本ツールが1測定点ずつ `iorate` を指定して個別に実行制御する
- デフォルト: ウォームアップ60秒、測定600秒(設定で変更可能)
- テスト条件マトリクス(read/write比率 × ブロックサイズ × 環境パラメータ)の全組み合わせについて、以下の測定シーケンスを実行する

### 4.2 測定戦略(measurement strategy)の設計方針

**重要な区分**: 測定戦略は「どのベンチマークツールでも使える汎用戦略」と「vdbench固有の測定戦略」に分ける。この区分を実装(§9のインターフェース)にもそのまま反映すること。

**汎用戦略: `range`(開始・終了・ステップ)**
- 全ツール共通で使える最も基本的な戦略。`start_iops`・`end_iops`・`step_iops` を指定し、その範囲を等間隔に測定するだけ
- 最大負荷の自動探索や停止条件の判定など、ツール固有の知識を一切必要としない
- fio/ior等、将来ドライバを追加する際もこの戦略はそのまま使える

**vdbench固有戦略: `vdbench_linear_descent`(線形降下)・`vdbench_bisect`(2分探索)**
- vdbenchの `iorate=max` による最大負荷自動計測を起点とする、より高度な戦略
- 「カーブの膝に測定点を集中させる」「latencyの収束を見て自動的に測定を打ち切る」といった判断は、ツールの応答特性への知識が前提になるため汎用戦略には含めず、vdbenchドライバ側のオプション戦略として実装する
- 他ツール(fio等)で同様の適応的戦略が必要になった場合は、そのツール用に個別実装する(汎用化を急がない)

### 4.3 最大負荷の計測(vdbench固有戦略が使用)

`vdbench_linear_descent` / `vdbench_bisect` の起点として、最大負荷を以下の手順で決定する。`range` 戦略ではこの手順は使用しない(start_iopsから直接測定を開始する)。

1. `iorate=max` で実行し、実測IOPSと平均latencyを得る
2. 実測latency ≥ 設定閾値(例: 5.0ms)→ その実測IOPSを最大負荷として採用
3. 実測latency < 閾値 → 「負荷不十分」エラーとしてこの条件のテストを失敗させる(飽和に達していないため)
4. `max_load_mode = "manual"` の場合は計測せず設定値を最大負荷として使用

### 4.4 各戦略の詳細

**`range`(汎用)**
- `start_iops` から `end_iops` まで `step_iops` 刻みで測定(降順・昇順どちらも可)
- 停止条件なし。指定範囲を全点測定して終了
- ツールが対応する `target_iops` 引数を解釈できればよく、ドライバ側の実装負荷が最小

**`vdbench_linear_descent`(vdbench固有・線形降下)**
- 最大負荷(§4.3)から `step_iops`(例: 1000)ずつIOPSを下げて測定
- 停止条件: 直前の測定点とのlatency変化率が `stop_delta_pct`(例: 5%)以下
- 上限: `max_iterations`(例: 10)回

**`vdbench_bisect`(vdbench固有・2分探索)**
- 既測定点(最大負荷点を含む)の隣接区間のうち、latency差分が最大の区間を選び、その中点IOPSで測定
- 停止条件: 全隣接区間のlatency差分が `stop_delta_pct`(例: 10%)以下
- 上限: `max_iterations` 回
- カーブの「膝」周辺に測定点を集中させるのが目的

**共通事項**: latencyは平均latency(read/write混在時はトータル平均)を判定に用いる。パーセンタイル判定は将来オプション(実装不要だが、metricとしてp95等の保存は行う)。

### 4.5 エッジケース・失敗時ポリシー

- 指定IOPSに実測IOPSが届かない場合(飽和領域): 実測値で記録し警告ログを出す。判定・グラフには実測IOPSを使う
- vdbenchプロセスがエラー終了した測定点: **1回リトライ、再失敗でその条件のテストを失敗として記録し、次の条件へ進む**(テスト全体は止めない)。`--fail-fast` オプションで全体停止も選べるようにする
- SSH切断: 再接続を試み、測定中だった点はリトライ扱い

---

## 5. データ格納(決定事項: 縦持ちスキーマ + 生データ保管)

### 5.1 SQLiteスキーマ

```sql
test_suite(id, name, created_at, description)          -- 一連のテスト群
test_run(id, test_suite_id, tool, conditions_json,     -- 1条件のテスト
         status, started_at, finished_at, raw_dir)
measurement(id, test_run_id, target_iops, seq,         -- 1測定点
            status, started_at, finished_at)

-- メトリクスは縦持ち。ツール追加・サーバ台数増でもスキーマ不変
metric(measurement_id, source, name, value REAL, unit TEXT)
  -- source: 'vdbench' | 'sysstat:server1' 等
  -- name:   'iops' | 'latency_avg' | 'latency_max' | 'read_iops' | 'throughput' 等

-- 時系列(Phase 1ではvdbenchのinterval出力を保存)
timeseries_metric(measurement_id, source, name, ts, value)
```

- `conditions_json` にはツールパラメータと環境パラメータを同列に記録(機種間比較・過去比較のため)
- 分析時は pandas で pivot して横持ちに変換する方針

### 5.2 生データディレクトリ

```
results/
└── 2026-07-08_143000_<test-suite-name>/
    ├── manifest.toml            # テスト条件、環境情報、使用したparams.tomlのコピー
    ├── raw/
    │   ├── vdbench/<run>/...    # flatfile, logfile等の生出力
    │   └── env/                 # 環境パラメータ適用コマンドとverify出力の証跡
    └── ...
```

**重要: 通常のテスト実行も内部的に「実行 → 生データ保存 → インポート」の経路を通すこと。** `benchman import <dir>` で任意の結果ディレクトリからDBを再構築できる(パーサー修正後の再パース、他環境の結果取り込みに使う)。SQLiteは生データから何度でも再生成可能、生データが真実、という原則。

---

## 6. 設定ファイル(TOML、config/ディレクトリに分割)

**構成方針**: サーバ(負荷生成側)とストレージ(測定対象)の定義は、テスト条件そのものとは性質が異なる(環境依存で使い回されることが多く、秘匿性の高い接続情報を含む)ため、**メインの設定ファイルとは別の独立したTOMLファイルに分離**する。`benchman run` には `config/` ディレクトリを渡し、内部で以下のファイル群を読み込んで結合する。

```
config/
├── test.toml           # メイン設定: tool, test_suite, strategy, timing, tool_params, matrix, env_params
├── servers.toml        # 負荷サーバ(SSH接続先)の定義
└── storage.toml        # ストレージ(測定対象)の定義
```

この分離により、たとえば「同じサーバ構成のまま測定対象ストレージだけ差し替える」「サーバ台数を増やす」といった変更が、テスト条件を記述した `test.toml` に触れずに行える。`servers.toml` は秘匿情報(SSH鍵パス等)を含むため、`.gitignore` 対象にしやすいという利点もある。

### 6.1 test.toml(メイン設定)

```toml
[test]
tool = "vdbench"
test_suite = "ontap-a900-nfs"

[strategy]
type = "vdbench_bisect"        # range(汎用) | vdbench_linear_descent | vdbench_bisect(vdbench固有、§4.2)

# --- 汎用戦略 'range' を使う場合はこちらを指定 ---
# [strategy]
# type = "range"
# start_iops = 10000
# end_iops = 100000
# step_iops = 10000

# --- vdbench固有戦略のパラメータ ---
[strategy.max_load]            # linear_descent/bisect の起点となる最大負荷計測(§4.3)
mode = "measure"               # measure | manual
latency_threshold_ms = 5.0
# manual_iops = 50000          # mode=manual時

[strategy.params]              # 戦略ごとのパラメータ(linear_descent/bisect共通の形)
step_iops = 1000               # linear_descentのみ使用
stop_delta_pct = 10
max_iterations = 10

[timing]
duration_sec = 600             # デフォルト600
warmup_sec = 60                # デフォルト60

[tool_params]                  # params.toml定義のツール固有パラメータ(§7)
# threads = 32
[tool_params.raw]              # 定義外パラメータの生パススルー
# xfersize_dist = "..."

[matrix]                       # sweepable なツールパラメータ + env_params 名のみ許可
read_pct   = [100, 70, 0]
block_size = ["4k", "8k", "64k"]
# mount_opts = ["default", "nconnect"]

# ---- 環境パラメータ定義(任意) ----
[env_params.mount_opts]
target = "server"              # server | storage
order = 2                      # 小さいほど外側ループ(切替コスト大)。未指定は定義順、ツールパラメータは常に最内
values = ["default", "nconnect"]

[env_params.mount_opts.apply.default]
commands = [
  "umount /mnt/bench || true",
  "mount -t nfs -o vers=4.1 stor1:/vol1 /mnt/bench",
]
verify = "mount | grep -q 'vers=4.1'"

[env_params.mount_opts.apply.nconnect]
commands = [
  "umount /mnt/bench || true",
  "mount -t nfs -o vers=4.1,nconnect=8 stor1:/vol1 /mnt/bench",
]
verify = "mount | grep -q 'nconnect=8'"
# post_apply = ["..."]         # 他パラメータの再適用が必要な場合
# script = "scripts/xxx.sh {value}"  # 複雑な手順は外部スクリプト委譲
```

### 6.2 servers.toml(負荷サーバ定義)

```toml
[[servers]]
name = "bench1"
host = "bench1.example.com"
user = "bench"
ssh_key = "~/.ssh/id_ed25519"
vdbench_path = "/opt/vdbench"

# 複数台構成の例(Phase 2以降で複数サーバ同時実行に対応)
# [[servers]]
# name = "bench2"
# host = "bench2.example.com"
# user = "bench"
# ssh_key = "~/.ssh/id_ed25519"
# vdbench_path = "/opt/vdbench"
```

### 6.3 storage.toml(測定対象ストレージ定義)

```toml
[storage]
name = "ontap-a900"
luns = ["/dev/sdb", "/dev/sdc"]

# ONTAP CLI等をenv_paramsのtarget="storage"から実行する際の接続情報
[storage.management]
host = "a900-mgmt.example.com"
user = "admin"
ssh_key = "~/.ssh/id_ed25519_ontap"
```

**エラーメッセージの明確化**: pydanticでの厳格検証(`extra="forbid"`)により、TOML構文自体は曖昧さが少ないが、キー名の誤りや型不一致は「**どのファイル**の何行目・どのキーが誤りか」を明示したエラーメッセージにすること(3ファイルに分かれるため、ファイル名を含めた特定が重要)。

### 6.4 検証

- 3ファイルそれぞれを読み込み直後にpydanticで厳格検証(`extra="forbid"`、必須キー欠落・型不一致を明確なエラーメッセージで)し、結合後に `strategy.type` と `driver.available_strategies()` の整合性も検証する
- `benchman config validate <config-dir>` サブコマンドを用意(長時間テスト前の事前チェック)。3ファイルすべてを対象に検証する
- `benchman plan <config-dir>`: マトリクス展開結果(全条件の組み合わせ、環境パラメータ適用順、推定所要時間)を表示するdry-run

### 6.5 環境パラメータの実行仕様

- 値の切り替え時に `commands` を順次実行 → `verify` を実行し**終了コード0を確認してから測定に入る**。verify失敗はデフォルトでテスト全体を停止
- `order` に基づきループの入れ子順を決定し、切り替え回数を最小化する
- 適用したコマンドとverifyの出力を `raw/env/` に証跡として保存
- `target = "storage"` もSSH経由実行(ONTAP CLI等)。実行部は抽象化しておく(将来REST API対応の余地)

---

## 7. ツール固有パラメータ定義(params.toml)

ドライバごとに `drivers/<tool>/params.toml` を同梱。GUI(Phase 3)がフォームを動的生成し、CLIでも検証・ヘルプ生成に使う。設定ファイルと形式を統一しTOMLとする。

```toml
meta:
  tool: vdbench
  version: "1.0"

groups:
  - id: workload
    label: { en: Workload, ja: ワークロード }

params:
  - name: read_pct
    group: workload
    type: int                   # int|float|bool|string|choice|size|duration|list
    min: 0
    max: 100
    default: 100
    sweepable: true             # matrix軸に使用可
    label: { en: "Read %", ja: "Read比率(%)" }
    help:  { en: "...", ja: "..." }
    # shown_if: { param: "...", not_equals: "..." }   # 条件付き表示(GUI用)
```

- 設定値ファイル読み込み時に `tool` に対応する params.toml をロードし、`pydantic.create_model` で動的にモデル生成して検証。**CLIとGUIで同一の検証ロジックを共有**する
- `matrix` に指定できるのは `sweepable: true` のパラメータと `env_params` 定義名のみ(検証でエラーに)
- `tool_params.raw` は無検証でドライバへ生渡し(エスケープハッチ)
- 定義から parmfile/jobfile への変換はドライバのPythonコードの責務(TOMLでテンプレート化しない)
- テスト実行時、使用した params.toml を結果ディレクトリへコピー(再現性)

Phase 1では vdbench の主要パラメータ15個程度(threads, xfersize, rdpct, seekpct, openflags 等)を定義する。

---

## 8. 多言語対応(Phase 1では最小限)

- 方針: gettext等は使わず、`locales/ja.toml` `locales/en.toml` + 自前 `t(key, **kwargs)` 関数(英語フォールバック、キー未定義ならキー表示)
- **規律: ユーザー向け文言(CLIメッセージ、エラー、レポート内ラベル)をハードコードせず `t()` 経由にする**こと。Phase 1では ja.toml のみ実文言を書けばよい
- 日付はISO 8601固定、ロケール書式変換はしない
- params.toml の label/help は `{en, ja}` 形式(§7で対応済み)

---

## 9. ドライバ / 測定戦略 / エンジンのインターフェース

```python
class BenchDriver(ABC):
    """ベンチマークツール抽象化。vdbench固有知識をこの外に漏らさない"""
    def generate_config(self, conditions: dict, target_iops: int | None) -> str: ...
    async def run(self, remote: RemoteExecutor, config: str, timing: Timing) -> RawResult: ...
    def parse(self, raw: RawResult) -> list[Metric]:   # 正規化された縦持ちメトリクス
        ...
    # target_iops=None は iorate=max を意味する(最大負荷計測に対応するドライバのみ)

    def available_strategies(self) -> list[str]:
        """このドライバが対応する戦略名のリスト。汎用戦略 'range' は全ドライバ共通で常に含める"""
        ...


class MeasurementStrategy(ABC):
    """測定戦略。次に測るIOPSを決める。ドライバの内部実装を知らない"""
    def next_target(self, history: list[MeasurementResult]) -> int | None:
        """None = 停止条件成立(range戦略では指定範囲を全点測定したら None)"""
```

- `strategy/range.py`: 汎用戦略。`start_iops` / `end_iops` / `step_iops` の3値のみで動作し、全ドライバ共通で利用可能
- `drivers/vdbench/strategies/linear_descent.py`、`drivers/vdbench/strategies/bisect.py`: vdbench固有戦略。§4.3 の最大負荷計測ロジックに依存するため、汎用の `strategy/` ではなく **vdbenchドライバのサブパッケージに置く**こと(将来他ツール用の適応戦略を追加する際も、それぞれのドライバ配下に置く方針とする)
- `engine.py` は `driver.available_strategies()` と設定ファイルの `strategy.type` を突き合わせ、未対応の戦略が指定された場合は起動前にエラーとする

- `engine.py` は「マトリクス展開 → env_params適用(order順) → 最大負荷計測 → strategyループ → store保存」のオーケストレーションのみを行う
- 正規化メトリクス名は定数モジュールで統一: `iops, latency_avg, latency_max, read_iops, write_iops, read_latency, write_latency, throughput_mbps` 等。vdbenchのflatfile出力から少なくともこれらを抽出する

---

## 10. テスト戦略(重要)

**実ストレージ・実vdbenchなしで開発とCIを完結させること。**

- `drivers/mock/`: 疑似ドライバを実装する。設定可能な合成IOPS-Latencyカーブ(例: latency = base + k / (max_iops - iops) のような飽和モデル + ノイズ)を返す。RemoteExecutorもインメモリのモックを用意
- モックドライバで以下を統合テスト:
  - 最大負荷計測の3分岐(採用/負荷不十分エラー/manual)
  - range戦略の範囲展開、vdbench固有戦略(linear_descent/bisect)の停止条件・上限回数
  - マトリクス展開と env_params のループ順・適用回数の最小化
  - 実行→生データ保存→インポート の往復(DB再構築の同一性)
- vdbenchパーサーは実際のflatfile出力のサンプルをテストフィクスチャとして `tests/fixtures/` に置いて単体テスト
- モックドライバはデモ用途(`benchman run examples/mock.toml`)にも使えるようにする
- vdbenchの出力サンプルは~/benchmanager/docs/design/rand-bs4k-read100-iops470000-threads256_20241221-133933/bench/outputに配置。性能情報はsummary.htmlにあり。
- vdbenchの動作に必要なシナリオファイルは~/benchmanager/docs/design/rand-bs4k-read100-iops470000-threads256_20241221-133933/bench/scenarioに配置
---

## 11. レポート(Phase 1)

- Plotlyで自己完結HTML(plotly.js同梱、オフライン閲覧可)を生成
- テスト条件ごとのIOPS-Latencyカーブをグリッド状にサブプロット配置
- ホバーで target_iops / 実測iops / latency_avg をツールチップ表示、ズーム・パン可能
- 各点に `customdata` として measurement_id を埋め込む(Phase 3の外れ値除外UIの布石)
- 失敗した測定点・警告(飽和で目標IOPS未達など)は視覚的に区別

以下はPhase 1では実装しないが、レポートモジュールの構造で考慮しておく:
- 外れ値の自動検出(単調性違反、LOWESS残差、測定内変動係数)と手動除外は「ビュー定義」(exclusion list)としてDBに保存し、生データは不変とする設計
- CSV/Excel(openpyxl)/PNG(kaleido)/PowerPoint出力

---

## 12. CLI(Phase 1で実装するサブコマンド)

```
benchman run <config-dir>           # テスト実行(config-dir = test.toml/servers.toml/storage.tomlを含むディレクトリ)
benchman plan <config-dir>          # dry-run(条件展開・適用順・推定時間)
benchman config validate <config-dir>
benchman import <results-dir>       # 結果ディレクトリからDBへ取り込み
benchman report <test-suite> [-o out.html]
benchman params <tool>              # params.toml からパラメータ一覧表示
```

`<config-dir>` のデフォルトはカレントディレクトリの `config/`。個別ファイルを直接指定したい場合向けに `--test-config` / `--servers-config` / `--storage-config` オプションでの上書きも用意する。

---

## 13. Phase 2以降のための設計制約(実装はしないが壊さないこと)

- **Agent方式**: Phase 3でGUIサーバ(FW外)とAgent(FW内)を分離する。Agent側からのアウトバウンドWSS常時接続 + ロングポーリングフォールバック。ジョブキュー・結果サマリはサーバ、生データはAgentローカル(切断時バッファ兼用、再接続時同期)。**このためエンジンは通信層と完全分離しておく**
- 時系列データ量が問題になったらParquet+DuckDBへ移行できるよう、store層のインターフェースを狭く保つ
- LLM分析(Phase 4)はprovider抽象化(Claude/OpenAI/ローカルLLM)。metric縦持ちからの要約整形が入力になる
- GUIはFastAPI + Plotly.js + AG Grid Community + pandas を予定

---

## 14. Phase 1 受け入れ基準

1. `benchman run` でモックドライバの設定(config-dir)を実行し、汎用戦略 `range` および vdbench固有戦略 `vdbench_linear_descent`/`vdbench_bisect` の3種すべてでIOPS-Latencyカーブが生成され、HTMLレポートが開けること
2. vdbenchドライバが、SSH経由で parmfile 転送 → 実行 → flatfile回収 → パース、の一連を実装済みであること(実機テストは人間が行う。asyncsshのモックで結合テストを通すこと)
3. `benchman import` で結果ディレクトリからDBを再構築でき、`benchman report` で同一レポートが得られること
4. env_params(mount切替)がループ順最適化・verify付きで動作すること(モックのRemoteExecutorで検証)
5. 不正な設定(test.toml/servers.toml/storage.tomlいずれか)に対し、ファイル名・キー名・原因を特定した日本語エラーメッセージが出ること
6. `range` 戦略が vdbench 以外のドライバ抽象(モックドライバで代用)でも動作し、汎用戦略とvdbench固有戦略が実装上分離されていることがコード構造から確認できること
7. pytest がネットワーク・実機なしで全件パスすること
8. README(日本語): インストール、`config/` ディレクトリの構成と各ファイルの書き方、実行例、モックでのデモ手順

## 15. 実装順序の推奨

1. config(pydanticモデル + ローダー + validate)
2. store(スキーマ + importer)
3. モックドライバ + RemoteExecutor抽象
4. strategy(汎用range)+ vdbench固有戦略(max_load / linear_descent / bisect)+ engine
5. report
6. vdbenchドライバ(parmfile生成 / flatfileパーサー)
7. env_params
8. CLI仕上げ + README

各ステップでテストを書きながら進めること。不明点・矛盾を発見したら推測で進めず、TODO.md に記録して質問すること。
