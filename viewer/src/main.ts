// maplibre-gl v6 は default export を持たない。名前付きで取る。
import {
  AttributionControl, Map as MapLibreMap, MapMouseEvent,
  NavigationControl, Popup, addProtocol,
} from 'maplibre-gl'
import { Protocol } from 'pmtiles'
import 'maplibre-gl/dist/maplibre-gl.css'

import { getBasemapStyle } from './basemap'
import { initialTheme, applyThemeAttr } from './theme'
import { createGlow3d } from './glow3d'
import { addLayers, setVisible } from './layers2d'
import { buildPanel } from './ui'
import { ATTRIBUTION, INITIAL_VIEW, TRAIL_OPTIONS } from './config'
import './style.css'

const protocol = new Protocol()
addProtocol('pmtiles', protocol.tile)

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
map.addControl(new AttributionControl({ compact: true, customAttribution: ATTRIBUTION }))
map.addControl(new NavigationControl({ visualizePitch: true }), 'top-right')

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
})
