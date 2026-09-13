"""観測点からフライト軌跡（LineString）を組み立てて GeoParquet にする。

  uv run python src/build_tracks.py -i work/points -o work/tracks.parquet

1フライトの単位は `(icao, UTC日, leg)`。leg は元データのフラグ（着陸と離陸の
境目の推定）から fetch_traces.py が振ったもの。

ただし leg だけでは足りない。日本の bbox を出て戻ってきた便は、同じ leg の中に
巨大な穴が開く（実測で最大 82,117秒 ≒ 23時間）。そのまま結ぶと存在しない直線に
なるので、一定以上の時間の飛びでさらに分割する。実測では5分超の飛びは全体の
0.19% しかなく、切って失うものは少ない。

接地中の点は既定では含める。空港の地上走行が見たい場合があるため。
除きたいときは --airborne-only。
"""
import argparse
import os
import sys

import duckdb

# 各頂点の高度を LIST で持たせる。MVT/MLT は2次元しか運べないため、
# 3次元で軌跡を描くにはタイルではなくこの GeoParquet を直接読む必要がある。
QUERY = """
WITH src AS (
    SELECT *,
           CAST(to_timestamp(ts) AT TIME ZONE 'UTC' AS DATE) AS day
    FROM read_parquet($points)
    {ground_filter}
),
gapped AS (
    SELECT *,
           ts - lag(ts) OVER w AS gap,
           lag(lat) OVER w AS prev_lat,
           lag(lon) OVER w AS prev_lon
    FROM src
    WINDOW w AS (PARTITION BY icao, day, leg ORDER BY ts)
),
segmented AS (
    SELECT *,
           sum(CASE WHEN gap IS NULL OR gap > $max_gap THEN 1 ELSE 0 END) OVER (
               PARTITION BY icao, day, leg ORDER BY ts
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS seg,
           -- 区間の先頭では直前の点が別の軌跡に属するので距離を足さない。
           CASE WHEN gap IS NULL OR gap > $max_gap THEN 0.0 ELSE
               6371.0088 * 2 * asin(sqrt(
                   pow(sin(radians(lat - prev_lat) / 2), 2)
                   + cos(radians(prev_lat)) * cos(radians(lat))
                     * pow(sin(radians(lon - prev_lon) / 2), 2)))
           END AS step_km
    FROM gapped
)
SELECT
    icao || '-' || strftime(day, '%Y%m%d') || '-' || leg || '-' || seg AS track_id,
    icao,
    any_value(r)  AS r,
    any_value(t)  AS t,
    -- 便名は区間の途中で変わりうる。最頻値ではなく最初に確定した値を採る。
    first(flight ORDER BY ts) FILTER (flight IS NOT NULL) AS flight,
    day,
    leg,
    seg,
    to_timestamp(min(ts)) AS t_start,
    to_timestamp(max(ts)) AS t_end,
    CAST(max(ts) - min(ts) AS INTEGER) AS dur_s,
    CAST(count(*) AS INTEGER) AS n_points,
    CAST(min(alt_baro) AS INTEGER) AS alt_min,
    CAST(max(alt_baro) AS INTEGER) AS alt_max,
    CAST(count(*) FILTER (on_ground) AS INTEGER) AS n_ground,
    CAST(max(gs) AS REAL) AS gs_max,
    -- 高度は頂点と同じ並びで持つ。null は接地または高度なし。
    list(alt_baro ORDER BY ts) AS alt_path,
    -- 距離は haversine を自前で積む。DuckDB spatial 1.5.5 の
    -- ST_Length_Spheroid / ST_Distance_Spheroid は NaN しか返さないため。
    ROUND(sum(step_km), 3) AS length_km,
    ST_MakeLine(list(ST_Point(lon, lat) ORDER BY ts)) AS geometry
FROM segmented
GROUP BY icao, day, leg, seg
HAVING count(*) >= $min_points
"""


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i', '--points', required=True,
                        help='観測点の Parquet があるディレクトリ、またはファイル')
    parser.add_argument('-o', '--out', required=True, help='出力する GeoParquet')
    parser.add_argument('--max-gap', type=float, default=300.0,
                        help='これを超える時間の飛びで軌跡を分割する秒数（既定: 300）')
    parser.add_argument('--min-points', type=int, default=2,
                        help='軌跡として残す最小の点数（既定: 2）')
    parser.add_argument('--airborne-only', action='store_true',
                        help='接地中の点を除く')
    parser.add_argument('--memory-limit', default='8GB')
    return parser.parse_args()


def main():
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    args = parse_args()

    points = args.points
    if os.path.isdir(points):
        points = os.path.join(points, '*.parquet')

    con = duckdb.connect()
    con.execute('INSTALL spatial; LOAD spatial;')
    con.execute(f"SET memory_limit='{args.memory_limit}'")

    query = QUERY.format(
        ground_filter='WHERE NOT on_ground' if args.airborne_only else '')

    print(f'入力: {points}', file=sys.stderr)
    print(f'分割: {args.max_gap:.0f}秒超の飛び'
          f'{"／接地点は除く" if args.airborne_only else "／接地点も含む"}', file=sys.stderr)

    con.execute(f'CREATE TABLE tracks AS {query}', {
        'points': points,
        'max_gap': args.max_gap,
        'min_points': args.min_points,
    })

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    con.execute("COPY tracks TO $out (FORMAT PARQUET, COMPRESSION ZSTD)", {'out': args.out})

    stats = con.execute("""
        SELECT count(*), count(DISTINCT icao), sum(n_points),
               round(median(length_km), 1), round(median(dur_s) / 60.0, 1)
        FROM tracks
    """).fetchone()
    size = os.path.getsize(args.out) / 2**20
    print(f'完了: 軌跡 {stats[0]:,}本 / 機体 {stats[1]:,}件 / 頂点 {stats[2]:,} / '
          f'{size:.1f} MiB', file=sys.stderr)
    print(f'  距離の中央値 {stats[3]} km、所要の中央値 {stats[4]} 分', file=sys.stderr)


if __name__ == '__main__':
    main()
