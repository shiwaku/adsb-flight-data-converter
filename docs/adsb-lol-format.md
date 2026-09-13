# ADSB.lol 履歴データの形式

2026-09-13 に実測した内容。一次情報は [ADSB.lol の Historical data](https://www.adsb.lol/docs/open-data/historical/) と
[wiedehopf/readsb の README-json.md](https://github.com/wiedehopf/readsb/blob/dev/README-json.md)。

## 配布の単位

GitHub Releases で配られる。リポジトリは年ごとに分かれる（`adsblol/globe_history_2026`）。

**1リリース = 1 UTC日 × 1インスタンス**。タグは `v<YYYY-MM-DD>-planes-readsb-<instance>`。

| インスタンス | 1日あたり | 内容 |
|---|---|---|
| `prod-0` | 2.15 GiB | 主フィード。本プロジェクトが使うのはこれ |
| `staging-0` | 2.65 GiB | 別系統。prod-0 と重複するため使うなら重複排除が要る |
| `mlatonly-0` | 0.10 GiB | MLAT のみで測位した機体（ADS-B 非搭載機） |

（2026-04-01 実測値）

資産は 2 GiB 前後で split され `.tar.aa` / `.tar.ab` の2ファイルになる。連結して展開する。

```
mkdir 2026.04.01
cat v2026.04.01-planes-readsb-prod-0.tar.aa v2026.04.01-planes-readsb-prod-0.tar.ab \
  | tar -xf - -C 2026.04.01
```

ライセンスは **ODbL 1.0**。

## アーカイブの中身

```
./traces/<ICAO下2桁>/trace_full_<icao>.json
```

- 機体1機・1日ぶんで1ファイル。ICAO 16進アドレスの下2桁で 256 のディレクトリに分かれる
- **拡張子は `.json` だが実体は gzip**。`file` で見ると `gzip compressed data`
- `~` で始まるファイル名は ICAO アドレスを持たない機体（TIS-B / ADS-R 由来）
- 平均サイズは約 30 KB（gzip）。展開すると約 5.6 倍

ドキュメントには heatmap ディレクトリも含まれるとあるが、prod-0 のアーカイブを実測した範囲では
`./traces/` のみだった。

## trace ファイルの構造

```json
{
  "icao": "780473",
  "r": "B-6359",
  "t": "A320",
  "dbFlags": 0,
  "desc": "AIRBUS A-320",
  "version": "readsb 3.16.6 b985831",
  "timestamp": 1775001600.0,
  "trace": [ [...], [...] ]
}
```

`timestamp` はその日の基準時刻（UNIX秒）。`trace` の各要素は配列で、意味は位置で決まる。

| 位置 | 内容 |
|---|---|
| 0 | `timestamp` からの経過秒 |
| 1 | 緯度 |
| 2 | 経度 |
| 3 | 気圧高度(ft) / 文字列 `"ground"` / null |
| 4 | 対地速度(kt) / null |
| 5 | 進路(deg) / null（接地中は機首方位） |
| 6 | フラグ（ビット） |
| 7 | 昇降率(fpm) / null |
| 8 | 機体オブジェクト / null |
| 9 | 測位のソース種別 / null（2022年以降のファイルのみ） |
| 10 | 幾何高度(ft) / null |
| 11 | 幾何昇降率 / null |

### フラグ（位置6）

| ビット | 意味 |
|---|---|
| 0 | 直前20秒に位置がなかった（間が空いている） |
| 1 | **新しい leg の開始**（着陸と離陸の境目を推定したもの） |
| 2 | 昇降率が気圧ではなく幾何 |
| 3 | 高度が気圧ではなく幾何 |

**ビット1が軌跡分割の根拠になる**。1機体の1日ぶんの trace には複数のフライトが
連続して入っているので、ここで切って「1フライト = 1 LineString」にする。

### 機体オブジェクト（位置8）

値が変わったときだけ入り、大半の点では null。便名 `flight` はここにあるため、
直近値を持ち回って各点に付ける必要がある（`src/fetch_traces.py` の `parse_member` がやっている）。

## 時刻について

アーカイブは **UTC 日**で切られる。JST に直すと 09:00 から翌日 08:59 になる。

`docs/reference-tokyo-r80km.jpg` のキャプションが「2026年3月31日 09:00–4月30日 08:59」と
なっているのは、UTC の 2026-03-31 〜 04-29 の30リリースをそのまま使ったため。
期間をアーカイブ境界に合わせると端数の処理が要らない。
