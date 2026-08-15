# TODO / 仕様書への確認事項

`docs/design/BENCHMAN_SPEC.md` の Phase 1 を実装するにあたり、**推測で決めた点**と
**仕様書と差がある点**を記録します(§15 の指示に従い、質問として残します)。

## A. 仕様の記述が曖昧で、こちらで決めた点(要確認)

### A-1. `params.toml` の記法(§7)
仕様書 §7 の例は YAML 風(`meta:` / `groups:` / `params:` とインデント)で書かれていますが、
本文には「設定ファイルと形式を統一し **TOML** とする」とあります。**TOML の配列テーブル**
(`[meta]` / `[[groups]]` / `[[params]]`)として実装しました。中身のキー構成は §7 の例のままです。

- 該当: `benchmanager/drivers/vdbench/params.toml`, `benchmanager/params.py`

### A-2. `vdbench_bisect` の最初の下側アンカー(§4.4)
2 分探索は「隣接区間」を必要としますが、開始時点では最大負荷点しかありません。
仕様書には最初の 1 点の決め方の記述がないため、以下にしました。

- `[strategy] start_iops` が指定されていればその値
- 未指定なら **最大負荷の 10%**(`DEFAULT_ANCHOR_RATIO`)

**確認したいこと**: この既定でよいか、それとも別の決め方(例: 最大負荷の 50%)が望ましいか。

### A-3. `stop_delta_pct` の「latency 差分」の定義(§4.4)
`vdbench_bisect` の停止条件は「隣接区間の latency 差分が `stop_delta_pct` 以下」ですが、
`%` 指定なので相対値と解釈し、**区間の 2 点のうち大きい方の latency に対する相対差(%)**
としました(0〜100% に収まり直感的なため)。

`vdbench_linear_descent` 側は「直前の測定点との latency 変化率」なので、
**直前の(= 1 つ高い負荷の)点の latency に対する変化率(%)** としています。

### A-4. `env_params` を定義したが `matrix` に書かなかった場合(§6.1)
仕様書の例では `mount_opts` を `env_params` に定義しつつ `matrix` の行はコメントアウトされています。
「定義したのに掃引しない」のか「定義した値すべてを掃引する」のか判断できないため、
**`matrix` に無ければ `env_params.<name>.values` 全体を掃引する**(= 定義そのものが軸になる)
としました。`matrix` に書けば値の絞り込みができます。

**確認したいこと**: 「定義はするが今回は掃引しない(単一値を適用するだけ)」というユースケースが
あるか。あるなら `matrix` 未指定時は `values` の先頭 1 つだけを適用する、等に変更します。

### A-5. `run()` の引数(§9)
§9 のインターフェースは `async def run(self, remote, config, timing) -> RawResult` ですが、
「実行 → 生データ保存 → インポート」(§5.2)を満たすには生出力の保存先が必要なため、
`output_dir: Path` を引数に追加しました。

同様に、`parse()` は §9 どおり `list[Metric]` を返し、時系列は別メソッド
`parse_timeseries(raw) -> list[TimeseriesPoint]` に分けています(§5.1 の
`timeseries_metric` を埋めるため)。

### A-6. モックドライバが vdbench 固有戦略を受け付けること(§10 / 受け入れ基準 1)
受け入れ基準 1 は「モックドライバの設定で 3 戦略すべてを実行できること」を求めているため、
`MockDriver.tool_strategies()` は vdbench 固有戦略名を返します(実装は
`drivers/vdbench/strategies/` に置いたままで、汎用/ツール固有の分離は崩していません)。
モックは vdbench の代役という位置づけです。

### A-7. `histogram.html` に複数の表がある場合
read のみのワークロードでは表が 1 つですが、read/write 混在では複数出力される可能性があります。
**最後の表**を採用しています(通常は合計)。混在時の実出力サンプルがあれば確認したいです。

### A-8. SQLite スキーマへの追加列
§5.1 の列に加えて、実運用で必要になる以下を追加しました(縦持ちの方針は変えていません)。

- `test_suite.results_dir`(同名スイートの再インポート判定に使用)
- `test_run.error` / `test_run.seq`、`measurement.error` / `measurement.raw_dir`

### A-9. 環境パラメータの適用先(複数サーバ)
`target = "server"` の場合、**`servers.toml` の全サーバに対して**コマンドと verify を実行します
(mount 等はサーバごとに必要なため)。1 台目だけに適用すべきケースがあれば教えてください。

## B. Phase 1 の範囲として実装していないもの(仕様どおり)

- パーセンタイル(p95 等)による停止条件判定 — メトリクスとしての保存のみ実装(§4.4)
- 外れ値の自動検出・手動除外 UI — `report` 側に `exclusions` 引数の口だけ用意(§11)
- CSV / Excel / PNG / PowerPoint 出力(§11)
- sysstat 収集・時系列ビュー(Phase 2)
- 複数サーバでの同時実行(Phase 2)。`remote/` は非同期で書いてあるため拡張可能

## C. 実機で確認が必要な項目

- vdbench の起動コマンド。現在は `<vdbench_path>/vdbench -f <parmfile> -o <outputdir>` 固定です
  (`./vdbench` を PATH 無しで直接叩く前提)。ラッパースクリプト経由が必要な環境があれば設定化します
- SFTP による出力ディレクトリの再帰回収(`asyncssh` の `sftp.get(recurse=True)`)の挙動
- `hd=` の `system=` に `servers.toml` の `host` をそのまま使っています。vdbench 側で
  名前解決可能なホスト名が別途必要な場合は、`servers.toml` に `vdbench_system` のような
  項目を足す必要があります
- 大量ホスト構成での parmfile 生成(現在は `servers.toml` × `files_per_host` で sd を生成)
