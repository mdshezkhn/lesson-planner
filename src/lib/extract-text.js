// Client-side text extraction for large lesson files.
//
// Vercel serverless caps request bodies at ~4.5 MB, so for big decks the
// browser extracts the TEXT locally and sends only a few KB. Nothing but the
// extracted text ever leaves the device.
//
// Supported fully: .pptx (ZIP of XML) and .ppt (OLE2 binary, same record scan
// as the backend parser). PDFs stay on the direct-upload path (rare > 4.5 MB).
import JSZip from 'jszip'
import CFB from 'cfb'

const decodeXmlEntities = (s) =>
  s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))

const textsFromSlideXml = (xml) => {
  const out = []
  const re = /<a:t>([^<]*)<\/a:t>|<a:t\s+[^>]*>([^<]*)<\/a:t>/g
  let m
  while ((m = re.exec(xml))) {
    const t = decodeXmlEntities(m[1] ?? m[2] ?? '').trim()
    if (t) out.push(t)
  }
  return out
}

async function extractPptx(bytes) {
  const zip = await JSZip.loadAsync(bytes)
  const slideNames = Object.keys(zip.files)
    .filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))
    .sort((a, b) => (parseInt(a.match(/\d+/)[0], 10) - parseInt(b.match(/\d+/)[0], 10)))
  const noteNames = Object.keys(zip.files).filter((n) => /^ppt\/notesSlides\/notesSlide\d+\.xml$/.test(n))

  const units = []
  for (let i = 0; i < slideNames.length; i++) {
    const xml = await zip.file(slideNames[i]).async('string')
    const texts = textsFromSlideXml(xml)
    if (!texts.length) continue
    let notes = ''
    const num = slideNames[i].match(/\d+/)[0]
    const noteFile = noteNames.find((n) => new RegExp(`notesSlide${num}\\.xml$`).test(n))
    if (noteFile) {
      const nxml = await zip.file(noteFile).async('string')
      notes = textsFromSlideXml(nxml)
        .filter((t) => !/^\d+$/.test(t))
        .join('\n')
    }
    units.push({ index: units.length + 1, title: texts[0], text: texts.slice(1).join('\n'), notes })
  }
  if (!units.length) throw new Error('No readable slides found in this .pptx file.')
  return units
}

// ---- .ppt (OLE2 binary): port of the backend OLE record walker -------------
const CFB_TEXT_CHARS = 0x0fa0
const CFB_TEXT_BYTES = 0x0fa8
const CFB_CSTRING = 0x0fba
const CFB_SLIDE_BOUNDARY = new Set([1000, 1006, 1008, 1016]) // document/slide/notes/master

function walkPptRecords(dv, data, offset, end, texts, groups) {
  let pos = offset
  while (pos + 8 <= end) {
    const verInst = dv.getUint16(pos, true)
    const rtype = dv.getUint16(pos + 2, true)
    const rlen = dv.getUint32(pos + 4, true)
    const bs = pos + 8
    const be = Math.min(bs + rlen, end)
    if (bs + rlen > end && be === end && rlen > end) break // truncated record guard
    const isContainer = (verInst & 0x0f) === 0x0f
    if (CFB_SLIDE_BOUNDARY.has(rtype) && isContainer) {
      if (texts.length) {
        groups.push([...texts])
        texts.length = 0
      }
      walkPptRecords(dv, data, bs, be, texts, groups)
      if (texts.length) {
        groups.push([...texts])
        texts.length = 0
      }
      pos = bs + rlen
      continue
    }
    if (isContainer) {
      walkPptRecords(dv, data, bs, be, texts, groups)
    } else if (rtype === CFB_TEXT_CHARS || rtype === CFB_CSTRING) {
      try {
        const t = new TextDecoder('utf-16le').decode(data.subarray(bs, be)).trim()
        if (t) texts.push(t)
      } catch { /* skip */ }
    } else if (rtype === CFB_TEXT_BYTES) {
      try {
        const t = new TextDecoder('windows-1252').decode(data.subarray(bs, be)).trim()
        if (t) texts.push(t)
      } catch { /* skip */ }
    }
    pos = rlen > 0 ? bs + rlen : pos + 8
  }
}

function extractPpt(bytes) {
  const cfb = CFB.read(bytes, { type: 'array' })
  const entry = CFB.find(cfb, 'PowerPoint Document')
  if (!entry || !entry.content) throw new Error('Not a readable .ppt file (missing PowerPoint stream).')
  const data = entry.content
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength)

  const texts = []
  const groups = []
  walkPptRecords(dv, data, 0, data.length, texts, groups)
  if (texts.length) groups.push([...texts])

  const units = []
  for (const chunk of groups) {
    const seen = new Set()
    const lines = []
    for (const t of chunk) {
      for (const ln of t.replace(/\x0b/g, '\n').split('\n')) {
        const line = ln.trim()
        if (line && line.length > 1 && !seen.has(line)) {
          seen.add(line)
          lines.push(line)
        }
      }
    }
    if (!lines.length) continue
    units.push({ index: units.length + 1, title: lines[0].slice(0, 120), text: lines.slice(1).join('\n'), notes: '' })
  }
  if (!units.length)
    throw new Error('No text could be extracted from this .ppt file. Please re-save it as .pptx and upload again.')
  return units
}

/**
 * Extract analysis units in the browser.
 * Returns { kind, units } or null when the file should go to the server as-is
 * (small files / pdf).
 */
export const DIRECT_UPLOAD_LIMIT = 3.5 * 1024 * 1024

export async function extractUnitsIfLarge(file) {
  if (file.size <= DIRECT_UPLOAD_LIMIT) return null
  const name = file.name.toLowerCase()
  const bytes = file.arrayBuffer
    ? new Uint8Array(await file.arrayBuffer())
    : new Uint8Array(file.buffer || file)
  if (name.endsWith('.pptx')) return { kind: 'pptx', units: await extractPptx(bytes) }
  if (name.endsWith('.ppt')) return { kind: 'ppt', units: await extractPpt(bytes) }
  return null // e.g. large PDFs: handled with a clear error server-side
}
