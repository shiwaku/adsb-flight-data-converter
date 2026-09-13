"""ADSB.lol の日次アーカイブから観測点を抜き出して Parquet にする。

  # ネットワークから（1日ぶん、日本全国）
  uv run python src/fetch_traces.py --date 2026-04-01 -o work/points

  # 手元の tar から（動作確認用）
  uv run python src/fetch_traces.py --tar work/head.tar -o work/probe --max-members 2000

配布物は「全球まとめて1日1本」で、bbox で絞ってもダウンロード量は減らない。
そのため展開した中間ファイルは一切残さず、

  HTTP ストリーム → tar 逐次読み → gunzip → bbox 判定 → Parquet 追記

を1パスで回す。ディスクに残るのは出力の Parquet だけ。

tar は split されて .tar.aa / .tar.ab の2資産で配られるので、両者を1本の
ファイルオブジェクトに見せかけて tarfile に食わせる（_ConcatStream）。

CPU律速なのは gunzip と JSON パースで、ダウンロードは相対的に軽い。よって
tar を読む本体は1プロセスのまま、機体ファイルの中身だけをワーカプールへ投げる。
"""
import argparse
import gzip
import io
import multiprocessing as mp
import os
import sys
import tarfile
import time

import orjson
import pyarrow as pa
import pyarrow.parquet as pq
import requests

# 既定の抽出範囲。日本全国が入る矩形。
# 南西諸島から北方領土まで含め、洋上の経路も拾えるよう広めに取る。
JAPAN_BBOX = (122.0, 20.0, 154.0, 46.5)  # lon_min, lat_min, lon_max, lat_max

REPO_TEMPLATE = 'adsblol/globe_history_{year}'

# trace の各要素の位置。README-json.md の定義に対応する。
I_OFFSET, I_LAT, I_LON, I_ALT = 0, 1, 2, 3
I_GS, I_TRACK, I_FLAGS, I_RATE = 4, 5, 6, 7
I_AIRCRAFT, I_SOURCE, I_ALT_GEOM, I_RATE_GEOM = 8, 9, 10, 11

FLAG_STALE = 1  # 直前20秒に位置がなかった（＝間が空いている）
FLAG_NEW_LEG = 2  # 着陸と離陸の境目。ここで軌跡を割る

SCHEMA = pa.schema([
    ('icao', pa.string()),
    ('r', pa.string()),          # 登録記号
    ('t', pa.string()),          # 型式コード
    ('flight', pa.string()),     # 便名／コールサイン（直近値を持ち回る）
    ('ts', pa.float64()),        # UNIX時刻（秒、小数あり）
    ('lon', pa.float64()),
    ('lat', pa.float64()),
    ('alt_baro', pa.int32()),    # 気圧高度(ft)。接地中は null
    ('alt_geom', pa.int32()),    # 幾何高度(ft)
    ('on_ground', pa.bool_()),
    ('gs', pa.float32()),        # 対地速度(kt)
    ('track', pa.float32()),     # 進路(deg)。接地中は機首方位
    ('baro_rate', pa.int32()),   # 昇降率(fpm)
    ('source', pa.string()),     # adsb_icao / mlat / tisb など
    ('leg', pa.int32()),         # その日その機体の中での通し番号
    ('stale', pa.bool_()),
])

# ワーカへ bbox を渡すためのグローバル。Pool の initializer で入れる。
_BBOX = JAPAN_BBOX


def _init_worker(bbox):
    global _BBOX
    _BBOX = bbox


def _as_int(value):
    """null と "ground" を int32 に落とし込む。"""
    if value is None or isinstance(value, str):
        return None
    return int(value)


def parse_member(payload):
    """機体1ファイルぶんの bytes を列指向の dict へ。bbox外なら None。

    先に全点を走査して bbox に1点でも入るか見る、ということはしない。
    入る点だけを残せばよく、二度走査する意味がないため。
    """
    lon_min, lat_min, lon_max, lat_max = _BBOX

    if payload[:2] == b'\x1f\x8b':
        payload = gzip.decompress(payload)
    try:
        doc = orjson.loads(payload)
    except orjson.JSONDecodeError:
        return None

    trace = doc.get('trace')
    if not trace:
        return None

    base = doc.get('timestamp')
    if base is None:
        return None

    icao = doc.get('icao')
    reg = doc.get('r')
    typ = doc.get('t')

    icao_c, r_c, t_c, flight_c = [], [], [], []
    ts_c, lon_c, lat_c = [], [], []
    baro_c, geom_c, ground_c = [], [], []
    gs_c, track_c, rate_c = [], [], []
    src_c, leg_c, stale_c = [], [], []

    leg = 0
    flight = None  # 機体オブジェクトは値が変わったときだけ出るので持ち回る

    for point in trace:
        flags = point[I_FLAGS] or 0
        if flags & FLAG_NEW_LEG:
            leg += 1

        extra = point[I_AIRCRAFT]
        if extra:
            value = extra.get('flight')
            if value:
                flight = value.strip()

        lat = point[I_LAT]
        lon = point[I_LON]
        # bbox 判定は leg と flight の更新より後に置く。範囲外の点を飛ばしても
        # leg 番号と便名の連続性が崩れないようにするため。
        if lat is None or lon is None:
            continue
        if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            continue

        alt = point[I_ALT]
        icao_c.append(icao)
        r_c.append(reg)
        t_c.append(typ)
        flight_c.append(flight)
        ts_c.append(base + point[I_OFFSET])
        lon_c.append(lon)
        lat_c.append(lat)
        baro_c.append(_as_int(alt))
        geom_c.append(_as_int(point[I_ALT_GEOM]) if len(point) > I_ALT_GEOM else None)
        ground_c.append(alt == 'ground')
        gs_c.append(point[I_GS])
        track_c.append(point[I_TRACK])
        rate_c.append(_as_int(point[I_RATE]))
        src_c.append(point[I_SOURCE] if len(point) > I_SOURCE else None)
        leg_c.append(leg)
        stale_c.append(bool(flags & FLAG_STALE))

    if not ts_c:
        return None

    return {
        'icao': icao_c, 'r': r_c, 't': t_c, 'flight': flight_c,
        'ts': ts_c, 'lon': lon_c, 'lat': lat_c,
        'alt_baro': baro_c, 'alt_geom': geom_c, 'on_ground': ground_c,
        'gs': gs_c, 'track': track_c, 'baro_rate': rate_c,
        'source': src_c, 'leg': leg_c, 'stale': stale_c,
    }


def parse_chunk(payloads):
    """まとめて渡された複数ファイルを処理する。

    1ファイルずつワーカへ投げると、平均30KBに対してプロセス間通信の往復が
    重すぎる。数百件ずつまとめて投げる。

    読んだ件数も返す。最後のチャンクは chunk サイズに満たないため、
    呼び出し側で件数を積算できないため。
    """
    return len(payloads), [result for result in map(parse_member, payloads) if result]


class _Writer:
    """列をためてから Parquet へ書く。

    機体1件ごとに write_table すると row group が機体の数だけできる。
    1件あたり数百点しかないので、読む側では row group の統計がまるで効かず、
    ファイルも無駄に膨らむ。まとめてから書く。

    書き込みは `.part` に対して行い、最後まで通ったときだけ本来の名前へ移す。
    途中で落ちた Parquet はフッタがなく読めないが、ファイルとしては存在するため、
    そのまま置くと fetch_range.py の「出力があればその日は飛ばす」判定を
    すり抜けて、欠けたまま先へ進んでしまう。
    """

    ROWS_PER_GROUP = 1_000_000

    def __init__(self, path):
        self.path = path
        self.temp_path = path + '.part'
        self.rows = 0
        self._writer = None
        self._buffer = {name: [] for name in SCHEMA.names}
        self._buffered = 0

    def add(self, columns):
        for name, values in columns.items():
            self._buffer[name].extend(values)
        count = len(columns['ts'])
        self._buffered += count
        self.rows += count
        if self._buffered >= self.ROWS_PER_GROUP:
            self.flush()

    def flush(self):
        if not self._buffered:
            return
        if self._writer is None:
            self._writer = pq.ParquetWriter(self.temp_path, SCHEMA, compression='zstd')
        self._writer.write_table(pa.table(self._buffer, schema=SCHEMA))
        self._buffer = {name: [] for name in SCHEMA.names}
        self._buffered = 0

    def close(self):
        self.flush()
        if self._writer is not None:
            self._writer.close()
            os.replace(self.temp_path, self.path)

    def abandon(self):
        """最後まで行けなかったときに書きかけを消す。"""
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)


class _ConcatStream(io.RawIOBase):
    """複数のレスポンスを順に読んで1本のストリームに見せる。

    tarfile のストリームモード（'r|'）は read() しか呼ばないので、
    seek/tell は実装しなくてよい。
    """

    def __init__(self, opener_list):
        self._openers = list(opener_list)
        self._current = None
        self.bytes_read = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        while True:
            if self._current is None:
                if not self._openers:
                    return 0
                self._current = self._openers.pop(0)()
            chunk = self._current.read(len(buffer))
            if chunk:
                buffer[:len(chunk)] = chunk
                self.bytes_read += len(chunk)
                return len(chunk)
            self._current.close()
            self._current = None


def asset_openers(date, instance, token):
    """その日のリリース資産を順に開くためのクロージャ列を返す。"""
    year = date[:4]
    repo = REPO_TEMPLATE.format(year=year)
    # タグの日付はドット区切り（v2026.04.01-planes-readsb-prod-0）。
    tag = f'v{date.replace("-", ".")}-planes-readsb-{instance}'
    headers = {'Accept': 'application/vnd.github+json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'

    url = f'https://api.github.com/repos/{repo}/releases/tags/{tag}'
    response = requests.get(url, headers=headers, timeout=60)
    if response.status_code == 404:
        raise SystemExit(f'リリースが見つからない: {repo} {tag}')
    response.raise_for_status()

    assets = sorted(response.json()['assets'], key=lambda a: a['name'])
    total = sum(a['size'] for a in assets)

    def make(asset):
        def open_it():
            stream = requests.get(
                asset['url'], headers={**headers, 'Accept': 'application/octet-stream'},
                stream=True, timeout=(30, 300))
            stream.raise_for_status()
            return stream.raw
        return open_it

    return [make(a) for a in assets], total, [a['name'] for a in assets]


def iter_payloads(tar, max_members):
    """tar から機体ファイルの中身だけを取り出す。"""
    count = 0
    for member in tar:
        if not member.isfile():
            continue
        handle = tar.extractfile(member)
        if handle is None:
            continue
        yield handle.read()
        count += 1
        if max_members and count >= max_members:
            return


def chunked(iterable, size):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--date', help='UTC日付 YYYY-MM-DD')
    source.add_argument('--tar', help='手元の tar（動作確認用）')
    parser.add_argument('--instance', default='prod-0',
                        help='prod-0 / staging-0 / mlatonly-0（既定: prod-0）')
    parser.add_argument('-o', '--out', required=True,
                        help='出力ディレクトリ。<date>.parquet を書く')
    parser.add_argument('--bbox', help='lon_min,lat_min,lon_max,lat_max（既定: 日本全国）')
    parser.add_argument('--max-members', type=int, default=0,
                        help='読む機体ファイル数の上限（動作確認用）')
    parser.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 4) - 1),
                        help='ワーカプロセス数')
    parser.add_argument('--chunk', type=int, default=256,
                        help='1回のプロセス間通信でまとめる機体ファイル数')
    return parser.parse_args()


def main():
    # Windows の既定コードページ（cp932）だと進捗表示が化ける。
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    args = parse_args()
    bbox = tuple(float(v) for v in args.bbox.split(',')) if args.bbox else JAPAN_BBOX

    os.makedirs(args.out, exist_ok=True)
    if args.tar:
        label = os.path.splitext(os.path.basename(args.tar))[0]
        openers = [lambda path=args.tar: open(path, 'rb')]
        total_bytes = os.path.getsize(args.tar)
        print(f'入力: {args.tar}（{total_bytes / 2**30:.2f} GiB）', file=sys.stderr)
    else:
        label = args.date
        openers, total_bytes, names = asset_openers(
            args.date, args.instance, os.environ.get('GITHUB_TOKEN') or gh_token())
        print(f'入力: {" + ".join(names)}（{total_bytes / 2**30:.2f} GiB）', file=sys.stderr)

    out_path = os.path.join(args.out, f'{label}.parquet')
    print(f'出力: {out_path}', file=sys.stderr)
    print(f'範囲: {bbox}  ワーカ: {args.jobs}', file=sys.stderr)

    stream = _ConcatStream(openers)
    state = _Writer(out_path)
    members = 0
    kept_members = 0
    started = time.monotonic()

    try:
        with mp.Pool(args.jobs, initializer=_init_worker, initargs=(bbox,)) as pool, \
                tarfile.open(fileobj=stream, mode='r|') as tar:
            chunks = chunked(iter_payloads(tar, args.max_members), args.chunk)
            for read, results in pool.imap_unordered(parse_chunk, chunks):
                members += read
                kept_members += len(results)
                for columns in results:
                    state.add(columns)

                elapsed = time.monotonic() - started
                read_gib = stream.bytes_read / 2**30
                print(f'\r  {read_gib:5.2f}/{total_bytes / 2**30:.2f} GiB  '
                      f'機体 {kept_members:,}/{members:,}  点 {state.rows:,}  '
                      f'{elapsed:.0f}s', end='', file=sys.stderr)
    except BaseException:
        state.abandon()
        print(file=sys.stderr)
        raise

    state.close()
    rows = state.rows
    print(file=sys.stderr)

    elapsed = time.monotonic() - started
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    print(f'完了: {rows:,}点 / 機体 {kept_members:,}件 / '
          f'{size / 2**20:.1f} MiB / {elapsed:.0f}秒', file=sys.stderr)


def gh_token():
    """gh CLI のトークンを借りる。GitHub API はレート制限が厳しいため。"""
    import subprocess
    try:
        result = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True)
        return result.stdout.strip() or None
    except FileNotFoundError:
        return None


if __name__ == '__main__':
    main()
