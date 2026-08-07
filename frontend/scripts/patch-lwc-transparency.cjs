// lightweight-charts 4.2.3 标签背景透明化 patch
// 源码 generateContrastColors 把背景色写成 rgb(...) 剥离了 alpha,
// 导致 priceLine 标签背景永远不透明(用户要求中枢上下沿标签半透明)。
// 本脚本把背景改为 rgba(..., alpha), 只有带 alpha 的颜色变透明, 不透明颜色(#131722等)不受影响。
// 通过 package.json postinstall 在每次 npm install 后自动重打(幂等)。
const fs = require('fs')
const path = require('path')

const targets = [
  'node_modules/lightweight-charts/dist/lightweight-charts.development.mjs',
  'node_modules/lightweight-charts/dist/lightweight-charts.production.mjs',
  'node_modules/lightweight-charts/dist/lightweight-charts.standalone.development.js',
]

// (uncompressed) 与 (compressed) 两种模板
const pairs = [
  ['_internal_background: `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`',
   '_internal_background: `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${rgb[3]})`'],
  ['rgb(${i[0]}, ${i[1]}, ${i[2]})',
   'rgba(${i[0]}, ${i[1]}, ${i[2]}, ${i[3]})'],
]

let changed = 0
for (const rel of targets) {
  const f = path.join(__dirname, '..', rel)
  if (!fs.existsSync(f)) { console.log(`skip(不存在): ${rel}`); continue }
  let src = fs.readFileSync(f, 'utf8')
  let fileChanged = false
  for (const [old, next] of pairs) {
    if (src.includes(old)) {
      src = src.split(old).join(next)
      fileChanged = true
    }
  }
  if (fileChanged) {
    fs.writeFileSync(f, src)
    changed++
    console.log(`✅ patched: ${rel}`)
  } else {
    console.log(`= (已打过或无需): ${rel}`)
  }
}
console.log(changed ? `完成, 修改 ${changed} 个文件` : '全部已是最新(幂等)')
