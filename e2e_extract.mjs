import { extractUnitsIfLarge } from './src/lib/extract-text.js'
import fs from 'fs'
const buf = fs.readFileSync('backend/samples/big_deck.pptx')
const extracted = await extractUnitsIfLarge({ size: buf.length, name: 'big_deck.pptx', buffer: buf })
console.log('extracted units:', extracted.units.length, '| first:', extracted.units[0].title)
fs.writeFileSync('C:/Users/Mohammed/AppData/Local/Temp/client_mat.json', JSON.stringify({ filename: 'big_deck.pptx', kind: extracted.kind, units: extracted.units }))
console.log('payload KB:', Math.round(Buffer.byteLength(fs.readFileSync('C:/Users/Mohammed/AppData/Local/Temp/client_mat.json')) / 1024))
