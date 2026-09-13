import { MapboxOverlay } from '@deck.gl/mapbox'
import { ScatterplotLayer, PathLayer } from '@deck.gl/layers'
import { DataFilterExtension } from '@deck.gl/extensions'
import type { Map as MapLibreMap } from 'maplibre-gl'

import { altitudeColor, altitudeToMeters } from './altitude'
import { POINTS_SOURCE_LAYER } from './config'

/**
 * 観測点を高度方向に立ち上げて発光させる。軌跡も同じデータから引く。
 *
 * ## なぜ点と軌跡が同じソースなのか
 *
 * MVT は整数の x, y しか持たない2次元の仕様で、MLT は MVT からの変換で作って
 * いるため、タイルのジオメトリに高度は載らない（MapLibre 側も loadGeometry() で
 * z を捨てる）。そこで高度は属性で運び、`[lon, lat, 高度]` の組み立てを
 * deck.gl 側で行う。参考実装 jma-earthquake-data-converter が震源の深さで
 * 採っているのと同じ構成。
 *
 * 軌跡を別レイヤのラインとしてタイル化すると、tippecanoe のクリップと簡素化で
 * 頂点数が変わり、頂点ごとの高度と対応が取れなくなる。観測点に track_id を
 * 持たせて JS 側で連結すれば、頂点と高度は必ず一致する。
 *
 * ## 発光の作り方
 *
 * 半径と不透明度を変えた円を3層重ねる。いちばん外は大きく薄く、芯は小さく白く。
 * 参考実装 jma-liden-tile-pipeline は MapLibre の `circle-blur` で暈を作るが、
 * あれは2次元専用のプロパティで deck.gl にはない。代わりに**加算合成**を使う。
 * 重なった光が足し算で明るくなるので、密集部が自然に白飛びする。
 *
 * ## 時間の減衰
 *
 * 「今は濃く、前は薄い」を毎フレーム JS で塗り直すと、数十万点では間に合わない。
 * DataFilterExtension は絞り込みと減衰を GPU 側でやるので、カーソルを動かす
 * コストが点の数に依存しない。`filterRange` で窓の外を落とし、
 * `filterSoftRange` で古い側をなめらかに薄くする。
 */

export interface Obs {
  position: [number, number, number]
  color: [number, number, number]
  ts: number
  trackId: string
  alt: number
  flight: string
  actype: string
  icao: string
}

/** 発光の層構成。外側ほど大きく薄い。芯だけ白にして色は外側に載せる。 */
const GLOW_LEVELS: Array<{
  id: string
  radius: number
  minPixels: number
  maxPixels: number
  alpha: number
  white: boolean
}> = [
  { id: 'glow', radius: 900, minPixels: 3, maxPixels: 18, alpha: 0.16, white: false },
  { id: 'mid', radius: 350, minPixels: 1.5, maxPixels: 7, alpha: 0.35, white: false },
  { id: 'core', radius: 90, minPixels: 0.6, maxPixels: 2.2, alpha: 0.9, white: true },
]

/**
 * 加算合成。重なるほど明るくなる。発光の実体はこれ。
 * 深度書き込みは切る。切らないと手前の点が後ろの光を遮って、
 * 重ね合わせによる濃淡が出ない。
 */
const ADDITIVE = {
  blend: true,
  blendColorOperation: 'add',
  blendColorSrcFactor: 'src-alpha',
  blendColorDstFactor: 'one',
  blendAlphaOperation: 'add',
  blendAlphaSrcFactor: 'one',
  blendAlphaDstFactor: 'one',
  depthWriteEnabled: false,
} as const

export interface Trail {
  path: Array<[number, number, number]>
  color: [number, number, number]
  trackId: string
}

export interface Glow3dOptions {
  /** 残光の窓（秒）。 */
  trailSeconds: number
  /** 現在のカーソル（UNIX秒）。 */
  cursor: number
  /** 軌跡を描くか。 */
  showTracks: boolean
  /** 点群を描くか。 */
  showPoints: boolean
  /** 全体の明るさ。 */
  brightness: number
}

export function createGlow3d(map: MapLibreMap) {
  const overlay = new MapboxOverlay({ interleaved: false, layers: [] })
  map.addControl(overlay)

  // querySourceFeatures はタイルのロード・アンロードで返る集合が変わる。
  // 取得したものを足しこんでいき消さないことで、点の明滅を防ぐ。
  const cache = new Map<string, Obs>()
  let options: Glow3dOptions = {
    trailSeconds: 900,
    cursor: 0,
    showTracks: true,
    showPoints: true,
    brightness: 1,
  }
  let pending = false
  let trails: Trail[] = []
  let trailKey = ''
  let onRange: ((min: number, max: number, count: number) => void) | null = null
  let tsMin = Infinity
  let tsMax = -Infinity

  function collect(): void {
    pending = false
    if (!map.getSource('points')) return
    const features = map.querySourceFeatures('points', { sourceLayer: POINTS_SOURCE_LAYER })
    let added = false
    for (const f of features) {
      const p = f.properties ?? {}
      const g = f.geometry
      if (g?.type !== 'Point') continue
      const [lon, lat] = g.coordinates as [number, number]
      const ts = Number(p.ts)
      if (!Number.isFinite(ts)) continue
      const trackId = String(p.track_id ?? '')
      // タイル境界をまたぐ重複を潰す。track_id と時刻が同じなら同じ観測。
      const key = `${trackId}|${ts}`
      if (cache.has(key)) continue
      const alt = Number(p.alt) || 0
      cache.set(key, {
        position: [lon, lat, altitudeToMeters(alt)],
        color: altitudeColor(alt),
        ts,
        trackId,
        alt,
        flight: String(p.flight ?? ''),
        actype: String(p.actype ?? ''),
        icao: String(p.icao ?? ''),
      })
      if (ts < tsMin) tsMin = ts
      if (ts > tsMax) tsMax = ts
      added = true
    }
    if (added) {
      onRange?.(tsMin, tsMax, cache.size)
      trailKey = '' // 点が増えたら軌跡も組み直す
      render()
    }
  }

  function schedule(): void {
    if (pending) return
    pending = true
    requestAnimationFrame(collect)
  }

  /**
   * 軌跡を組み直す。
   *
   * 点と違って軌跡は GPU 側で窓を切れない（1本につき1つの値しか持てない）ので、
   * 窓に入る点だけを集めて線を引き直す。毎フレームやると重いため、
   * カーソルを粗く量子化して、その値が変わったときだけ組み直す。
   */
  function rebuildTrails(): void {
    const { cursor, trailSeconds } = options
    const quantum = Math.max(5, trailSeconds / 60)
    const key = `${Math.floor(cursor / quantum)}|${trailSeconds}`
    if (key === trailKey) return
    trailKey = key

    const from = cursor - trailSeconds
    const byTrack = new Map<string, Obs[]>()
    for (const o of cache.values()) {
      if (o.ts < from || o.ts > cursor) continue
      const list = byTrack.get(o.trackId)
      if (list) list.push(o)
      else byTrack.set(o.trackId, [o])
    }

    trails = []
    for (const [trackId, list] of byTrack) {
      if (list.length < 2) continue
      list.sort((a, b) => a.ts - b.ts)
      trails.push({
        trackId,
        path: list.map((o) => o.position),
        // 線の色は区間のいちばん新しい点の高度に合わせる。先頭が今の高度。
        color: list[list.length - 1].color,
      })
    }
  }

  function render(): void {
    const { cursor, trailSeconds, showPoints, showTracks, brightness } = options
    const data = [...cache.values()]
    const from = cursor - trailSeconds
    // 古い側 60% から薄れ始める。参考実装の残光カーブに合わせた。
    const soft: [number, number] = [from, from + trailSeconds * 0.6]

    rebuildTrails()

    const layers: unknown[] = []

    if (showTracks) {
      // 線も2層。太く薄い層で暈を作り、細い層を芯にする。
      for (const [id, width, alpha] of [
        ['trail-glow', 260, 0.18],
        ['trail-core', 70, 0.7],
      ] as Array<[string, number, number]>) {
        layers.push(
          new PathLayer<Trail>({
            id,
            data: trails,
            getPath: (d) => d.path,
            getColor: (d) => [d.color[0], d.color[1], d.color[2], Math.round(255 * alpha * brightness)],
            getWidth: width,
            widthMinPixels: id === 'trail-core' ? 0.8 : 2,
            widthMaxPixels: id === 'trail-core' ? 2 : 10,
            capRounded: true,
            jointRounded: true,
            billboard: false,
            parameters: ADDITIVE,
            pickable: false,
          }),
        )
      }
    }

    if (showPoints) {
      for (const level of GLOW_LEVELS) {
        layers.push(
          new ScatterplotLayer<Obs>({
            id: `points-${level.id}`,
            data,
            getPosition: (d: Obs) => d.position,
            getFillColor: (d: Obs) =>
              level.white
                ? [255, 255, 255, Math.round(255 * level.alpha * brightness)]
                : [d.color[0], d.color[1], d.color[2], Math.round(255 * level.alpha * brightness)],
            getRadius: level.radius,
            radiusMinPixels: level.minPixels,
            radiusMaxPixels: level.maxPixels,
            billboard: true,
            antialiasing: false,
            parameters: ADDITIVE,
            pickable: false,
            // 窓の外を落とし、古い側をなめらかに薄くする。どちらも GPU 側。
            getFilterValue: (d: Obs) => d.ts,
            filterRange: [from, cursor],
            filterSoftRange: soft,
            extensions: [new DataFilterExtension({ filterSize: 1 })],
            updateTriggers: { getFillColor: brightness },
          } as never),
        )
      }
    }

    overlay.setProps({ layers: layers as never })
  }

  map.on('sourcedata', (e: { sourceId?: string; sourceDataType?: string }) => {
    if (e.sourceDataType === 'metadata') return
    if (e.sourceId !== 'points') return
    schedule()
  })
  map.on('moveend', schedule)

  return {
    setOptions(next: Partial<Glow3dOptions>): void {
      options = { ...options, ...next }
      render()
    },
    onRangeChange(fn: (min: number, max: number, count: number) => void): void {
      onRange = fn
    },
    /** クリック位置のいちばん近い観測点。ポップアップ用。 */
    nearest(lon: number, lat: number, maxDeg: number): Obs | null {
      const { cursor, trailSeconds } = options
      let best: Obs | null = null
      let bestD = maxDeg * maxDeg
      for (const o of cache.values()) {
        if (o.ts < cursor - trailSeconds || o.ts > cursor) continue
        const dx = o.position[0] - lon
        const dy = o.position[1] - lat
        const d = dx * dx + dy * dy
        if (d < bestD) {
          bestD = d
          best = o
        }
      }
      return best
    },
    refresh: schedule,
  }
}
