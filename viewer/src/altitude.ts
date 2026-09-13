/**
 * 高度から色を作る。
 *
 * 刻みは等間隔にしない。実データの気圧高度は中央値 14,050 ft、p90 36,000 ft、
 * 最大 47,050 ft で、さらに16%が接地（0 ft）という強い偏りがある。
 * 0〜45,000 ft を均等に塗ると、離着陸まわりの低空がほぼ同じ色に潰れて
 * 進入経路の層が読めない。低空側に刻みを寄せる。
 *
 * 明度だけで段階を付けないのも要点。発光させると明度差はにじんで潰れるため、
 * 色相を回して区別する（白→黄→橙→桃→青紫→青）。姉妹リポジトリ
 * jma-liden-tile-pipeline の残光配色と同じ判断。
 */
export const ALT_STOPS: Array<[number, [number, number, number]]> = [
  [0, [255, 255, 255]],       // 接地・地上走行
  [1500, [255, 242, 168]],    // 離着陸
  [5000, [255, 194, 71]],     // 上昇・降下の初期
  [10000, [244, 120, 59]],    // 中低空
  [20000, [224, 74, 110]],    // 中高度
  [30000, [150, 80, 190]],    // 巡航の下側
  [38000, [90, 120, 230]],    // 巡航
  [48000, [120, 220, 255]],   // 巡航より上
]

export function altitudeColor(ft: number): [number, number, number] {
  const a = Number.isFinite(ft) ? Math.max(0, ft) : 0
  for (let i = 1; i < ALT_STOPS.length; i++) {
    const [a1, c1] = ALT_STOPS[i - 1]
    const [a2, c2] = ALT_STOPS[i]
    if (a <= a2) {
      const k = (a - a1) / (a2 - a1)
      return [
        Math.round(c1[0] + (c2[0] - c1[0]) * k),
        Math.round(c1[1] + (c2[1] - c1[1]) * k),
        Math.round(c1[2] + (c2[2] - c1[2]) * k),
      ]
    }
  }
  return ALT_STOPS[ALT_STOPS.length - 1][1]
}

/** 凡例のグラデーション（CSS）。地図と凡例の配色をずらさないためここから作る。 */
export function altitudeGradient(): string {
  const last = ALT_STOPS[ALT_STOPS.length - 1][0]
  const stops = ALT_STOPS.map(
    ([a, c]) => `rgb(${c[0]},${c[1]},${c[2]}) ${((a / last) * 100).toFixed(1)}%`,
  )
  return `linear-gradient(to right, ${stops.join(', ')})`
}

/** 凡例の目盛り。非線形なので位置を計算して置く。 */
export const ALT_TICKS = [0, 10000, 20000, 30000, 40000]

export function altitudeTickPosition(ft: number): number {
  return (ft / ALT_STOPS[ALT_STOPS.length - 1][0]) * 100
}

/** 高度(ft) → 地図上の高さ(m)。誇張しないと真上から見たとき層が潰れる。 */
export const ALT_EXAGGERATION = 3
export const FT_TO_M = 0.3048

export function altitudeToMeters(ft: number): number {
  return (Number.isFinite(ft) ? Math.max(0, ft) : 0) * FT_TO_M * ALT_EXAGGERATION
}
