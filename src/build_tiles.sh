#!/usr/bin/env bash
# GeoJSONSeq から PMTiles と MLT（MapLibre Tile）を作る。WSL で動かす。
#
#   bash src/build_tiles.sh work/geojson/mesh.geojsonl work/tiles mesh
#
# 経路:
#   GeoJSONSeq → MBTiles（tippecanoe）
#              ├→ PMTiles            （pmtiles convert）
#              └→ MLT の MBTiles     （encode.jar --mbtiles）
#                   → {z}/{x}/{y}.mlt（explode_mbtiles.py）
#
# MLT には MVT を経由する。参考実装の Encode CLI が MVT を入力に取る
# トランスコーダで、タイル自体は作れないため。
#
# ## タイルを1枚ずつ encode.jar に渡してはいけない
#
# 参考実装は .pbf を1枚ずつ渡すが、それだとタイルごとに JVM が立ち上がり
# 1枚あたり約1秒かかる。実測では 1,992枚の半分で10分を超えた。観測点レイヤは
# 桁違いにタイル数が増えるので、この方式では終わらない。
# コンテナごと `--mbtiles` で渡せば JVM は1回で済み、同じ 1,992枚が 19.6秒で終わる。
#
# `--pmtiles` 入力も CLI にはあるが、tippecanoe が書くメタデータを
# planetiler 側のデシリアライザが解釈できずに落ちる。MBTiles を使うこと。
#
# 前提（WSL Ubuntu で確認済み）:
#   - tippecanoe 2.x
#   - pmtiles CLI
#   - Java 21以上（17では不可）
#   - encode.jar … maplibre-tile-spec から自前でビルドしたもの
#
#     git clone --depth=1 https://github.com/maplibre/maplibre-tile-spec.git ~/mlt-spec
#     cd ~/mlt-spec/java && chmod +x gradlew && ./gradlew cli
#     → java/mlt-cli/build/libs/encode.jar
#
# 環境変数で調整する:
#   MINZOOM / MAXZOOM      ズーム範囲（既定 0〜10）
#   TIPPECANOE_EXTRA       tippecanoe への追加フラグ
#   MLT_ENCODE_JAR         encode.jar の位置
#   MLT_THREADS            encode.jar の並列数（既定 8）
#   SKIP_MLT=1             PMTiles だけ作る
set -e

SRC=${1:?使い方: bash src/build_tiles.sh <入力.geojsonl> <出力ディレクトリ> <レイヤ名>}
OUT=${2:?使い方: bash src/build_tiles.sh <入力.geojsonl> <出力ディレクトリ> <レイヤ名>}
LAYER=${3:?使い方: bash src/build_tiles.sh <入力.geojsonl> <出力ディレクトリ> <レイヤ名>}

JAR=${MLT_ENCODE_JAR:-$HOME/mlt-spec/java/mlt-cli/build/libs/encode.jar}
MINZOOM=${MINZOOM:-0}
MAXZOOM=${MAXZOOM:-10}
THREADS=${MLT_THREADS:-8}

mkdir -p "$OUT"

# tippecanoe は入力のファイル名を source-layer 名にする（2.x の -l は GLOBAL
# フラグなので使わない）。viewer 側の source-layer をレイヤ名に合わせるため、
# レイヤ名どおりの名前で入力を指し直す。
NAMED="$OUT/$LAYER.geojsonl"
if [ "$(realpath "$SRC")" != "$(realpath "$NAMED" 2>/dev/null || echo '')" ]; then
  ln -sf "$(realpath "$SRC")" "$NAMED" 2>/dev/null || cp "$SRC" "$NAMED"
fi

# export_geojsonseq.py が残した一覧を読んで、実数属性の型を固定する。
# tippecanoe が整数値を INT で格納してしまうのを抑えるため。
#
# ただしこれだけでは足りない。実測では -T length_km:float を渡しても
# INT_32 / DOUBLE の混在が残り、8,306枚中190枚が欠けた。決め手は encode.jar 側の
# --coerce-mismatch で、こちらを入れると型エラーは0件になり全枚数が揃う。
TYPES=""
if [ -f "$SRC.floats" ]; then
  # read は末尾に改行がないと EOF で1を返す。set -e に殺されるので || true が要る。
  IFS=',' read -ra COLS < "$SRC.floats" || true
  for col in "${COLS[@]}"; do
    [ -n "$col" ] && TYPES="$TYPES -T $col:float"
  done
  echo "実数として固定する属性:${TYPES//-T /}"
fi

# 間引かない。観測点は密度そのものが情報で、軌跡は経路そのものが情報。
#   -r1  低ズームでの間引き率を1（＝間引かない）。既定2.5だと薄くなる
#   -pf  1タイルあたりの地物数上限を外す
#   -pk  1タイルあたりのサイズ上限（500KB）を外す
echo "=== 1. GeoJSONSeq → MBTiles ==="
# shellcheck disable=SC2086
tippecanoe -Z"$MINZOOM" -z"$MAXZOOM" -r1 -pf -pk --force \
  $TYPES $TIPPECANOE_EXTRA -o "$OUT/$LAYER.mbtiles" "$NAMED" 2>&1 | tail -2

echo "=== 2. MBTiles → PMTiles ==="
rm -f "$OUT/$LAYER.pmtiles"
pmtiles convert "$OUT/$LAYER.mbtiles" "$OUT/$LAYER.pmtiles" 2>&1 | tail -2

if [ "${SKIP_MLT:-0}" = "1" ]; then
  echo "SKIP_MLT=1 のため MLT は作らない"
  ls -lh "$OUT/$LAYER.pmtiles"
  exit 0
fi

[ -f "$JAR" ] || { echo "encode.jar が見つからない: $JAR" >&2; exit 1; }

echo "=== 3. MBTiles → MLT ==="
# FastPFOR/FSST は既定でオフのまま。有効化すると大きく縮むが、
# デコーダ側の実装が揃っていない環境で読めなくなる恐れがあるため参考実装に合わせる。
#
# --coerce-mismatch は必須。同じ属性に小数と整数が混ざったときに型を揃えてくれる。
# 入れないとそのタイルだけ例外で落ち、しかも終了コードは0のまま黙って欠ける。
rm -f "$OUT/$LAYER.mlt.mbtiles"
# 出力はログに落としてから要約する。tail で切ると、スレッドの中で投げられた
# 例外がスタックフレームに押し流されて見えなくなる。
ENCLOG="$OUT/$LAYER.encode.log"
java -jar "$JAR" --mbtiles "$OUT/$LAYER.mbtiles" --dir "$OUT" \
  --outlines ALL --coerce-mismatch -j "$THREADS" > "$ENCLOG" 2>&1 || true
if grep -q 'Exception' "$ENCLOG"; then
  echo "  エンコーダが投げた例外（種類ごと）:"
  sed 's/.*Exception[^:]*: //; s/Feature index [0-9]*/Feature index N/' "$ENCLOG" \
    | grep -vE '^\s*at |^\s*\.\.\.' | sort | uniq -c | sort -rn | head -5 | sed 's/^/    /'
  echo "  全文: $ENCLOG"
fi

echo "=== 4. 枚数の検算 ==="
# encode.jar は1枚のタイルで例外を投げても終了コード0で返る。スレッドプールの中で
# 死ぬだけなので set -e では捕まらない。枚数を突き合わせて自分で気づくしかない。
python3 - "$OUT/$LAYER.mbtiles" "$OUT/$LAYER.mlt.mbtiles" <<'PY'
import sqlite3, sys
count = lambda p: sqlite3.connect(p).execute('SELECT count(*) FROM tiles').fetchone()[0]
mvt, mlt = count(sys.argv[1]), count(sys.argv[2])
print(f'  MVT {mvt:,}枚 / MLT {mlt:,}枚')
if mvt != mlt:
    print(f'  {mvt - mlt:,}枚が欠けている。encode.jar の出力を確認すること', file=sys.stderr)
    sys.exit(1)
print('  一致')
PY

echo "=== 5. MLT を {z}/{x}/{y}.mlt へ展開 ==="
rm -rf "$OUT/$LAYER-mlt"
python3 "$(dirname "$0")/explode_mbtiles.py" "$OUT/$LAYER.mlt.mbtiles" \
  -o "$OUT/$LAYER-mlt" --ext mlt

echo
ls -lh "$OUT/$LAYER.pmtiles" "$OUT/$LAYER.mbtiles" "$OUT/$LAYER.mlt.mbtiles" \
  | awk '{print "  " $9 "  " $5}'
echo "出力: $OUT/$LAYER.pmtiles / $OUT/$LAYER-mlt"
