# 作業方針

## 目標

ADSB.lol の履歴データ（globe_history）を、GIS で扱える形式へ変換する一式を作る。
最終的な見せ方は MapLibre GL JS + deck.gl による3次元表示で、
[jma-earthquake-data-converter](https://github.com/shiwaku/jma-earthquake-data-converter) の構成を踏襲する。

震源の「深さ」を高度に読み替えると、ほぼそのまま当てはまる：

| jma-earthquake-data-converter | 本プロジェクト |
|---|---|
| 震源（点） | ADS-B 観測点（点） |
| 深さ(km) → 地下へ | 高度(ft) → 上空へ |
| 深さで着色 | 高度で着色 |
| MLT + deck.gl ScatterplotLayer | 同じ |
| — | フライト軌跡（LineString） |

## 決まっていること

| 項目 | 決定 |
|---|---|
| 実装 | Python + uv |
| ソース | `prod-0` のみ（staging-0 / mlatonly-0 は使わない） |
| 空間範囲 | 日本全国 bbox で保持し、都市単位の絞り込みは後段で行う |
| 対象期間 | まず1週間（2026-03-31〜04-06 UTC）で Phase 7 まで通し、その後1か月へ延ばす |
| 出力 | GeoParquet / PMTiles / MLT / 軌跡 LineString |

期間を後回しにできるのは、日次アーカイブが独立していて、日ごとの Parquet を
後から足していけるため。範囲を日本全国にしてあるので、
東京 R=80km のような絞り込みで再ダウンロードは発生しない。

## フェーズ

| Phase | 内容 | 状況 |
|---|---|---|
| 0 | 提供物と形式の棚卸し | 完了 → [adsb-lol-format.md](adsb-lol-format.md) |
| 1 | 観測点の抽出（bbox 絞り込み → Parquet） | 完了 |
| 2 | フライト軌跡（leg → LineString） | 完了 |
| 3 | メッシュ集計（観測点密度） | 完了 |
| 4 | GeoParquet | 完了 |
| 5 | PMTiles | 完了 |
| 6 | MLT | 完了 |
| 7 | viewer（MapLibre + deck.gl で3次元表示） | 実装済・確認中 |

## Phase 1 観測点の抽出

`src/fetch_traces.py`。

配布は全球まとめて1日1本で、bbox で絞ってもダウンロード量は減らない。
1日 2.15 GiB、展開すると約12 GiB になるため、展開物をディスクに置く設計は
1か月ぶん（65 GiB / 展開 360 GiB）で破綻する。そこで

```
HTTP ストリーム → tar 逐次読み → gunzip → bbox 判定 → Parquet 追記
```

を1パスで回し、中間ファイルを一切残さない。tar は split 配布なので
2資産を1本のストリームに見せかけて `tarfile` に食わせている。

gunzip と JSON パースが CPU 律速なので、tar を読む本体は1プロセスのまま、
機体ファイルの中身だけをワーカプールへ投げる。

## Phase 2 フライト軌跡

`src/build_tracks.py`。観測点 Parquet → GeoParquet（LineString）。

1フライトの単位は `(icao, UTC日, leg)`。leg は元データのフラグ ビット1
（着陸と離陸の境目の推定）から Phase 1 が振ったもの。

ただし leg だけでは足りない。日本の bbox を出て戻ってきた便は同じ leg の中に
巨大な穴が開く（実測で最大 82,117秒 ≒ 23時間）。**5分超の飛びでさらに分割する**。

決めたこと：

| 論点 | 決定 | 根拠 |
|---|---|---|
| 時間の飛び | 5分超で分割 | 5分超は全区間の0.19%。切って失うものが少ない |
| 接地中の点 | Parquet には残す | `on_ground` で後段から選べる。再抽出が要らない |
| 短すぎる軌跡 | 2点未満を落とす | 1点では LineString にできない。実測39件のみ |

球面距離は haversine を SQL 側で自前計算している。DuckDB spatial 1.5.5 の
`ST_Length_Spheroid` と `ST_Distance_Spheroid` は例外を出さずに NaN を返すため、
使ってはいけない。

各頂点の高度を `alt_path`（LIST）で持たせてある。MVT/MLT は2次元しか運べず、
属性も地物ごとの値しか持てないため、**3次元の軌跡を描くにはタイルではなく
この GeoParquet を直接読む必要がある**。Phase 7 の設計に影響する（後述）。

## Phase 3 メッシュ集計

`docs/reference-tokyo-r80km.jpg` が示している成果物。地域メッシュ単位で
観測点数を数える。参考画像は「1か月10回以上」で閾値を切っている。

`src/build_mesh.py`。刻みは `--level` で 1km / 500m / 250m / 125m を選べる
（3次メッシュを n 回2分割したもの）。既定は 500m。

メッシュコードは、3次メッシュが緯度 1/120 度・経度 1/80 度であることを使って
`floor(lat / 刻み)` と `floor(lon / 刻み)` の2整数から導く。桁を順に剥がす
伝統的な計算より単純で、セルの矩形も同じ2整数から出せる。

刻みの数に注意が要る。1次→2次は8分割だが **2次→3次は10分割**で、合わせて80。
実装では伝統的な計算と4,004点 × 4レベルで突き合わせ、全一致を確認している。

接地中の点は空港で巨大な塊を作る（東京 R=80km の上位メッシュは全て羽田で、
高度中央値 25〜250 ft）。`--airborne-only` で落とせる。

## Phase 4〜6 配信形式

`src/export_geojsonseq.py` → `src/build_tiles.sh` → `src/explode_mbtiles.py`。

```
Parquet → GeoJSONSeq → MBTiles（tippecanoe）
                       ├→ PMTiles         （pmtiles convert）
                       └→ MLT の MBTiles  （encode.jar --mbtiles）
                            → {z}/{x}/{y}.mlt（explode_mbtiles.py）
```

MLT は MVT を経由する。[maplibre-tile-spec](https://github.com/maplibre/maplibre-tile-spec) の
Encode CLI が MVT を入力に取るトランスコーダで、タイル自体は作れないため。
`encode.jar` のビルドには Java 21 以上が要る。

### タイルを1枚ずつ encode.jar に渡してはいけない

参考実装は `.pbf` を1枚ずつ渡しているが、それだとタイルごとに JVM が立ち上がり
**1枚あたり約1秒**かかる。実測で 1,992枚の半分を超えたところで10分を回った。
観測点レイヤはタイル数が桁違いに増えるので、この方式では終わらない。

コンテナごと `--mbtiles` で渡せば JVM は1回で済む。**同じ 1,992枚が 19.6秒**。
約60倍の差がある。

`--pmtiles` 入力も CLI にはあるが、tippecanoe が書くメタデータを planetiler 側の
デシリアライザが解釈できずに落ちる。MBTiles を経由すること。

### 実測（500m メッシュ・1日ぶん・21,295地物・z0-11）

| 出力 | サイズ |
|---|---|
| MBTiles（gzip済み MVT） | 3.9 MiB |
| PMTiles | 3.3 MiB |
| MLT の MBTiles | 4.7 MiB |
| 展開後の MLT 1,992枚 | 4.2 MiB |

MLT のほうが大きいのは、MBTiles が MVT を gzip で持つのに対し MLT は
無圧縮で置いているため。圧縮率の比較にはならない。全体で42秒。

### 数値の型に注意

高度などの数値は必ず DOUBLE にキャストしてから GeoJSONSeq に出す。整数のまま
出すと MVT 内で INT と DOUBLE が混在し、MLT のエンコーダが型エラーで止まる。
`export_geojsonseq.py` がキャストを持っている。

### GeoParquet の切り分け

観測点は件数が多く、WKB のジオメトリ列を足すとファイルが膨らむ。観測点は経度緯度の
列のまま素の Parquet で置き（DuckDB spatial からそのまま読める）、GeoParquet に
するのは軌跡とメッシュだけにしている。

## Phase 7 viewer

`viewer/`。Vite + TypeScript + MapLibre GL JS v6 + deck.gl（`MapboxOverlay`）。

```
npm install
npm run dev     # 開発時は ../work/tiles を /tiles として配信する
```

### 役割分担

| 何を | どこで | 形式 |
|---|---|---|
| 観測点（3D・発光） | deck.gl `ScatterplotLayer` ×3 | MLT |
| 軌跡（3D・残光） | deck.gl `PathLayer` ×2 | 同じ MLT から JS で連結 |
| 軌跡（2D・全期間） | MapLibre `line` | PMTiles |
| メッシュ密度 | MapLibre `fill` | PMTiles |

### 点と軌跡が同じタイルである理由

MVT は整数の x, y しか持たない2次元の仕様で、MLT はその MVT から変換して作る。
つまり**タイルのジオメトリに高度は載らない**（MapLibre 側も `loadGeometry()` で
z を捨てる）。高度は属性で運び、`[lon, lat, 高度]` の組み立ては deck.gl 側で行う。

軌跡を独立したラインレイヤとしてタイル化すると、tippecanoe のクリップと簡素化で
頂点数が変わり、頂点ごとの高度と対応が取れなくなる。観測点に `track_id` を
持たせて JS 側で連結すれば、頂点と高度は必ず一致する。タイルも1式で済む。

### 発光

半径と不透明度を変えた円を3層重ねる（外は大きく薄く、芯は小さく白く）。
構成は姉妹リポジトリ jma-liden-tile-pipeline の落雷表現に合わせている。

ただしあちらは MapLibre の `circle-blur` で暈を作る。あれは2次元専用の
プロパティで deck.gl にはないため、こちらは**加算合成**で置き換えた。
重なった光が足し算で明るくなるので、密集部が自然に白飛びする。
深度書き込みは切る。切ると手前の点が後ろの光を遮り、重ね合わせの濃淡が出ない。

色は明度で段階を付けず色相を回す（白→黄→橙→桃→青紫→青）。発光させると
明度差はにじんで潰れるため。割り当ては高度で、刻みは低空側に寄せてある
（実データは中央値 14,050 ft・16%が接地という偏りがあり、均等に塗ると
離着陸まわりが同じ色に潰れる）。

### 時間の減衰

時刻カーソルを走らせ、カーソル直近を濃く、過去に向かって薄くする。

数十万点の色を毎フレーム JS で塗り直すと間に合わないため、
`DataFilterExtension` で絞り込みと減衰を GPU 側に寄せた
（`filterRange` で窓の外を落とし、`filterSoftRange` で古い側を薄くする）。
カーソルを動かすコストが点の数に依存しなくなる。

軌跡は1本につき1つの値しか持てず GPU 側で窓を切れないので、
カーソルを粗く量子化して、その値が変わったときだけ線を組み直している。

### テーマ

既定はダーク。淡色地図では白い芯が背景に溶けて光って見えない。
ベースマップは地理院淡色スタイルの明度を反転して暗色化したもので、
実装は jma-liden-tile-pipeline の `basemap.ts` をそのまま持ってきている。

## ライセンス

- データは ODbL 1.0。成果物の表示に出典を出す必要がある
- 参考画像と同じ体裁：`Contains information from ADSB.lol, which is made available under ODbL 1.0.`
- 行政区域・空港は国土数値情報（N03・C28）
