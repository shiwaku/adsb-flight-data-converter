import { createReadStream, statSync } from 'node:fs'
import { extname, join, normalize } from 'node:path'
import { defineConfig } from 'vite'

/**
 * 開発時だけ `../work/tiles` を `/tiles` として配信する。
 *
 * 生成したタイルは work/ の下（.gitignore 済み）にあり、数 GB になる。
 * public/ へコピーするとビルド成果物に混ざるので、開発サーバから直に読ませる。
 * 本番は VITE_TILE_BASE に配信元の URL を入れて差し替える。
 */
function serveWorkTiles() {
  const root = normalize(join(process.cwd(), '..', 'work', 'tiles'))
  const types: Record<string, string> = {
    '.pmtiles': 'application/octet-stream',
    '.mlt': 'application/octet-stream',
    '.json': 'application/json',
  }
  return {
    name: 'serve-work-tiles',
    configureServer(server: { middlewares: { use: (fn: unknown) => void } }) {
      server.middlewares.use((req: any, res: any, next: () => void) => {
        if (!req.url?.startsWith('/tiles/')) return next()
        // パス走査を防ぐ。normalize したうえで root の外を指したら弾く。
        const target = normalize(join(root, decodeURIComponent(req.url.slice('/tiles/'.length))))
        if (!target.startsWith(root)) {
          res.statusCode = 403
          return res.end()
        }
        try {
          const info = statSync(target)
          if (!info.isFile()) throw new Error('not a file')
          res.setHeader('Content-Type', types[extname(target)] ?? 'application/octet-stream')
          res.setHeader('Content-Length', info.size)
          // PMTiles は Range リクエストで部分取得する。
          res.setHeader('Accept-Ranges', 'bytes')
          const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? '')
          if (range) {
            const start = range[1] ? Number(range[1]) : 0
            const end = range[2] ? Number(range[2]) : info.size - 1
            res.statusCode = 206
            res.setHeader('Content-Range', `bytes ${start}-${end}/${info.size}`)
            res.setHeader('Content-Length', end - start + 1)
            return createReadStream(target, { start, end }).pipe(res)
          }
          return createReadStream(target).pipe(res)
        } catch {
          res.statusCode = 404
          return res.end()
        }
      })
    },
  }
}

export default defineConfig({
  base: './',
  plugins: [serveWorkTiles()],
})
