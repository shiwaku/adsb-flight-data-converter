"""期間を指定して fetch_traces.py を日ごとに回す。

  uv run python src/fetch_range.py --from 2026-03-31 --to 2026-04-06 -o work/points

律速は CPU ではなくネットワーク（単日の実測で 2.15 GiB を 381秒 = 5.8 MB/s、
15ワーカでも CPU は余っていた）。

**並列にしても速くならない。** 3日同時で試したところ、3.4時間かけて1日ぶんも
終わらなかった（23プロセスの CPU 時間の合計が180秒＝ほぼ全部が待ち時間）。
同じ時刻に単独の接続で測ると 2.5 MB/s 出ていたので、回線ではなく
GitHub 側が同一 IP からの同時ダウンロードを絞っていると見られる。
既定を逐次（--concurrency 1）にしてあるのはこのため。

出力がすでにある日は飛ばす。途中で落ちても走り直せば続きから進む。
"""
import argparse
import concurrent.futures
import datetime as dt
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FETCH = os.path.join(HERE, 'fetch_traces.py')


def dates(start, end):
    first = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    if last < first:
        raise SystemExit('--to が --from より前になっている')
    step = dt.timedelta(days=1)
    while first <= last:
        yield first.isoformat()
        first += step


def run_day(date, args):
    out = os.path.join(args.out, f'{date}.parquet')
    if os.path.exists(out):
        return date, 'skip', f'既にある（{os.path.getsize(out) / 2**20:.1f} MiB）'

    command = [sys.executable, FETCH, '--date', date, '-o', args.out,
               '--instance', args.instance, '--jobs', str(args.jobs_per_day)]
    if args.bbox:
        command += [f'--bbox={args.bbox}']

    result = subprocess.run(command, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if result.returncode != 0:
        tail = (result.stderr or '').strip().splitlines()
        return date, 'fail', tail[-1] if tail else f'終了コード {result.returncode}'

    # 進捗は \r で上書きしているので、最後の行だけ取る。
    lines = [line for line in (result.stderr or '').replace('\r', '\n').splitlines() if line.strip()]
    return date, 'ok', lines[-1] if lines else ''


def parse_args():
    cpus = os.cpu_count() or 4
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--from', dest='start', required=True, help='UTC日付 YYYY-MM-DD')
    parser.add_argument('--to', dest='end', required=True, help='UTC日付 YYYY-MM-DD')
    parser.add_argument('-o', '--out', required=True)
    parser.add_argument('--instance', default='prod-0')
    parser.add_argument('--bbox')
    parser.add_argument('--concurrency', type=int, default=1,
                        help='同時に処理する日数（既定: 1）。'
                             '増やしても速くならない実測がある。docstring 参照')
    parser.add_argument('--jobs-per-day', type=int, default=max(1, cpus - 1),
                        help='1日あたりのワーカプロセス数')
    return parser.parse_args()


def main():
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    args = parse_args()
    os.makedirs(args.out, exist_ok=True)

    targets = list(dates(args.start, args.end))
    print(f'{len(targets)}日ぶん（{targets[0]} 〜 {targets[-1]}）'
          f'／同時 {args.concurrency}日 × {args.jobs_per_day}ワーカ', file=sys.stderr)

    done, failed = 0, []
    with concurrent.futures.ThreadPoolExecutor(args.concurrency) as pool:
        futures = {pool.submit(run_day, date, args): date for date in targets}
        for future in concurrent.futures.as_completed(futures):
            date, status, message = future.result()
            done += 1
            if status == 'fail':
                failed.append(date)
            print(f'[{done}/{len(targets)}] {date} {status}: {message}', file=sys.stderr)

    if failed:
        print(f'失敗した日: {", ".join(sorted(failed))}', file=sys.stderr)
        raise SystemExit(1)
    print('全日完了', file=sys.stderr)


if __name__ == '__main__':
    main()
