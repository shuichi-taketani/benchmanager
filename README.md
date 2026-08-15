# Benchmark Manager (`benchman`)

ストレージベンチマークツール(Phase 1 では vdbench)をラップし、負荷(IOPS)を変化させながら
測定を自動実行して **IOPS-Latency カーブ** を生成・分析するツールです。

- 測定戦略は「どのツールでも使える汎用戦略」と「vdbench 固有戦略」に分離
- 生データ(vdbench の出力そのもの)が真実。SQLite はそこから何度でも再生成可能
- レポートは Plotly の自己完結 HTML(plotly.js 同梱、オフライン環境で閲覧可)
- 外部ネットワークに依存しない構成。テストは実機・実 vdbench なしで完結

---

## 1. インストール

Python 3.11 以上が必要です。

```bash
# uv がある場合
uv venv
uv pip install -e .

# uv が無い場合
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

開発用(テスト実行)には追加で:

```bash
uv pip install -e ".[dev]"      # pytest, pytest-asyncio
```

インストールすると `benchman` コマンドが使えるようになります。

```bash
benchman --help
```

---

## 2. 設定ディレクトリ `config/` の構成

`benchman run` には **ディレクトリ** を渡します(既定はカレントの `./config`)。
サーバ・ストレージの定義は、テスト条件そのものとは性質が異なる(環境依存で使い回す、
秘匿性の高い接続情報を含む)ため、独立したファイルに分離しています。

```
config/
├── test.toml       # メイン設定: tool, test_suite, strategy, timing, tool_params, matrix, env_params
├── servers.toml    # 負荷サーバ(SSH 接続先)の定義  ← .gitignore 対象にしやすい
└── storage.toml    # 測定対象ストレージの定義
```

この分離により「同じサーバ構成のまま測定対象ストレージだけ差し替える」「サーバ台数を増やす」
といった変更を `test.toml` に触れずに行えます。

個別ファイルを直接指定したい場合は `--test-config` / `--servers-config` / `--storage-config` で上書きできます。

### 2.1 `test.toml`

```toml
[test]
tool = "vdbench"                # vdbench | mock
test_suite = "ontap-a900-nfs"   # 結果ディレクトリ名・レポートの単位になる
description = "AFF A900 NFS ランダムアクセス"

[strategy]
type = "vdbench_bisect"         # range(汎用) | vdbench_linear_descent | vdbench_bisect

# --- 汎用戦略 'range' を使う場合 ---
# [strategy]
# type = "range"
# start_iops = 10000
# end_iops = 100000
# step_iops = 10000

# --- vdbench 固有戦略のパラメータ ---
[strategy.max_load]             # 線形降下 / 2分探索の起点となる最大負荷計測
mode = "measure"                # measure(iorate=max で実測) | manual(設定値を使用)
latency_threshold_ms = 5.0      # 実測 latency がこれ未満なら「負荷不十分」エラー
# manual_iops = 50000           # mode = "manual" のとき必須

[strategy.params]
step_iops = 1000                # linear_descent のみ使用
stop_delta_pct = 10             # 停止条件(latency 変化率 %)
max_iterations = 10             # 測定点数の上限

[timing]
duration_sec = 600              # 測定時間(既定 600)
warmup_sec = 60                 # ウォームアップ(既定 60)
interval_sec = 1                # vdbench の interval

[tool_params]                   # params.toml で定義されたツール固有パラメータ(検証あり)
threads = 256
file_size = "256g"

[tool_params.raw]               # 定義外パラメータの生パススルー(無検証)
# xfersize_dist = "(4k,50,8k,50)"

[matrix]                        # sweepable なツールパラメータ + env_params 名のみ指定可
read_pct   = [100, 70, 0]
block_size = ["4k", "8k", "64k"]
mount_opts = ["default", "nconnect"]

# ---- 環境パラメータ(任意) ----
[env_params.mount_opts]
target = "server"               # server | storage
order = 2                       # 小さいほど外側ループ(切替コストが大きいものを外へ)
values = ["default", "nconnect"]

[env_params.mount_opts.apply.default]
commands = [
  "umount /mnt/bench || true",
  "mount -t nfs -o vers=4.1 stor1:/vol1 /mnt/bench",
]
verify = "mount | grep -q 'vers=4.1'"    # 終了コード 0 を確認してから測定に入る

[env_params.mount_opts.apply.nconnect]
commands = [
  "umount /mnt/bench || true",
  "mount -t nfs -o vers=4.1,nconnect=8 stor1:/vol1 /mnt/bench",
]
verify = "mount | grep -q 'nconnect=8'"
# post_apply = ["other_param"]                  # 変更時に再適用が必要な他パラメータ
# script = "scripts/setup_mount.sh {value}"     # 複雑な手順は外部スクリプトへ委譲
```

`tool_params` に書けるパラメータの一覧は `benchman params vdbench` で確認できます。

### 2.2 `servers.toml`

```toml
[[servers]]
name = "bench1"
host = "bench1.example.com"
user = "bench"
ssh_key = "~/.ssh/id_ed25519"
vdbench_path = "/opt/vdbench"     # vdbench の設置先
workdir = "/tmp/benchman"         # parmfile と出力の一時置き場(既定 /tmp/benchman)

# 複数台構成(Phase 2 以降で同時実行に対応)
# [[servers]]
# name = "bench2"
# ...
```

### 2.3 `storage.toml`

```toml
[storage]
name = "ontap-a900"
luns = ["/mnt/bench/file1", "/mnt/bench/file2"]   # vdbench の sd=... lun になる
description = "AFF A900 / NFSv4.1"

# env_params の target = "storage" から ONTAP CLI 等を実行する場合の接続情報
[storage.management]
host = "a900-mgmt.example.com"
user = "admin"
ssh_key = "~/.ssh/id_ed25519_ontap"
```

### 2.4 設定エラーの読み方

検証エラーは **どのファイルの・何行目の・どのキーが・なぜ** 誤りかを表示します。

```
$ benchman config validate config
エラー: [test.toml] 設定の検証に失敗しました(1 件)
  - キー 'test.tool_name' (test.toml 4 行目): 定義されていないキーです。スペルミスの可能性があります(入力値: vdbench)
```

長時間テストの前に必ず `benchman config validate` を実行してください。

---

## 3. モックドライバでのデモ(実機不要)

`mock` ドライバは合成 IOPS-Latency カーブ(`latency = base + k / (max_iops - iops)`)を返す
疑似ドライバです。実ストレージも vdbench も SSH 接続も不要で、そのまま動きます。

```bash
# 1. 何が実行されるかを確認(dry-run)
benchman plan examples/mock

# 2. 汎用 range 戦略で実行し、HTML レポートまで生成
benchman run examples/mock --report mock-range.html

# 3. vdbench 固有戦略(2分探索)で実行
benchman run examples/mock-bisect --report mock-bisect.html

# 4. 線形降下で実行したい場合は examples/mock-bisect/test.toml の
#    strategy.type を "vdbench_linear_descent" に変更する

# 5. 生データから DB を作り直し、同じレポートを再生成
benchman import results/<結果ディレクトリ> --db rebuilt.sqlite
benchman report mock-range-demo --db rebuilt.sqlite -o mock-range-rebuilt.html
```

生成された HTML をブラウザで開くと、テスト条件ごとの IOPS-Latency カーブが
グリッド状に並びます(ホバーで目標 IOPS / 実測 IOPS / latency、ズーム・パン可)。

---

## 4. vdbench での実行

```bash
benchman config validate config     # まず検証
benchman plan config                # 条件展開と推定所要時間
benchman run config --report a900.html
```

`benchman run` は各測定点について次を行います。

1. 条件に応じた **parmfile を生成** し、SSH で負荷サーバへ転送
2. `vdbench -f parmfile -o output` を実行(1 測定点 = 1 実行。vdbench の curve 機能は使わない)
3. 出力ディレクトリを回収して **生データとして保存**
4. `flatfile.html` の集計行(`avg_*`)と `histogram.html` をパースし、正規化メトリクスへ変換
5. 全条件終了後、生データディレクトリを **インポート** して SQLite を構築

---

## 5. CLI 一覧

| コマンド | 説明 |
|---|---|
| `benchman run <config-dir>` | テスト実行(`--report` で HTML まで生成、`--fail-fast` で初回失敗時に全体停止) |
| `benchman plan <config-dir>` | dry-run(条件展開・環境パラメータ適用順・切替回数・推定所要時間) |
| `benchman config validate <config-dir>` | 3 ファイルすべてを検証 |
| `benchman import <results-dir>` | 結果ディレクトリから DB を再構築 |
| `benchman report <test-suite> [-o out.html]` | HTML レポート生成 |
| `benchman params <tool>` | ツール固有パラメータの一覧 |

共通オプション: `--db`(SQLite パス、既定 `benchman.sqlite`)、`--results-dir`(既定 `results/`)、
`--lang ja|en`、`-v/--verbose`。

---

## 6. 出力されるもの

### 6.1 結果ディレクトリ(生データ = 真実)

```
results/
└── 2026-07-08_143000_ontap-a900-nfs/
    ├── manifest.toml            # テスト条件・環境情報・戦略・timing
    ├── params.toml              # 使用した params.toml のコピー(再現性のため)
    ├── config/                  # 使用した test/servers/storage.toml のコピー
    └── raw/
        ├── vdbench/run-0001/run.toml          # 1 テスト条件
        │   └── m-0001/measurement.toml + output/…   # 1 測定点の生出力
        └── env/                 # 環境パラメータの適用コマンドと verify 出力の証跡
```

通常の実行も内部で「実行 → 生データ保存 → インポート」の経路を通ります。
そのため **パーサーを修正したあとでも `benchman import` で測り直しなしに DB を作り直せます**。

### 6.2 SQLite

メトリクスは縦持ちです(ツール追加・サーバ台数増でもスキーマ不変)。

```
test_suite(id, name, created_at, description, results_dir)
test_run(id, test_suite_id, tool, conditions_json, status, started_at, finished_at, raw_dir, ...)
measurement(id, test_run_id, target_iops, seq, status, started_at, finished_at, ...)
metric(measurement_id, source, name, value, unit)
timeseries_metric(measurement_id, source, name, ts, value)
```

分析時は pandas で pivot して横持ちに変換する想定です。

```python
import sqlite3, pandas as pd
conn = sqlite3.connect("benchman.sqlite")
df = pd.read_sql_query("""
    SELECT m.id AS measurement_id, r.conditions_json, m.target_iops, k.name, k.value
    FROM measurement m JOIN test_run r ON r.id = m.test_run_id
    JOIN metric k ON k.measurement_id = m.id
""", conn)
wide = df.pivot_table(index=["measurement_id", "target_iops"], columns="name", values="value")
```

---

## 7. 測定戦略

| 戦略 | 区分 | 内容 | 停止条件 |
|---|---|---|---|
| `range` | 汎用(全ドライバ) | `start_iops` から `end_iops` まで `step_iops` 刻み(昇順・降順可) | なし(範囲を全点測定) |
| `vdbench_linear_descent` | vdbench 固有 | 最大負荷から `step_iops` ずつ降下 | 直前点との latency 変化率 ≤ `stop_delta_pct`、または `max_iterations` |
| `vdbench_bisect` | vdbench 固有 | 既測定点の隣接区間のうち latency 差が最大の区間の中点を測る(カーブの膝に点を集中) | 全区間の latency 差 ≤ `stop_delta_pct`、または `max_iterations` |

vdbench 固有戦略は **最大負荷計測**(`iorate=max`)を起点にします。

1. `iorate=max` で実行し、実測 IOPS と平均 latency を得る
2. 実測 latency ≥ `latency_threshold_ms` → その実測 IOPS を最大負荷として採用
3. 実測 latency < 閾値 → 「負荷不十分」としてその条件を失敗にし、次の条件へ進む
4. `mode = "manual"` なら計測せず `manual_iops` を使用

汎用 `range` はこの手順を使わず、`start_iops` から直接測定を開始します。

### 失敗時の扱い

- 目標 IOPS に実測が届かない(飽和領域): **実測値で記録**し警告。判定・グラフも実測 IOPS を使用
- 測定点のエラー終了: **1 回リトライ**、再失敗ならその点を失敗として記録し次へ進む(`--fail-fast` で全体停止)
- SSH 切断: 再接続を試み、測定中だった点はリトライ扱い

---

## 8. 開発

```bash
uv pip install -e ".[dev]"
python -m pytest -q          # 実機・ネットワーク不要ですべて通ること
```

ディレクトリ構成:

```
benchmanager/
├── config/      # TOML ロード + pydantic モデル(extra="forbid")
├── drivers/     # ベンチマークツール抽象化
│   ├── base.py          # BenchDriver
│   ├── vdbench/         # parmfile 生成・flatfile パーサー
│   │   ├── params.toml  # ツール固有パラメータ定義
│   │   └── strategies/  # vdbench 固有戦略(max_load / linear_descent / bisect)
│   └── mock/            # 疑似ドライバ(テスト・デモ用)
├── strategy/    # 汎用測定戦略(range)
├── envparams/   # 環境パラメータの適用・検証
├── remote/      # asyncssh / モックによるリモート実行の抽象化
├── store/       # SQLite + 結果ディレクトリのインポート/エクスポート
├── report/      # Plotly HTML レポート
├── engine.py    # マトリクス展開 + 実行のオーケストレーション
└── cli.py
locales/         # ja.toml / en.toml(ユーザー向け文言はすべてここ経由)
```

設計上の約束:

- **`engine.py` は通信層・GUI を一切知らない**(Phase 3 で Agent 化する際にそのまま流用するため)
- 汎用戦略は `strategy/`、ツール固有戦略は `drivers/<tool>/strategies/` に置く
- ユーザー向け文言はハードコードせず `t(key, **kwargs)` 経由にする
- `store/` のインターフェースは狭く保つ(時系列を Parquet + DuckDB へ逃がせるように)

未解決の質問・仕様との差分は [TODO.md](TODO.md) にまとめてあります。
