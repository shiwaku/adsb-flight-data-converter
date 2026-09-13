// maplibre-gl v6 は default export を持たない。名前付きで取る。
import {
  AttributionControl, Map as MapLibreMap, MapMouseEvent,
  NavigationControl, Popup, addProtocol, setWorkerUrl,
} from 'maplibre-gl'
// maplibre 6 はワーカーの場所を実行時に import.meta.url から決める（同じ階層に
// maplibre-gl-worker.mjs がある前提）。その前提が外れるとワーカーが404になり、
// タイルが1枚も復号されない。症状は「地図が真っ黒なまま load イベントが飛ばず、
// パネルもレイヤーも出ない」。スタイルが背景色1枚でも同じで、しかも
// error イベントすら飛ばないので気づきにくい。
// ?worker&url で Vite にワーカーを別チャンクとして吐かせ、そのURLを渡して回避する。
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { Protocol } from 'pmtiles'
import 'maplibre-gl/dist/maplibre-gl.css'

import { getBasemapStyle } from './basemap'
import { initialTheme, applyThemeAttr } from './theme'
import { createGlow3d } from './glow3d'
import { addLayers, setVisible } from './layers2d'
import { buildPanel } from './ui'
import { ATTRIBUTION, INITIAL_VIEW, TRAIL_OPTIONS } from './config'
import './style.css'

setWorkerUrl(workerUrl)

const protocol = new Protocol()
addProtocol('pmtiles', protocol.tile)

/**
 * deck.gl（@deck.gl/mapbox）との互換のため map.transform を生やす。
 *
 * deck.gl の getViewport は `map.transform.height` を読む。maplibre 6 で
 * transform が `map._camera.transform` へ移ったため、そのままでは
 * 「Cannot read properties of undefined (reading 'height')」で落ちる。
 * deck.gl が触るのは読み取りだけなので、別名を用意すれば足りる。
 */
function exposeTransform(m: MapLibreMap): void {
  const anyMap = m as unknown as { transform?: unknown; _camera?: { transform?: unknown } }
  if (anyMap.transform || !anyMap._camera?.transform) return
  Object.defineProperty(m, 'transform', {
    get: () => (m as unknown as { _camera: { transform: unknown } })._camera.transform,
    configurable: true,
  })
}

// 発光が主役の可視化なので、テーマは既定でダークにする。
// 淡色地図では白い芯が背景に溶けて光って見えない。
const theme = initialTheme()
applyThemeAttr(theme)

const map = new MapLibreMap({
  container: 'map',
  style: getBasemapStyle(theme),
  center: INITIAL_VIEW.center,
  zoom: INITIAL_VIEW.zoom,
  pitch: INITIAL_VIEW.pitch,
  bearing: INITIAL_VIEW.bearing,
  maxPitch: 85,
  attributionControl: false,
})
// MapLibre はタイルやスタイルの失敗を例外にせず error イベントで流す。
// 拾っておかないと「真っ黒な地図」だけが残って原因が分からない。
map.on('error', (e) => {
  console.error('[adsb] map error:', e.error?.message ?? e)
})
window.addEventListener('error', (e) => console.error('[adsb] uncaught:', e.error ?? e.message))
window.addEventListener('unhandledrejection', (e) => console.error('[adsb] rejected:', e.reason))

// 開発時だけ地図をコンソールから触れるようにしておく。
// タイルが出ないときに map.getStyle() や querySourceFeatures を叩いて切り分ける。
if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__map = map

map.addControl(new AttributionControl({ compact: true, customAttribution: ATTRIBUTION }))
map.addControl(new NavigationControl({ visualizePitch: true }), 'top-right')
exposeTransform(map)

const state = {
  cursor: 0,
  trailSeconds: TRAIL_OPTIONS[1].seconds,
  speed: 300,
  playing: false,
  showPoints: true,
  showTracks: true,
  showMesh: false,
  showTracks2d: false,
  brightness: 1,
  min: 0,
  max: 0,
  loaded: 0,
}

map.on('load', () => {
  try {
  addLayers(map)
  setVisible(map, 'mesh-fill', state.showMesh)
  setVisible(map, 'tracks-line', state.showTracks2d)

  const glow = createGlow3d(map)

  const panel = buildPanel(document.getElementById('panel')!, {
    onChange(next) {
      Object.assign(state, next)
      setVisible(map, 'mesh-fill', state.showMesh)
      setVisible(map, 'tracks-line', state.showTracks2d)
      glow.setOptions({
        cursor: state.cursor,
        trailSeconds: state.trailSeconds,
        showPoints: state.showPoints,
        showTracks: state.showTracks,
        brightness: state.brightness,
      })
    },
    state,
  })

  glow.onRangeChange((min, max, count) => {
    const first = state.min === 0
    state.min = min
    state.max = max
    state.loaded = count
    // 最初にデータが入った時点でカーソルを先頭へ置く。
    if (first) state.cursor = min + state.trailSeconds
    panel.update(state)
    glow.setOptions({ cursor: state.cursor })
  })

  // 再生ループ。実時間の経過にスピードを掛けてデータ時間を進める。
  let last = performance.now()
  function tick(now: number): void {
    const dt = (now - last) / 1000
    last = now
    if (state.playing && state.max > state.min) {
      state.cursor += dt * state.speed
      if (state.cursor > state.max) state.cursor = state.min + state.trailSeconds
      glow.setOptions({ cursor: state.cursor })
      panel.update(state)
    }
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)

  // クリックで直近の観測点をポップアップ。deck.gl 側は pickable にしていないので
  // （数十万点の当たり判定は重い）、キャッシュから最近傍を引く。
  map.on('click', (e: MapMouseEvent) => {
    const tolerance = 0.02 * Math.pow(2, 10 - map.getZoom())
    const hit = glow.nearest(e.lngLat.lng, e.lngLat.lat, tolerance)
    if (!hit) return
    const when = new Date(hit.ts * 1000).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' })
    new Popup({ closeButton: true })
      .setLngLat([hit.position[0], hit.position[1]])
      .setHTML(
        `<div class="pp-title">${hit.flight || hit.icao}</div>` +
        `<dl class="pp-dl">` +
        `<dt>機種</dt><dd>${hit.actype || '—'}</dd>` +
        `<dt>高度</dt><dd>${hit.alt.toLocaleString()} ft</dd>` +
        `<dt>時刻</dt><dd>${when}</dd>` +
        `<dt>ICAO</dt><dd>${hit.icao}</dd>` +
        `</dl>`,
      )
      .addTo(map)
  })

  glow.setOptions({
    cursor: state.cursor,
    trailSeconds: state.trailSeconds,
    showPoints: state.showPoints,
    showTracks: state.showTracks,
    brightness: state.brightness,
  })
  } catch (err) {
    console.error('[adsb] レイヤの組み立てに失敗:', err)
    throw err
  }
})
