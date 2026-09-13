import { ALT_TICKS, altitudeGradient, altitudeTickPosition } from './altitude'
import { SPEED_OPTIONS, TRAIL_OPTIONS } from './config'

export interface PanelState {
  cursor: number
  trailSeconds: number
  speed: number
  playing: boolean
  showPoints: boolean
  showTracks: boolean
  showMesh: boolean
  showTracks2d: boolean
  brightness: number
  min: number
  max: number
  loaded: number
}

function jst(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('ja-JP', {
    timeZone: 'Asia/Tokyo',
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export function buildPanel(
  root: HTMLElement,
  opts: { state: PanelState; onChange: (next: Partial<PanelState>) => void },
) {
  const { state, onChange } = opts

  root.innerHTML = `
    <h1>日本上空の航空機</h1>
    <p class="sub">ADS-B観測点の3次元表示</p>

    <div class="row time">
      <button id="play" class="play" type="button" aria-label="再生">▶</button>
      <div class="clock"><span id="clock">—</span> <span class="tz">JST</span></div>
    </div>
    <input id="seek" class="seek" type="range" min="0" max="1000" value="0" />
    <div class="ends"><span id="t0">—</span><span id="t1">—</span></div>

    <label class="field">残光
      <select id="trail">
        ${TRAIL_OPTIONS.map((o) => `<option value="${o.seconds}">${o.label}</option>`).join('')}
      </select>
    </label>
    <label class="field">速度
      <select id="speed">
        ${SPEED_OPTIONS.map((s) => `<option value="${s}">×${s}</option>`).join('')}
      </select>
    </label>
    <label class="field">明るさ
      <input id="brightness" type="range" min="0.2" max="2.5" step="0.1" value="1" />
    </label>

    <div class="layers">
      <label><input id="l-points" type="checkbox" checked /> 観測点（3D・発光）</label>
      <label><input id="l-tracks" type="checkbox" checked /> 軌跡（3D・残光）</label>
      <label><input id="l-tracks2d" type="checkbox" /> 軌跡（2D・全期間）</label>
      <label><input id="l-mesh" type="checkbox" /> メッシュ密度</label>
    </div>

    <div class="legend">
      <div class="legend-title">高度</div>
      <div class="legend-ramp" style="background:${altitudeGradient()}"></div>
      <div class="legend-ticks">
        ${ALT_TICKS.map((ft) =>
          `<span style="left:${altitudeTickPosition(ft)}%">${ft / 1000}k</span>`).join('')}
      </div>
    </div>

    <p class="note" id="stat">読み込み中…</p>
    <p class="note">
      データ: <a href="https://www.adsb.lol/" target="_blank" rel="noopener">ADSB.lol</a>（ODbL 1.0）
    </p>
  `

  const $ = <T extends HTMLElement>(id: string): T => root.querySelector<T>('#' + id)!
  const play = $<HTMLButtonElement>('play')
  const seek = $<HTMLInputElement>('seek')
  const trail = $<HTMLSelectElement>('trail')
  const speed = $<HTMLSelectElement>('speed')
  const brightness = $<HTMLInputElement>('brightness')
  const clock = $<HTMLSpanElement>('clock')
  const t0 = $<HTMLSpanElement>('t0')
  const t1 = $<HTMLSpanElement>('t1')
  const stat = $<HTMLParagraphElement>('stat')

  trail.value = String(state.trailSeconds)
  speed.value = String(state.speed)

  play.addEventListener('click', () => {
    onChange({ playing: !state.playing })
    play.textContent = state.playing ? '❚❚' : '▶'
    play.setAttribute('aria-label', state.playing ? '一時停止' : '再生')
  })
  seek.addEventListener('input', () => {
    if (state.max <= state.min) return
    const k = Number(seek.value) / 1000
    onChange({ cursor: state.min + (state.max - state.min) * k, playing: false })
    play.textContent = '▶'
  })
  trail.addEventListener('change', () => onChange({ trailSeconds: Number(trail.value) }))
  speed.addEventListener('change', () => onChange({ speed: Number(speed.value) }))
  brightness.addEventListener('input', () => onChange({ brightness: Number(brightness.value) }))

  for (const [id, key] of [
    ['l-points', 'showPoints'],
    ['l-tracks', 'showTracks'],
    ['l-tracks2d', 'showTracks2d'],
    ['l-mesh', 'showMesh'],
  ] as Array<[string, keyof PanelState]>) {
    const box = $<HTMLInputElement>(id)
    box.addEventListener('change', () => onChange({ [key]: box.checked } as Partial<PanelState>))
  }

  return {
    update(s: PanelState): void {
      clock.textContent = jst(s.cursor)
      t0.textContent = jst(s.min)
      t1.textContent = jst(s.max)
      if (s.max > s.min) {
        seek.value = String(Math.round(((s.cursor - s.min) / (s.max - s.min)) * 1000))
      }
      stat.textContent = s.loaded
        ? `表示中の観測点 ${s.loaded.toLocaleString()} 点（見えている範囲を読み込み中）`
        : '観測点を読み込み中…'
    },
  }
}
