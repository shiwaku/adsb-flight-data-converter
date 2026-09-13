import type { ExpressionSpecification, LayerSpecification, Map as MapLibreMap } from 'maplibre-gl'

import { ALT_STOPS } from './altitude'
import {
  ATTRIBUTION, MESH_PMTILES, MESH_SOURCE_LAYER,
  POINTS_MAXZOOM, POINTS_MINZOOM, POINTS_MLT, POINTS_SOURCE_LAYER,
  TRACKS_PMTILES, TRACKS_SOURCE_LAYER,
} from './config'

/**
 * MapLibre 側のレイヤ。3次元の発光は deck.gl（glow3d.ts）が持ち、
 * ここは「低ズームの俯瞰」と「点群のためのタイル読み込み」を受け持つ。
 */

/**
 * MLT のソース定義。
 *
 * **encoding は TileJSON 経由でしか worker に伝わらない。**
 * インラインの tiles:[...] で書くと MVT として誤パースされて失敗するため、
 * TileJSON を組み立てて Blob URL で渡す。
 * （参考実装 jma-earthquake-data-converter の dataLayers.ts が同じ手を使っている）
 */
function mltSource(url: string, sourceLayer: string, minzoom: number, maxzoom: number) {
  const tilejson = {
    tilejson: '2.2.0',
    tiles: [url],
    minzoom,
    maxzoom,
    attribution: ATTRIBUTION,
    vector_layers: [{ id: sourceLayer, fields: {} }],
  }
  const blob = URL.createObjectURL(
    new Blob([JSON.stringify(tilejson)], { type: 'application/json' }),
  )
  return { type: 'vector', url: blob, encoding: 'mlt', attribution: ATTRIBUTION }
}

/** 高度→色のランプを MapLibre の式にする。凡例と配色をずらさないため同じ定数から作る。 */
function altColorExpr(property: string): ExpressionSpecification {
  const stops = ALT_STOPS.flatMap(([a, c]) => [a, `rgb(${c[0]},${c[1]},${c[2]})`])
  return ['interpolate', ['linear'], ['coalesce', ['get', property], 0], ...stops] as ExpressionSpecification
}

export function addLayers(map: MapLibreMap): void {
  // ---- メッシュ密度（全国の俯瞰）----
  map.addSource('mesh', {
    type: 'vector',
    url: `pmtiles://${MESH_PMTILES}`,
    attribution: ATTRIBUTION,
  })
  map.addLayer({
    id: 'mesh-fill',
    type: 'fill',
    source: 'mesh',
    'source-layer': MESH_SOURCE_LAYER,
    paint: {
      // 観測点数の対数で濃さを決める。最大が 269,618点、中央値が2点という
      // 極端な分布なので、線形だと空港以外が真っ黒になる。
      'fill-color': [
        'interpolate', ['linear'], ['log10', ['max', ['get', 'n_points'], 1]],
        0.5, 'rgba(120, 40, 90, 0.25)',
        1.5, 'rgba(200, 60, 90, 0.45)',
        2.5, 'rgba(255, 140, 60, 0.65)',
        3.5, 'rgba(255, 240, 190, 0.85)',
      ] as unknown as ExpressionSpecification,
      'fill-opacity': 0.9,
    },
  } as LayerSpecification)

  // ---- 軌跡（2次元の俯瞰）----
  map.addSource('tracks', {
    type: 'vector',
    url: `pmtiles://${TRACKS_PMTILES}`,
    attribution: ATTRIBUTION,
  })
  map.addLayer({
    id: 'tracks-line',
    type: 'line',
    source: 'tracks',
    'source-layer': TRACKS_SOURCE_LAYER,
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': altColorExpr('alt_max'),
      'line-width': ['interpolate', ['linear'], ['zoom'], 4, 0.3, 10, 1.2] as unknown as ExpressionSpecification,
      'line-opacity': 0.35,
    },
  } as LayerSpecification)

  // ---- 観測点（MLT）----
  // 描画そのものは deck.gl が持つ。ここに不透明度0の円を1枚だけ置いて、
  // ソースとタイルの読み込みを生かしている。レイヤを完全に無くすと
  // MapLibre がタイルを取りに行かず、querySourceFeatures が空になる。
  map.addSource('points', mltSource(
    POINTS_MLT, POINTS_SOURCE_LAYER, POINTS_MINZOOM, POINTS_MAXZOOM,
  ) as never)
  map.addLayer({
    id: 'points-loader',
    type: 'circle',
    source: 'points',
    'source-layer': POINTS_SOURCE_LAYER,
    paint: { 'circle-radius': 1, 'circle-opacity': 0 },
  } as LayerSpecification)
}

export function setVisible(map: MapLibreMap, id: string, visible: boolean): void {
  if (map.getLayer(id)) {
    map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
  }
}
