# adsb-flight-data-converter

[ADSB.lol](https://www.adsb.lol/) が公開している航空機の履歴データ（globe_history）を、
読みやすい形式（GISデータ）に変換するプログラムです。

観測点・フライト軌跡・メッシュ密度の3つを、GeoParquet / PMTiles / MLT で出力し、
MapLibre GL JS + deck.gl で高度方向に立体表示します。

![東京上空、飛行機はどこを飛ぶか](docs/reference-tokyo-r80km.jpg)

（上図は本プロジェクトの目標とする成果物の一例です）

## 状況

Phase 0〜7 が一通り通り、1週間ぶん（2026-03-31〜04-06 UTC）の成果物を生成済みです。
**viewer の実際の描画はまだ確認できていません**（[#1](https://github.com/shiwaku/adsb-flight-data-converter/issues/1)）。

| Phase | 内容 | 状況 |
|---|---|---|
| 0 | 提供物と形式の棚卸し | 完了 |
| 1 | 観測点の抽出 | 完了 |
| 2 | フライト軌跡（LineString） | 完了 |
| 3 | メッシュ集計（観測点密度） | 完了 |
| 4〜6 | GeoParquet / PMTiles / MLT | 完了 |
| 7 | viewer（3次元表示） | 実装済（描画は未確認） |

- 現在地と再開手順: [docs/STATUS.md](docs/STATUS.md)
- 作業方針と設計判断: [docs/PLAN.md](docs/PLAN.md)
- 元データの形式: [docs/adsb-lol-format.md](docs/adsb-lol-format.md)
- 残タスク: [issues](https://github.com/shiwaku/adsb-flight-data-converter/issues)

## 生成できるもの

1週間ぶん（7日・日本全国）の実測値です。

| データセット | 件数 | GeoParquet | PMTiles | MLT |
|---|---|---|---|---|
| 観測点 | 19,662,844点 | 423 MB | 307 MB | 11,042枚 / 552 MB |
| フライト軌跡 | 75,847本 | 224 MB | 326 MB | 11,281枚 |
| メッシュ密度（500m） | 2,207,711個 | 63 MB | 99 MB | 7,874枚 |

## データの入手

ADSB.lol の履歴データは GitHub Releases で配られています。
**1リリース = 1 UTC日**で、`prod-0` は1日あたり 2.15 GiB です。

- https://github.com/adsblol/globe_history_2026 （年ごとにリポジトリが分かれます）

全球ぶんがまとめて1本になっているため、地域を絞ってもダウンロード量は減りません。
本プログラムは展開物をディスクに残さず、ダウンロードしながら絞り込んで書き出します。

形式の詳細は [docs/adsb-lol-format.md](docs/adsb-lol-format.md) を参照してください。

## 全体の流れ

```
ADSB.lol の日次アーカイブ
  │  fetch_traces.py / fetch_range.py
  ▼
観測点 Parquet（work/points/<日付>.parquet）
  ├─ build_tracks.py ──► フライト軌跡 GeoParquet
  ├─ build_mesh.py   ──► メッシュ密度 GeoParquet
  │
  │  export_geojsonseq.py
  ▼
GeoJSONSeq
  │  build_tiles.sh（tippecanoe → pmtiles convert / encode.jar）
  ▼
PMTiles（2次元配信）+ MLT（3次元表示用）
  │  explode_mbtiles.py
  ▼
{z}/{x}/{y}.mlt
```

## 準備

```
uv sync
```

タイル生成には別途 **tippecanoe 2.x / pmtiles CLI / Java 21以上 / encode.jar** が要ります。
`encode.jar` は [maplibre-tile-spec](https://github.com/maplibre/maplibre-tile-spec) から自前でビルドします。

```
git clone --depth=1 https://github.com/maplibre/maplibre-tile-spec.git ~/mlt-spec
cd ~/mlt-spec/java && chmod +x gradlew && ./gradlew cli
# → java/mlt-cli/build/libs/encode.jar
```

`build_tiles.sh` は WSL / Linux で動かします。

## 1. 観測点の抽出（fetch_traces.py / fetch_range.py）

日次アーカイブから、指定した範囲に入る観測点だけを Parquet に書き出します。
既定の範囲は日本全国です。

```
# 1日ぶん
uv run python src/fetch_traces.py --date 2026-04-01 -o work/points

# 期間を指定して日ごとに回す（取得済みの日は飛ばす）
uv run python src/fetch_range.py --from 2026-03-31 --to 2026-04-06 -o work/points
```

| オプション | 内容 |
|---|---|
| `--date` | UTC日付 `YYYY-MM-DD` |
| `--tar` | 手元の tar を読む（動作確認用） |
| `--instance` | `prod-0`（既定）/ `staging-0` / `mlatonly-0` |
| `--bbox` | `lon_min,lat_min,lon_max,lat_max`。既定は日本全国 |
| `--jobs` | ワーカプロセス数 |
| `--max-members` | 読む機体ファイル数の上限（動作確認用） |

> **`fetch_range.py --concurrency` を上げないでください。**
> 律速はネットワークで、3並列にすると3.4時間かけて1日ぶんも終わりませんでした。
> GitHub 側が同一 IP からの同時ダウンロードを絞っていると見られます。
> 逐次なら1日あたり207〜379秒です。

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

DuckDB からそのまま読めます。

```sql
SELECT flight, count(*) FROM 'work/points/*.parquet' GROUP BY 1 ORDER BY 2 DESC;
```

## 2. フライト軌跡（build_tracks.py）

観測点を1フライト1本の LineString にまとめて GeoParquet にします。

```
uv run python src/build_tracks.py -i work/points -o work/tracks.parquet
```

単位は `(icao, UTC日, leg)`。`leg` は元データのフラグ（着陸と離陸の境目の推定）です。
ただし日本の bbox を出て戻ってきた便は同じ leg に巨大な穴が開くため（実測で最大約23時間）、
**5分を超える時間の飛びでさらに分割**します。

| オプション | 内容 |
|---|---|
| `--max-gap` | 分割する時間の飛び（秒）。既定 300 |
| `--min-points` | 軌跡として残す最小の点数。既定 2 |
| `--airborne-only` | 接地中の点を除く |

各頂点の高度を `alt_path`（LIST）で持たせてあります。MVT/MLT は2次元しか運べないため、
3次元の軌跡を線として描くにはこの GeoParquet を直接読む必要があります。

## 3. メッシュ集計（build_mesh.py）

観測点を地域メッシュ単位で数えて GeoParquet にします。

```
uv run python src/build_mesh.py -i work/points -o work/mesh_500m.parquet --level 500m
```

| オプション | 内容 |
|---|---|
| `--level` | `1km`（3次）/ `500m` / `250m` / `125m`。既定 `500m` |
| `--min-count` | この観測点数に満たないメッシュを落とす |
| `--airborne-only` | 接地中の点を除く |

接地中の点は空港で巨大な塊を作ります（東京 R=80km の上位メッシュはすべて羽田で、
高度中央値 25〜250 ft）。`--airborne-only` で落とせます。

## 4. タイル生成（export_geojsonseq.py → build_tiles.sh）

```
uv run python src/export_geojsonseq.py --kind mesh -i work/mesh_500m.parquet \
  -o work/geojson/mesh.geojsonl --where "n_points >= 10"

bash src/build_tiles.sh work/geojson/mesh.geojsonl work/tiles mesh
```

`--kind` は `points` / `tracks` / `mesh`。
`build_tiles.sh` は MBTiles を経由して PMTiles と MLT の両方を作り、
`{z}/{x}/{y}.mlt` へ展開するところまでやります。

| 環境変数 | 内容 |
|---|---|
| `MINZOOM` / `MAXZOOM` | ズーム範囲。既定 0〜10 |
| `TIPPECANOE_THIN` | 間引き方針。既定 `-r1 -pf -pk`（＝間引かない） |
| `TIPPECANOE_EXTRA` | tippecanoe への追加フラグ |
| `MLT_ENCODE_JAR` | `encode.jar` の位置 |
| `SKIP_MLT=1` | PMTiles だけ作る |

観測点は件数が多いので間引きが要ります。

```
MINZOOM=5 MAXZOOM=11 TIPPECANOE_THIN="--drop-densest-as-needed" \
  bash src/build_tiles.sh work/geojson/points.geojsonl work/tiles points
```

> **タイルを1枚ずつ `encode.jar` に渡してはいけません。**
> タイルごとに JVM が立ち上がり1枚あたり約1秒かかります。
> コンテナごと `--mbtiles` で渡せば JVM は1回で済み、同じ1,992枚が19.6秒で終わります。

## 5. viewer

Vite + TypeScript + MapLibre GL JS v6 + deck.gl。

```
cd viewer
npm install
npm run dev     # http://localhost:5173/
```

開発時は `../work/tiles` を `/tiles` として配信します。
配信先を変える場合は `VITE_TILE_BASE` を設定してください。

| 表示 | 実装 | タイル |
|---|---|---|
| 観測点（3D・発光） | deck.gl `ScatterplotLayer` ×3 | MLT |
| 軌跡（3D・残光） | deck.gl `PathLayer` ×2 | 同じ MLT から JS で連結 |
| 軌跡（2D・全期間） | MapLibre `line` | PMTiles |
| メッシュ密度 | MapLibre `fill` | PMTiles |

時刻カーソルを走らせると、カーソル直近が濃く、過去に向かって薄れます。
色は高度（低空が暖色、巡航が寒色）、濃さは新しさに割り当てています。

設計の詳細と、踏んだ落とし穴は [docs/PLAN.md](docs/PLAN.md) と
[docs/STATUS.md](docs/STATUS.md) にまとめてあります。

## データ使用上の注意

本データセットは ADSB.lol のデータを加工して作成したものです。
ADSB.lol のデータは **ODbL 1.0** で提供されています。

```
Contains information from ADSB.lol, which is made available under ODbL 1.0.
```

## ライセンス

本プログラムは [MITライセンス](LICENSE) で提供されます。

## 免責事項

利用者が当該データを用いて行う一切の行為について何ら責任を負うものではありません。
