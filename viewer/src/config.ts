/**
 * タイルの配置ルート。
 *
 * 開発時は vite.config.ts のミドルウェアが `../work/tiles` を `/tiles` として
 * 配信する。本番は `.env` の VITE_TILE_BASE に配信元を入れて差し替える。
 */
export const TILE_BASE = (import.meta.env.VITE_TILE_BASE || '/tiles').replace(/\/+$/, '')

/** 観測点。3次元表示の主役。MLT で配る。 */
export const POINTS_MLT = `${TILE_BASE}/points-mlt/{z}/{x}/{y}.mlt`
export const POINTS_SOURCE_LAYER = 'points'
export const POINTS_MINZOOM = 5
export const POINTS_MAXZOOM = 10

/** フライト軌跡。俯瞰用の2次元。 */
export const TRACKS_PMTILES = `${TILE_BASE}/tracks.pmtiles`
export const TRACKS_SOURCE_LAYER = 'tracks'

/** メッシュ密度。低ズームの全国俯瞰はこれが担う。 */
export const MESH_PMTILES = `${TILE_BASE}/mesh.pmtiles`
export const MESH_SOURCE_LAYER = 'mesh'

export const ATTRIBUTION =
  'Contains information from <a href="https://www.adsb.lol/" target="_blank" rel="noopener">ADSB.lol</a>, ' +
  'which is made available under ODbL 1.0.'

/** 初期表示。羽田・成田の進入経路が両方入る位置に合わせる。 */
export const INITIAL_VIEW = {
  center: [139.9, 35.6] as [number, number],
  zoom: 8,
  pitch: 60,
  bearing: -20,
}

/**
 * 残光の長さ（秒）の選択肢。
 *
 * ADS-B は中央値4秒間隔で点が並ぶので、落雷のように点が疎ではない。
 * 窓を長く取りすぎると画面が点で埋まって軌跡の形が消える。
 */
export const TRAIL_OPTIONS = [
  { label: '5分', seconds: 300 },
  { label: '15分', seconds: 900 },
  { label: '30分', seconds: 1800 },
  { label: '2時間', seconds: 7200 },
]

/** 再生速度（実時間1秒あたりに進めるデータ時間の秒数）。 */
export const SPEED_OPTIONS = [60, 300, 900, 3600]
