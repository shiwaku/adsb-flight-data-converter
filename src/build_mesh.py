"""観測点を地域メッシュ単位で集計して GeoParquet にする。

  uv run python src/build_mesh.py -i work/points -o work/mesh_500m.parquet --level 500m

`docs/reference-tokyo-r80km.jpg` が示している成果物にあたる。

## メッシュの決め方

標準地域メッシュは3次メッシュ（約1km）が緯度 30秒 × 経度 45秒。度に直すと
緯度 1/120 度、経度 1/80 度で、1次メッシュ（緯度 2/3 度・経度 1 度）をちょうど
80分割したものになる。

そのため分割レベル n（3次を0とする）のセルは

    緯度の刻み = (1/120) / 2^n
    経度の刻み = (1/80)  / 2^n

で、原点からの通し番号は `floor(lat / 刻み)` と `floor(lon / 刻み)` で直に出る。
メッシュコードもセルの矩形もこの2つの整数から導けるので、
桁を追う伝統的な計算をせずに済む。
"""
import argparse
import os
import sys

import duckdb

# 3次メッシュを0として、何回2分割するか。
LEVELS = {
    '1km': 0,    # 3次メッシュ
    '500m': 1,   # 4次メッシュ（2分の1地域メッシュ）
    '250m': 2,   # 5次メッシュ（4分の1地域メッシュ）
    '125m': 3,   # 6次メッシュ（8分の1地域メッシュ）
}


def mesh_code_sql(level):
    """iy / ix（そのレベルでの通し番号）からメッシュコードを組み立てる SQL。

    3次までの8桁は 1次（2桁+2桁）・2次（1桁+1桁）・3次（1桁+1桁）の並び。

    刻みの数に注意。1次→2次は8分割だが、2次→3次は10分割で、合わせて80。
    したがって1次の中での通し番号を80で割った余りに対し、
    2次の桁は「10で割った商」（0〜7）、3次の桁は「10で割った余り」（0〜9）になる。

    それより細かい桁は、親セルを2×2に割ったときの位置を
    「緯度側の上下 × 2 + 経度側の左右 + 1」で1〜4に符号化したものが続く。
    """
    base = f'(iy >> {level})'
    base_x = f'(ix >> {level})'
    parts = [
        f"lpad(CAST({base} // 80 AS VARCHAR), 2, '0')",
        f"lpad(CAST({base_x} // 80 - 100 AS VARCHAR), 2, '0')",
        f"CAST(({base} % 80) // 10 AS VARCHAR)",
        f"CAST(({base_x} % 80) // 10 AS VARCHAR)",
        f"CAST({base} % 10 AS VARCHAR)",
        f"CAST({base_x} % 10 AS VARCHAR)",
    ]
    # 細分の桁は粗いほうから並べる。レベル j の桁はビット (level - j) を見る。
    for j in range(1, level + 1):
        bit = level - j
        parts.append(
            f"CAST(((iy >> {bit}) % 2) * 2 + ((ix >> {bit}) % 2) + 1 AS VARCHAR)")
    return ' || '.join(parts)


QUERY = """
WITH src AS (
    SELECT * FROM read_parquet($points)
    {ground_filter}
),
celled AS (
    SELECT *,
           CAST(floor(lat / {dlat}) AS BIGINT) AS iy,
           CAST(floor(lon / {dlon}) AS BIGINT) AS ix,
           CAST(to_timestamp(ts) AT TIME ZONE 'UTC' AS DATE) AS day
    FROM src
)
SELECT
    {code} AS mesh_code,
    '{label}' AS mesh_level,
    CAST(count(*) AS INTEGER) AS n_points,
    CAST(count(DISTINCT icao) AS INTEGER) AS n_aircraft,
    CAST(count(DISTINCT (icao, day, leg)) AS INTEGER) AS n_flights,
    CAST(count(DISTINCT day) AS INTEGER) AS n_days,
    CAST(round(median(alt_baro)) AS INTEGER) AS alt_median,
    CAST(min(alt_baro) AS INTEGER) AS alt_min,
    CAST(max(alt_baro) AS INTEGER) AS alt_max,
    CAST(count(*) FILTER (on_ground) AS INTEGER) AS n_ground,
    ST_MakeEnvelope(ix * {dlon}, iy * {dlat},
                    (ix + 1) * {dlon}, (iy + 1) * {dlat}) AS geometry
FROM celled
GROUP BY iy, ix
HAVING count(*) >= $min_count
"""


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i', '--points', required=True,
                        help='観測点の Parquet があるディレクトリ、またはファイル')
    parser.add_argument('-o', '--out', required=True, help='出力する GeoParquet')
    parser.add_argument('--level', default='500m', choices=sorted(LEVELS),
                        help='メッシュの刻み（既定: 500m）')
    parser.add_argument('--min-count', type=int, default=1,
                        help='この観測点数に満たないメッシュを落とす（既定: 1＝落とさない）')
    parser.add_argument('--airborne-only', action='store_true',
                        help='接地中の点を除く。空港の塊を消したいとき')
    parser.add_argument('--memory-limit', default='8GB')
    return parser.parse_args()


def main():
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    args = parse_args()

    level = LEVELS[args.level]
    dlat = f'({1.0 / 120} / {2 ** level})'
    dlon = f'({1.0 / 80} / {2 ** level})'

    points = args.points
    if os.path.isdir(points):
        points = os.path.join(points, '*.parquet')

    con = duckdb.connect()
    con.execute('INSTALL spatial; LOAD spatial;')
    con.execute(f"SET memory_limit='{args.memory_limit}'")

    query = QUERY.format(
        ground_filter='WHERE NOT on_ground' if args.airborne_only else '',
        dlat=dlat, dlon=dlon, code=mesh_code_sql(level), label=args.level)

    print(f'入力: {points}', file=sys.stderr)
    print(f'メッシュ: {args.level}（3次を{level}回2分割）／閾値 {args.min_count}点以上'
          f'{"／接地点は除く" if args.airborne_only else ""}', file=sys.stderr)

    con.execute(f'CREATE TABLE mesh AS {query}', {
        'points': points, 'min_count': args.min_count})

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    con.execute('COPY mesh TO $out (FORMAT PARQUET, COMPRESSION ZSTD)', {'out': args.out})

    stats = con.execute("""
        SELECT count(*), sum(n_points), max(n_points),
               CAST(round(median(n_points)) AS INTEGER),
               CAST(round(quantile_cont(n_points, 0.99)) AS INTEGER)
        FROM mesh
    """).fetchone()
    size = os.path.getsize(args.out) / 2**20
    print(f'完了: メッシュ {stats[0]:,}個 / 観測点 {stats[1]:,} / {size:.1f} MiB',
          file=sys.stderr)
    print(f'  1メッシュあたり 中央値 {stats[3]}点、p99 {stats[4]}点、最大 {stats[2]:,}点',
          file=sys.stderr)


if __name__ == '__main__':
    main()
