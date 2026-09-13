# adsb-flight-data-converter

[ADSB.lol](https://www.adsb.lol/) が公開している航空機の履歴データ（globe_history）を、
読みやすい形式（GISデータ）に変換するプログラムです。

最終的に MapLibre GL JS + deck.gl で高度方向に立体表示することを目標にしています。

![東京上空、飛行機はどこを飛ぶか](docs/reference-tokyo-r80km.jpg)

（上図は本プロジェクトの目標とする成果物の一例です）

## 状況

Phase 0（形式の棚卸し）完了、Phase 1（観測点の抽出）実装済み。

| Phase | 内容 | 状況 |
|---|---|---|
| 0 | 提供物と形式の棚卸し | 完了 |
| 1 | 観測点の抽出 | 実装済 |
| 2 | フライト軌跡（LineString） | 未着手 |
| 3 | メッシュ集計（観測点密度） | 未着手 |
| 4〜6 | GeoParquet / PMTiles / MLT | 未着手 |
| 7 | viewer（3次元表示） | 未着手 |

- 現在地と再開手順: [docs/STATUS.md](docs/STATUS.md)
- 作業方針: [docs/PLAN.md](docs/PLAN.md)
- 元データの形式: [docs/adsb-lol-format.md](docs/adsb-lol-format.md)

## データの入手

ADSB.lol の履歴データは GitHub Releases で配られています。
**1リリース = 1 UTC日**で、`prod-0` は1日あたり 2.15 GiB です。

- https://github.com/adsblol/globe_history_2026

全球ぶんがまとめて1本になっているため、地域を絞ってもダウンロード量は減りません。
本プログラムは展開物をディスクに残さず、ダウンロードしながら絞り込んで書き出します。

## 観測点の抽出（fetch_traces.py）

日次アーカイブから、指定した範囲に入る観測点だけを Parquet に書き出します。
既定の範囲は日本全国です。

```
uv sync
uv run python src/fetch_traces.py --date 2026-04-01 -o work/points
```

| オプション | 内容 |
|---|---|
| `--date` | UTC日付 `YYYY-MM-DD` |
| `--tar` | 手元の tar を読む（動作確認用） |
| `--instance` | `prod-0`（既定）/ `staging-0` / `mlatonly-0` |
| `--bbox` | `lon_min,lat_min,lon_max,lat_max`。既定は日本全国 |
| `--jobs` | ワーカプロセス数 |
| `--max-members` | 読む機体ファイル数の上限（動作確認用） |

### 出力

`<出力ディレクトリ>/<日付>.parquet`。1観測点が1行です。

| 列 | 内容 |
|---|---|
| `icao` | ICAO 24bit アドレス（16進） |
| `r` | 登録記号 |
| `t` | 型式コード |
| `flight` | 便名／コールサイン |
| `ts` | UNIX時刻（秒） |
| `lon` / `lat` | 経度・緯度 |
| `alt_baro` | 気圧高度(ft)。接地中は null |
| `alt_geom` | 幾何高度(ft) |
| `on_ground` | 接地中か |
| `gs` | 対地速度(kt) |
| `track` | 進路(deg)。接地中は機首方位 |
| `baro_rate` | 昇降率(fpm) |
| `source` | 測位のソース種別（`adsb_icao` / `mlat` など） |
| `leg` | その日その機体の中でのフライト通し番号 |
| `stale` | 直前20秒に位置がなかったか |

`leg` は元データのフラグ（着陸と離陸の境目の推定）から作っています。
`(icao, 日付, leg)` が1フライトの単位になります。

DuckDB からそのまま読めます。

```sql
SELECT flight, count(*) FROM 'work/points/2026-04-01.parquet' GROUP BY 1 ORDER BY 2 DESC;
```

## データ使用上の注意

本データセットは ADSB.lol のデータを加工して作成したものです。
ADSB.lol のデータは **ODbL 1.0** で提供されています。

```
Contains information from ADSB.lol, which is made available under ODbL 1.0.
```

## ライセンス

本プログラムは MIT ライセンスで提供されます。

## 免責事項

利用者が当該データを用いて行う一切の行為について何ら責任を負うものではありません。
