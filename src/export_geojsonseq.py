"""Parquet 成果物を GeoJSONSeq に書き出す。タイル生成の入口。

  uv run python src/export_geojsonseq.py --kind points -i work/points -o work/geojson/points.geojsonl
  uv run python src/export_geojsonseq.py --kind tracks -i work/tracks.parquet -o work/geojson/tracks.geojsonl
  uv run python src/export_geojsonseq.py --kind mesh   -i work/mesh_500m.parquet -o work/geojson/mesh.geojsonl

tippecanoe はファイル名を source-layer 名にするので、出力名がそのまま
viewer 側の `source-layer` になる。

## 属性を絞る理由

タイルに載せる属性はタイルサイズに直結する。1週間ぶんの観測点は1,500万点を
超えるので、popup と着色に要るものだけに絞る。

## 数値の型に注意

MLT のエンコーダは、同じ属性の中で型が割れていると例外を投げる。
たとえば `length_km` に 0.251 と 2.0 が混ざると

    Property 'length_km' has different type: INT_32 / DOUBLE

で落ち、そのタイルだけが出力されない（終了コードは0のままなので気づきにくい。
実測で軌跡レイヤの 8,306枚中 190枚が黙って欠けた）。

SQL 側で DOUBLE にキャストするだけでは足りない。GeoJSON は 2.0 と書いても
**tippecanoe が整数値を INT として格納する**ため、小数と整数が同居する属性で
型が割れる。tippecanoe に `-T <属性>:float` を渡して型を固定する必要がある。

そのため、このスクリプトは DOUBLE で出した列の一覧を
`<出力>.floats` に書き出す。`build_tiles.sh` がそれを読んで `-T` を組み立てる。
"""
import argparse
import os
import sys

import duckdb

# 観測点。3次元の点群の材料になるので高度が主役。
POINTS = """
SELECT
    icao,
    flight,
    t AS actype,
    -- MLT のエンコーダが INT/DOUBLE の混在で止まるため必ず実数にする。
    CAST(coalesce(alt_baro, 0) AS DOUBLE) AS alt,
    CAST(gs AS DOUBLE) AS gs,
    on_ground,
    strftime(to_timestamp(ts) AT TIME ZONE 'UTC', '%Y-%m-%d %H:%M:%S') AS dt,
    ST_Point(lon, lat) AS geom
FROM read_parquet($src)
{where}
"""

# フライト軌跡。2次元で描く。頂点ごとの高度（alt_path）はタイルに載せられない。
TRACKS = """
SELECT
    track_id,
    icao,
    flight,
    r,
    t AS actype,
    CAST(day AS VARCHAR) AS day,
    strftime(t_start, '%Y-%m-%d %H:%M:%S') AS t_start,
    CAST(dur_s AS DOUBLE) AS dur_s,
    CAST(length_km AS DOUBLE) AS length_km,
    CAST(alt_max AS DOUBLE) AS alt_max,
    CAST(n_points AS DOUBLE) AS n_points,
    geometry AS geom
FROM read_parquet($src)
{where}
"""

# メッシュ集計。観測点密度の面。
MESH = """
SELECT
    mesh_code,
    mesh_level,
    CAST(n_points AS DOUBLE) AS n_points,
    CAST(n_flights AS DOUBLE) AS n_flights,
    CAST(n_aircraft AS DOUBLE) AS n_aircraft,
    CAST(n_days AS DOUBLE) AS n_days,
    CAST(coalesce(alt_median, 0) AS DOUBLE) AS alt_median,
    geometry AS geom
FROM read_parquet($src)
{where}
"""

KINDS = {'points': POINTS, 'tracks': TRACKS, 'mesh': MESH}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kind', required=True, choices=sorted(KINDS))
    parser.add_argument('-i', '--input', required=True,
                        help='Parquet のファイル、またはそれが入ったディレクトリ')
    parser.add_argument('-o', '--out', required=True, help='出力する .geojsonl')
    parser.add_argument('--where', default='',
                        help="追加の絞り込み。例: \"n_points >= 10\"")
    parser.add_argument('--airborne-only', action='store_true',
                        help='観測点のうち接地中のものを除く（--kind points のみ）')
    parser.add_argument('--memory-limit', default='8GB')
    return parser.parse_args()


def main():
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    args = parse_args()

    src = args.input
    if os.path.isdir(src):
        src = os.path.join(src, '*.parquet')

    clauses = []
    if args.where:
        clauses.append(f'({args.where})')
    if args.airborne_only:
        if args.kind != 'points':
            raise SystemExit('--airborne-only は --kind points のときだけ使える')
        clauses.append('NOT on_ground')
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''

    con = duckdb.connect()
    con.execute('INSTALL spatial; LOAD spatial;')
    con.execute(f"SET memory_limit='{args.memory_limit}'")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    if os.path.exists(args.out):
        os.remove(args.out)

    query = KINDS[args.kind].format(where=where)
    print(f'入力: {src}', file=sys.stderr)
    print(f'種別: {args.kind}{("／" + where) if where else ""}', file=sys.stderr)

    con.execute(f'CREATE TABLE export AS {query}', {'src': src})
    count = con.execute('SELECT count(*) FROM export').fetchone()[0]
    con.execute("COPY export TO $out (FORMAT GDAL, DRIVER 'GeoJSONSeq')", {'out': args.out})

    # DOUBLE で出した列を控えておく。tippecanoe に -T <列>:float を渡さないと
    # 整数値が INT として格納され、MLT のエンコーダが型の割れで落ちる。
    floats = [name for name, kind in con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'export'").fetchall() if kind == 'DOUBLE']
    with open(args.out + '.floats', 'w', encoding='utf-8') as sink:
        sink.write(','.join(floats))

    size = os.path.getsize(args.out) / 2**20
    print(f'完了: {count:,}地物 / {size:.1f} MiB / {args.out}', file=sys.stderr)
    print(f'  実数として固定する属性: {", ".join(floats) or "なし"}', file=sys.stderr)


if __name__ == '__main__':
    main()
