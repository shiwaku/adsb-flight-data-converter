"""mbtiles を {z}/{x}/{y}.<拡張子> のファイル群へ展開する。

  uv run python src/explode_mbtiles.py work/tiles/points.mlt.mbtiles -o work/tiles/points-mlt --ext mlt

MapLibre は HTTP 越しに mbtiles を読めないため、静的配信するにはファイルへ
開く必要がある。MLT のエンコーダを `--mbtiles` で動かすと mbtiles が出てくるので、
その後段に使う。

mbtiles の y 座標は TMS（南が0）。XYZ（北が0）へ反転する。
"""
import argparse
import os
import sqlite3
import sys


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('mbtiles')
    parser.add_argument('-o', '--out', required=True, help='出力ディレクトリ')
    parser.add_argument('--ext', default='pbf', help='拡張子（既定: pbf）')
    return parser.parse_args()


def main():
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    args = parse_args()

    conn = sqlite3.connect(args.mbtiles)
    rows = conn.execute(
        'SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles')

    written = 0
    total_bytes = 0
    largest = 0
    per_zoom = {}
    for z, x, tms_y, data in rows:
        y = (1 << z) - 1 - tms_y  # TMS → XYZ
        directory = os.path.join(args.out, str(z), str(x))
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, f'{y}.{args.ext}'), 'wb') as sink:
            sink.write(data)
        written += 1
        total_bytes += len(data)
        largest = max(largest, len(data))
        count, size = per_zoom.get(z, (0, 0))
        per_zoom[z] = (count + 1, size + len(data))

    conn.close()
    print(f'{written:,}枚 / {total_bytes / 2**20:.1f} MiB '
          f'（最大 {largest / 1024:.0f} KiB）', file=sys.stderr)
    for z in sorted(per_zoom):
        count, size = per_zoom[z]
        print(f'  z{z:<3d} {count:7,}枚 {size / 2**20:8.1f} MiB', file=sys.stderr)


if __name__ == '__main__':
    main()
