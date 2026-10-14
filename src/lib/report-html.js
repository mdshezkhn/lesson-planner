// Client-side report builders: a printable, self-contained HTML gap-analysis
// report and a plain-Markdown version, both generated from the analysis state.

const esc = (s) =>
  String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')

const STATUS = {
  covered: { label: 'Covered', color: '#2f855a', bg: '#e6f4ea', pct: '100%' },
  partial: { label: 'Partial', color: '#c05621', bg: '#fdf0e2', pct: '50%' },
  missing: { label: 'Missing', color: '#c53030', bg: '#fdeaea', pct: '0%' },
}
const VERDICT = {
  keep: { label: 'KEEP', color: '#2f855a', bg: '#e6f4ea' },
  revise: { label: 'REVISE', color: '#c05621', bg: '#fdf0e2' },
  remove: { label: 'REMOVE', color: '#c53030', bg: '#fdeaea' },
}
const bullets = (content) =>
  String(content || '')
    .split('\n')
    .filter((l) => l.trim())
    .map((l) => `<li>${esc(l.replace(/^[-•]\s*/, ''))}</li>`)
    .join('')

export function buildReportHtml(report, meta) {
  const coverage = report.coverage || []
  const slides = report.slides || []
  const newSlides = report.new_slides || []
  const objText = Object.fromEntries((report.objectives || []).map((o) => [o.id, o.text]))

  const counts = { covered: 0, partial: 0, missing: 0 }
  coverage.forEach((c) => { counts[c.status] = (counts[c.status] || 0) + 1 })
  const total = coverage.length || 1
  const pct = Math.round(((counts.covered + 0.5 * counts.partial) / total) * 100)

  const coverageRows = coverage
    .map((c) => {
      const st = STATUS[c.status] || STATUS.partial
      const id = esc(c.objective_id)
      return `<tr>
        <td><b>${id}</b></td><td>${esc(objText[c.objective_id] || '')}</td>
        <td><span class="badge" style="color:${st.color};background:${st.bg}">${st.label} · ${st.pct}</span></td>
        <td>${esc(c.evidence || '')}${c.comment ? `<br><small>${esc(c.comment)}</small>` : ''}</td>
      </tr>`
    })
    .join('')

  const slideCards = slides
    .map((s) => {
      const v = VERDICT[s.verdict] || VERDICT.keep
      const tags = (s.matched_objective_ids || []).map((id) => `<span class="tag">${esc(id)}</span>`).join(' ')
      const revised =
        s.verdict === 'revise' && (s.revised_title || s.revised_content)
          ? `<div class="box"><b>Suggested replacement:</b> ${esc(s.revised_title || s.title)}${
              s.revised_content ? `<ul>${bullets(s.revised_content)}</ul>` : ''
            }</div>`
          : ''
      return `<div class="card">
        <div class="row"><span class="slideno">Slide ${s.index}</span>
          <span class="badge" style="color:${v.color};background:${v.bg}">${v.label}</span> ${tags}</div>
        <div class="stitle">${esc(s.title || '(untitled)')}</div>
        ${s.feedback ? `<div class="feedback">${esc(s.feedback)}</div>` : ''}
        ${revised}
      </div>`
    })
    .join('')

  const newSlideCards = newSlides
    .map(
      (ns) => `<div class="card new">
        <div class="row"><span class="slideno">NEW — insert after slide ${esc(ns.after_index || 'start')}</span>
          ${(ns.objective_ids || []).map((id) => `<span class="tag">${esc(id)}</span>`).join(' ')}</div>
        <div class="stitle">${esc(ns.title || '')}</div>
        ${ns.content ? `<ul>${bullets(ns.content)}</ul>` : ''}
        ${ns.reason ? `<div class="feedback"><b>Why:</b> ${esc(ns.reason)}</div>` : ''}
      </div>`,
    )
    .join('')

  const gaps = (report.gaps || []).length
    ? `<h2>⚠️ Gaps to close</h2><ul class="gaps">${report.gaps.map((g) => `<li>${esc(g)}</li>`).join('')}</ul>`
    : ''

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lesson Sync — Gap Analysis Report</title>
<style>
  body { font-family: 'Segoe UI', Calibri, Arial, sans-serif; color: #1a202c; margin: 0; background: #f5f6fa; }
  .page { max-width: 820px; margin: 0 auto; padding: 32px 28px 48px; background: #fff; min-height: 100vh; }
  h1 { font-size: 1.5rem; margin: 0 0 4px; }
  h2 { font-size: 1.05rem; margin: 28px 0 10px; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }
  .meta { color: #4a5568; font-size: .85rem; margin-bottom: 4px; }
  .score { display: inline-block; font-size: 2rem; font-weight: 800; }
  .score small { font-size: .9rem; color: #4a5568; font-weight: 600; }
  .summarybox { background: #f0f4ff; border-left: 4px solid #667eea; padding: 10px 14px; margin: 14px 0; font-size: .92rem; }
  table { width: 100%; border-collapse: collapse; font-size: .88rem; }
  th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
  th { background: #edf2f7; font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; }
  .card { border: 1px solid #e2e8f0; border-left-width: 5px; border-radius: 8px; padding: 10px 14px; margin: 10px 0; page-break-inside: avoid; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .slideno { font-size: .72rem; font-weight: 800; text-transform: uppercase; color: #4a5568; letter-spacing: .05em; }
  .badge { font-size: .72rem; font-weight: 800; padding: 2px 9px; border-radius: 99px; }
  .tag { background: #667eea; color: #fff; border-radius: 5px; font-size: .68rem; font-weight: 700; padding: 1px 6px; }
  .stitle { font-weight: 700; margin: 5px 0 3px; }
  .feedback { font-size: .9rem; color: #2d3748; margin-top: 3px; }
  .box { background: #fdf8ef; border: 1px dashed #dd9a2b; border-radius: 8px; padding: 8px 12px; margin-top: 8px; font-size: .9rem; }
  .card.new { background: #f0f4ff; border-left-color: #667eea; }
  ul { margin: 6px 0 4px 20px; padding: 0; }
  li { margin: 2px 0; }
  .gaps li { margin: 4px 0; }
  .foot { margin-top: 36px; padding-top: 12px; border-top: 1px solid #e2e8f0; color: #718096; font-size: .78rem; text-align: center; }
  @media print { body { background: #fff; } .page { padding: 10mm 12mm; } .card, h2 { page-break-inside: avoid; } }
</style></head><body><div class="page">
  <h1>📊 Gap Analysis Report</h1>
  <div class="meta"><b>Material:</b> ${esc(meta.source)} &nbsp;·&nbsp; <b>Grade:</b> ${esc(meta.grade)} &nbsp;·&nbsp;
    <b>Categories:</b> ${esc(meta.categories)} &nbsp;·&nbsp; <b>Generated:</b> ${esc(meta.date)}</div>
  <div><span class="score">${pct}%<small> objectives aligned</small></span>
    <span class="badge" style="color:#2f855a;background:#e6f4ea">✅ ${counts.covered} covered</span>
    <span class="badge" style="color:#c05621;background:#fdf0e2">🟡 ${counts.partial} partial</span>
    <span class="badge" style="color:#c53030;background:#fdeaea">❌ ${counts.missing} missing</span></div>
  ${report.summary ? `<div class="summarybox"><b>Summary:</b> ${esc(report.summary)}</div>` : ''}

  <h2>🎯 Objective coverage</h2>
  <table><tr><th>ID</th><th>Objective</th><th>Status</th><th>Evidence</th></tr>${coverageRows}</table>

  <h2>🎞️ Slide-by-slide feedback</h2>
  ${slideCards || '<p>No slide-level feedback was produced.</p>'}

  ${newSlides.length ? `<h2>✨ Proposed new slides</h2>${newSlideCards}` : ''}
  ${gaps}

  <div class="foot">Designed for teachers by a teacher — Mohammed Shehzad Khan (Teacher Mo)<br>
  Generated by Lesson Sync on ${esc(meta.date)} · ${slides.length} slides analysed · ${newSlides.length} new slides proposed</div>
</div></body></html>`
}

export function buildReportMarkdown(report, meta) {
  const coverage = report.coverage || []
  const slides = report.slides || []
  const newSlides = report.new_slides || []
  const objText = Object.fromEntries((report.objectives || []).map((o) => [o.id, o.text]))
  const L = []
  L.push(`# Lesson Sync — Gap Analysis Report`)
  L.push(``)
  L.push(`**Material:** ${meta.source} · **Grade:** ${meta.grade} · **Categories:** ${meta.categories}`)
  L.push(`**Generated:** ${meta.date}`)
  L.push(``)
  if (report.summary) { L.push(`**Summary:** ${report.summary}`); L.push(``) }

  L.push(`## Objective coverage`)
  L.push(``)
  L.push(`| ID | Objective | Status | Evidence |`)
  L.push(`|---|---|---|---|`)
  for (const c of coverage) {
    const st = (STATUS[c.status] || STATUS.partial).label
    L.push(`| ${esc(c.objective_id)} | ${objText[c.objective_id] || ''} | ${st} | ${(c.evidence || '').replace(/\|/g, '\\|')} |`)
  }
  L.push(``)

  L.push(`## Slide-by-slide feedback`)
  L.push(``)
  for (const s of slides) {
    const v = (VERDICT[s.verdict] || VERDICT.keep).label
    L.push(`### Slide ${s.index} — [${v}] ${s.title || '(untitled)'}`)
    if (s.matched_objective_ids?.length) L.push(`Objectives: ${s.matched_objective_ids.join(', ')}`)
    if (s.feedback) L.push(s.feedback)
    if (s.verdict === 'revise' && (s.revised_title || s.revised_content)) {
      L.push(`> **Suggested replacement:** ${s.revised_title || s.title}`)
      for (const b of String(s.revised_content || '').split('\n').filter(Boolean)) L.push(`> - ${b.replace(/^- /, '')}`)
    }
    L.push(``)
  }

  if (newSlides.length) {
    L.push(`## Proposed new slides`)
    L.push(``)
    for (const ns of newSlides) {
      L.push(`### NEW (after slide ${ns.after_index || 'start'}) — ${ns.title}`)
      for (const b of String(ns.content || '').split('\n').filter(Boolean)) L.push(`- ${b.replace(/^- /, '')}`)
      if (ns.reason) L.push(`Why: ${ns.reason}`)
      L.push(``)
    }
  }
  if (report.gaps?.length) {
    L.push(`## Gaps to close`)
    L.push(``)
    for (const g of report.gaps) L.push(`- ${g}`)
  }
  L.push(``)
  L.push(`---`)
  L.push(`Designed for teachers by a teacher — Mohammed Shehzad Khan (Teacher Mo)`)
  return L.join('\n')
}
