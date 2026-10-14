import { useState, useRef } from 'react'
import './App.css'
import { extractUnitsIfLarge, DIRECT_UPLOAD_LIMIT } from './lib/extract-text.js'
import { buildReportHtml, buildReportMarkdown } from './lib/report-html.js'

// Same-origin by default: in dev, Vite proxies /analyze /generate /health to
// localhost:8000; in production, vercel.json rewrites them to the serverless
// backend deployed in the same project. Set VITE_API_URL only if you host the
// frontend and backend on different origins.
const API = import.meta.env.VITE_API_URL || ''


const CATEGORIES = ['Grammar', 'Reading', 'Speaking', 'Writing', 'Phonics', 'Science']
const GRADES = ['Kindergarten', '1st Grade', '2nd Grade', '3rd Grade', '4th Grade', '5th Grade', '6th Grade']

const STATUS_META = {
  covered: { icon: '✅', label: 'Covered', cls: 'badge-green' },
  partial: { icon: '🟡', label: 'Partial', cls: 'badge-amber' },
  missing: { icon: '❌', label: 'Missing', cls: 'badge-red' },
}
const VERDICT_META = {
  keep: { icon: '✔️', label: 'Keep', cls: 'badge-green' },
  revise: { icon: '✏️', label: 'Revise', cls: 'badge-amber' },
  remove: { icon: '🗑️', label: 'Remove', cls: 'badge-red' },
}

// Visual summary of objective coverage: donut with overall % + legend.
function CoverageChart({ coverage }) {
  const counts = { covered: 0, partial: 0, missing: 0 }
  coverage.forEach((c) => { counts[c.status] = (counts[c.status] || 0) + 1 })
  const total = coverage.length
  if (!total) return null
  const pct = Math.round(((counts.covered + 0.5 * counts.partial) / total) * 100)
  const C = 2 * Math.PI * 42
  const segs = [
    { key: 'covered', val: counts.covered, color: '#38a169' },
    { key: 'partial', val: counts.partial, color: '#ed8936' },
    { key: 'missing', val: counts.missing, color: '#e53e3e' },
  ]
  let acc = 0
  const arcs = segs.filter((s) => s.val > 0).map((s) => {
    const frac = s.val / total
    const arc = {
      color: s.color,
      dash: `${Math.max(frac * C - 2, 0.1)} ${C - frac * C + 2}`,
      offset: -((acc / total) * C),
    }
    acc += s.val
    return arc
  })
  return (
    <div className="chart-card">
      <div className="donut-wrap">
        <svg viewBox="0 0 120 120" width="150" height="150" role="img" aria-label={`Coverage ${pct}%`}>
          <circle cx="60" cy="60" r="42" fill="none" stroke="#e2e8f0" strokeWidth="16" />
          {arcs.map((a, i) => (
            <circle
              key={i} cx="60" cy="60" r="42" fill="none"
              stroke={a.color} strokeWidth="16" strokeLinecap="butt"
              strokeDasharray={a.dash} strokeDashoffset={a.offset}
              transform="rotate(-90 60 60)"
            />
          ))}
          <text x="60" y="58" textAnchor="middle" className="donut-num">{pct}%</text>
          <text x="60" y="76" textAnchor="middle" className="donut-label">aligned</text>
        </svg>
      </div>
      <div className="chart-legend">
        <div className="legend-row"><span className="dot" style={{ background: '#38a169' }} /> Covered — objective fully taught <strong>{counts.covered}</strong></div>
        <div className="legend-row"><span className="dot" style={{ background: '#ed8936' }} /> Partial — touched, needs depth <strong>{counts.partial}</strong></div>
        <div className="legend-row"><span className="dot" style={{ background: '#e53e3e' }} /> Missing — not addressed <strong>{counts.missing}</strong></div>
        <div className="legend-note">{counts.covered} full + {counts.partial * 0.5} half credit ÷ {total} objectives</div>
      </div>
    </div>
  )
}

function App() {
  // ---- inputs -------------------------------------------------------------
  const [file, setFile] = useState(null)
  const [objectivesFile, setObjectivesFile] = useState(null)
  const [objectives, setObjectives] = useState('')
  const [gradeLevel, setGradeLevel] = useState('3rd Grade')
  const [selectedCategories, setSelectedCategories] = useState(['Reading'])
  const [interactive, setInteractive] = useState(true)

  // ---- analysis result ----------------------------------------------------
  const [phase, setPhase] = useState('input') // input | report
  const [analyzing, setAnalyzing] = useState(false)
  const [error, setError] = useState('')
  const [report, setReport] = useState(null)
  const [editableOriginal, setEditableOriginal] = useState(false)
  const [nUnits, setNUnits] = useState(0)
  const [isMock, setIsMock] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')

  // ---- sign-in: name + per-user access code (admin = Teacher Mo) ----------
  const [accessCode, setAccessCode] = useState(() => localStorage.getItem('lesson-sync-access-code') || '')
  const [userName, setUserName] = useState(() => localStorage.getItem('lesson-sync-user-name') || '')
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem('lesson-sync-user') || 'null') } catch { return null }
  })
  const [usersList, setUsersList] = useState([])
  const [newColleagueName, setNewColleagueName] = useState('')
  const [hasRef, setHasRef] = useState('yes')
  const [designDone, setDesignDone] = useState(false)

  // ---- generation ---------------------------------------------------------
  const [slideActions, setSlideActions] = useState({}) // { "<unit index>": keep|revise|remove }
  const [includeNew, setIncludeNew] = useState(true)
  const [illustrations, setIllustrations] = useState([])
  const [illustrating, setIllustrating] = useState(false)
  const [skipIllo, setSkipIllo] = useState({}) // title -> true = excluded from the deck
  const [mode, setMode] = useState('edit')
  const [generating, setGenerating] = useState(false)

  const fileInputRef = useRef(null)
  const objFileInputRef = useRef(null)

  // ---- handlers -----------------------------------------------------------
  const handleDrop = (e) => {
    e.preventDefault()
    if (e.dataTransfer.files?.[0]) setFile(e.dataTransfer.files[0])
  }

  const toggleCategory = (cat) =>
    setSelectedCategories((prev) =>
      prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat],
    )

  const handleAnalyze = async () => {
    if (!file) return setError('Please upload your previous-year presentation or worksheet first.')
    if (!objectives.trim() && !objectivesFile)
      return setError('Please enter the current-year lesson objectives (or attach an objectives file).')

    setError('')
    setAnalyzing(true)
    try {
      let clientMaterial = null
      if (file.size > DIRECT_UPLOAD_LIMIT) {
        setUploadMsg('Reading slides in your browser')
        clientMaterial = await extractUnitsIfLarge(file)
        setUploadMsg('')
        if (!clientMaterial)
          throw new Error('This file is too large to upload and cannot be read in the browser (large PDF). Please convert it to .pptx/.docx or split it.')
      }
      const fd = new FormData()
      if (clientMaterial) {
        fd.append('clientMaterial', JSON.stringify({ filename: file.name, ...clientMaterial }))
        fd.append('filename', file.name)
      } else {
        fd.append('file', file)
      }
      fd.append('objectives', objectives)
      fd.append('gradeLevel', gradeLevel)
      fd.append('categories', selectedCategories.join(', '))
      fd.append('interactive', interactive)
      fd.append('accessCode', accessCode)
      if (objectivesFile) fd.append('objectivesFile', objectivesFile)

      const res = await fetch(`${API}/analyze`, { method: 'POST', body: fd })
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail || res.statusText
        if (res.status === 401) throw new Error('Invalid or missing access code.')
        if (res.status === 413) throw new Error('File too large. Free hosting caps uploads at 4 MB — try a smaller file or split the deck.')
        if (res.status === 504 || res.status === 502) throw new Error('The analysis took too long and was cut off. Try a deck with fewer slides, then retry.')
        throw new Error(typeof detail === 'string' ? detail : 'Analysis failed')
      }
      const data = await res.json()
      localStorage.setItem('lesson-sync-access-code', accessCode)
      setReport(data.report)
      setEditableOriginal(data.editable_original && !clientMaterial)
      setNUnits(data.n_units)
      setIsMock(data.mock)
      setIllustrations([])
      setSkipIllo({})

      const defaults = {}
      for (const s of data.report.slides || []) defaults[String(s.index)] = s.verdict
      setSlideActions(defaults)
      setMode(data.editable_original ? 'edit' : 'rebuild')
      setPhase('report')
    } catch (e) {
      setError(`Analysis failed: ${e.message}`)
    } finally {
      setAnalyzing(false)
    }
  }

  const handleIllustrate = async () => {
    setError('')
    setIllustrating(true)
    try {
      const fd = new FormData()
      fd.append('report', JSON.stringify(report))
      fd.append('accessCode', accessCode)
      fd.append('gradeLevel', gradeLevel)
      fd.append('categories', selectedCategories.join(', '))
      fd.append('maxImages', '3')
      const res = await fetch(`${API}/illustrate`, { method: 'POST', body: fd })
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail || res.statusText
        throw new Error(res.status === 401 ? 'Invalid or missing access code.' : (typeof detail === 'string' ? detail : 'Illustration failed'))
      }
      const data = await res.json()
      setIllustrations(data.illustrations || [])
      setSkipIllo({})
    } catch (e) {
      setError(`Illustration failed: ${e.message}`)
    } finally {
      setIllustrating(false)
    }
  }

  const chosenIllos = () =>
    illustrations.filter((i) => i.image_url && !skipIllo[i.title]).map(({ title, image_url }) => ({ title, url: image_url }))

  const handleGenerate = async () => {
    if (mode === 'edit' && !file)
      return setError('Edit mode needs your original .pptx — please start over and upload it again.')

    setError('')
    setGenerating(true)
    try {
      // Stateless: the report (and, for edit mode, the original file) are
      // re-sent, so any stateless host can serve this without sessions.
      const fd = new FormData()
      fd.append('report', JSON.stringify(report))
      fd.append('mode', mode)
      fd.append('slideActions', JSON.stringify(slideActions))
      fd.append('includeNewSlides', includeNew)
      fd.append('accessCode', accessCode)
      fd.append('gradeLevel', gradeLevel)
      fd.append('categories', selectedCategories.join(', '))
      fd.append('interactive', interactive)
      fd.append('sourceName', file?.name || 'previous material')
      const illos = chosenIllos()
      if (illos.length) fd.append('illustrations', JSON.stringify(illos))
      if (mode === 'edit') fd.append('file', file)

      const res = await fetch(`${API}/generate`, { method: 'POST', body: fd })
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail || res.statusText
        throw new Error(res.status === 401 ? 'Invalid or missing access code.' : (typeof detail === 'string' ? detail : 'Generation failed'))
      }
      const blob = await res.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'Updated_Lesson_Presentation.pptx'
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      a.remove()
    } catch (e) {
      setError(`Could not build the deck: ${e.message}`)
    } finally {
      setGenerating(false)
    }
  }

  const signIn = async () => {
    setError('')
    try {
      const res = await fetch(`${API}/login?code=${encodeURIComponent(accessCode)}`)
      if (!res.ok) throw new Error('Invalid or missing access code.')
      const data = await res.json()
      setUser({ name: data.name, role: data.role })
      localStorage.setItem('lesson-sync-access-code', accessCode)
      localStorage.setItem('lesson-sync-user', JSON.stringify({ name: data.name, role: data.role }))
      if (data.role === 'admin') loadUsers()
    } catch (e) {
      setError(`Sign-in failed: ${e.message}`)
    }
  }

  const loadUsers = async () => {
    const res = await fetch(`${API}/users`, { headers: { 'x-access-code': accessCode } })
    if (res.ok) setUsersList((await res.json()).users)
  }

  const addUser = async () => {
    if (!newColleagueName.trim()) return
    const res = await fetch(`${API}/users`, {
      method: 'POST',
      headers: { 'x-access-code': accessCode, 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newColleagueName.trim() }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return setError(data.detail || 'Could not add colleague.')
    setNewColleagueName('')
    setError('')
    loadUsers()
  }

  const removeUser = async (code) => {
    const res = await fetch(`${API}/users?code=${encodeURIComponent(code)}`, {
      method: 'DELETE',
      headers: { 'x-access-code': accessCode },
    })
    if (res.ok) loadUsers()
  }

  const handleDesign = async () => {
    if (!objectives.trim()) return setError('Please enter the lesson objectives first.')
    setError('')
    setGenerating(true)
    try {
      const fd = new FormData()
      fd.append('designMode', 'true')
      fd.append('mode', 'rebuild')
      fd.append('report', '')
      fd.append('objectives', objectives)
      fd.append('gradeLevel', gradeLevel)
      fd.append('categories', selectedCategories.join(', '))
      fd.append('interactive', interactive)
      fd.append('accessCode', accessCode)
      fd.append('userName', userName || user?.name || '')
      fd.append('sourceName', 'designed from scratch')
      const illos = chosenIllos()
      if (illos.length) fd.append('illustrations', JSON.stringify(illos))
      const res = await fetch(`${API}/generate`, { method: 'POST', body: fd })
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail || res.statusText
        throw new Error(res.status === 401 ? 'Invalid or missing access code.' : (typeof detail === 'string' ? detail : 'Design failed'))
      }
      const blob = await res.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'Designed_Lesson.pptx'
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      a.remove()
      setDesignDone(true)
    } catch (e) {
      setError(`Could not design the lesson: ${e.message}`)
    } finally {
      setGenerating(false)
    }
  }

  const reportMeta = () => ({
    source: file?.name || 'previous material',
    grade: gradeLevel,
    categories: selectedCategories.join(', '),
    preparedFor: userName || user?.name || '',
    date: new Date().toLocaleString(),
  })

  const saveBlob = (content, mime, filename) => {
    const blob = new Blob([content], { type: mime })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    a.remove()
  }

  const stamp = () => new Date().toISOString().slice(0, 10)

  const handleReportHtml = () => {
    saveBlob(buildReportHtml(report, reportMeta()), 'text/html;charset=utf-8', `Gap_Analysis_Report_${stamp()}.html`)
  }
  const handleReportMd = () => {
    saveBlob(buildReportMarkdown(report, reportMeta()), 'text/markdown;charset=utf-8', `Gap_Analysis_Report_${stamp()}.md`)
  }
  const handleReportPrint = () => {
    const w = window.open('', '_blank')
    if (!w) return setError('Pop-up blocked — allow pop-ups for this site to use Print.')
    w.document.write(buildReportHtml(report, reportMeta()))
    w.document.close()
    w.focus()
    setTimeout(() => w.print(), 400)
  }

  const startOver = () => {
    setPhase('input')
    setReport(null)
    setError('')
    setSlideActions({})
    setIllustrations([])
    setSkipIllo({})
  }

  // ---- derived ------------------------------------------------------------
  const counts = (report?.coverage || []).reduce(
    (acc, c) => ({ ...acc, [c.status]: (acc[c.status] || 0) + 1 }),
    {},
  )
  const actionCounts = Object.values(slideActions).reduce(
    (acc, v) => ({ ...acc, [v]: (acc[v] || 0) + 1 }),
    {},
  )

  // =========================================================================
  // PHASE 1 — INPUT
  // =========================================================================
  if (phase === 'input') {
    return (
      <div className="glass-panel app-container">
        <h2 className="title">🎯 Lesson Sync</h2>
        <p className="subtitle">
          Upload old material + lesson objectives → get a gap analysis and an updated,
          objective-aligned presentation.
        </p>

        <div className="form-group">
          <label className="form-label"><span>🗂️</span> Do you have a reference lesson presentation / worksheet?</label>
          <div className="categories-container">
            <div className={`category-pill ${hasRef === 'yes' ? 'selected' : ''}`} onClick={() => setHasRef('yes')}>Yes — analyse against my objectives</div>
            <div className={`category-pill ${hasRef === 'no' ? 'selected' : ''}`} onClick={() => setHasRef('no')}>No — design from scratch</div>
          </div>
        </div>

        {hasRef === 'yes' && (
        <div className="form-group">
          <label className="form-label"><span>📄</span> presentation/ worksheet</label>
          <div
            className="upload-zone"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
          >
            <input
              type="file"
              style={{ display: 'none' }}
              ref={fileInputRef}
              accept=".pdf,.pptx,.ppt,.docx"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>
            <p className="upload-title">{file ? `✅ ${file.name}` : 'Drag & Drop or Click to Upload'}</p>
            <p className="upload-subtitle">supports PowerPoint (.pptx, .ppt), PDF and Word (.docx)</p>
          </div>
        </div>
        )}

        <div className="form-group">
          <label className="form-label"><span>🎯</span> Lesson objectives</label>
          <textarea
            className="form-textarea"
            value={objectives}
            onChange={(e) => setObjectives(e.target.value)}
            placeholder={'One objective per line, e.g.:\n• Identify the main idea of a text\n• Use supporting details to justify answers'}
          />
          {hasRef === "yes" && (
          <div className="obj-file-row">
            <button className="link-btn" onClick={() => objFileInputRef.current?.click()}>
              📎 {objectivesFile ? objectivesFile.name : 'Attach objectives as a file (.docx/.pdf/.txt)'}
            </button>
            <input
              type="file"
              style={{ display: 'none' }}
              ref={objFileInputRef}
              accept=".pdf,.docx,.txt"
              onChange={(e) => setObjectivesFile(e.target.files?.[0] || null)}
            />
          </div>
          )}
        </div>

        <div className="form-group">
          <label className="form-label"><span>🎓</span> Grade level</label>
          <select className="form-select" value={gradeLevel} onChange={(e) => setGradeLevel(e.target.value)}>
            {GRADES.map((g) => <option key={g}>{g}</option>)}
          </select>
        </div>

        <div className="form-group">
          <label className="form-label"><span>🏷️</span> Categories</label>
          <div className="categories-container">
            {CATEGORIES.map((cat) => (
              <div
                key={cat}
                className={`category-pill ${selectedCategories.includes(cat) ? 'selected' : ''}`}
                onClick={() => toggleCategory(cat)}
              >
                {cat}
              </div>
            ))}
          </div>
        </div>

        <div className="toggle-group">
          <span className="toggle-label">Include Games & Interactive Activities 🎮</span>
          <label className="switch">
            <input type="checkbox" checked={interactive} onChange={(e) => setInteractive(e.target.checked)} />
            <span className="slider" />
          </label>
        </div>

        <div className="form-group">
          <label className="form-label"><span>🙋</span> Your name</label>
          <input
            className="form-select"
            value={userName}
            onChange={(e) => setUserName(e.target.value)}
            placeholder="e.g. Aaminah"
          />
        </div>

        <div className="form-group">
          <label className="form-label"><span>🔒</span> Access code</label>
          <div className="signin-row">
            <input
              className="form-select"
              type="password"
              value={accessCode}
              onChange={(e) => setAccessCode(e.target.value)}
              placeholder="Your secret code"
              autoComplete="off"
            />
            <button className="ghost-btn small" onClick={signIn}>Sign in</button>
          </div>
          {user && (
            <div className="signedin-note">
              ✅ Signed in as <b>{user.name}</b> ({user.role === 'admin' ? 'admin' : 'teacher'})
            </div>
          )}
        </div>

        {user?.role === 'admin' && (
          <div className="form-group admin-panel">
            <label className="form-label"><span>👥</span> Colleague access</label>
            <div className="signin-row">
              <input
                className="form-select"
                value={newColleagueName}
                onChange={(e) => setNewColleagueName(e.target.value)}
                placeholder="Colleague's name"
              />
              <button className="ghost-btn small" onClick={addUser}>+ Generate code</button>
            </div>
            {usersList.length > 0 && (
              <table className="users-table">
                <tbody>
                  {usersList.map((u) => (
                    <tr key={u.code}>
                      <td>{u.name}</td>
                      <td><code>{u.code}</code></td>
                      <td>
                        {u.source === 'blob' ? (
                          <button className="link-btn" onClick={() => removeUser(u.code)}>remove</button>
                        ) : (
                          <small>(static)</small>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="optional-hint">
              Share each colleague's code + this app's URL. Codes marked (static) are managed in the
              APP_USERS environment variable — ask the app admin to add new ones.
            </div>
          </div>
        )}

        {error && <div className="error-box">⚠️ {error}</div>}

        {hasRef === 'yes' ? (
          <button className="submit-btn" onClick={handleAnalyze} disabled={analyzing}>
            {analyzing ? (uploadMsg ? `${uploadMsg}…` : '🤖 Analyzing slides against objectives…') : '🔍 Analyze & Find Gaps'}
          </button>
        ) : (
          <button className="submit-btn" onClick={handleDesign} disabled={generating}>
            {generating ? '✨ Designing your lesson…' : '✨ Design lesson from scratch'}
          </button>
        )}
        {designDone && !error && (
          <div className="design-success">✅ Your lesson deck has been designed and downloaded (Designed_Lesson.pptx).</div>
        )}
        <div className="app-footer">
          Designed for teachers by a teacher — Mohammed Shehzad Khan (Teacher Mo)
        </div>
      </div>
    )
  }

  // =========================================================================
  // PHASE 2 — REPORT
  // =========================================================================
  const coverage = report?.coverage || []
  const slides = report?.slides || []
  const newSlides = report?.new_slides || []
  const objText = Object.fromEntries((report?.objectives || []).map((o) => [o.id, o.text]))

  return (
    <div className="glass-panel app-container app-container-wide">
      <div className="report-header">
        <h2 className="title">📊 Gap Analysis Report</h2>
        <div className="header-chips">
          <span className="chip">{nUnits} slides/pages analyzed</span>
          <span className="chip chip-green">✅ {counts.covered || 0} covered</span>
          <span className="chip chip-amber">🟡 {counts.partial || 0} partial</span>
          <span className="chip chip-red">❌ {counts.missing || 0} missing</span>
          {isMock && <span className="chip chip-amber">MOCK MODE — add a free API key (OpenRouter / NVIDIA) for real analysis</span>}
          {!isMock && report?._engine && (
            <span className="chip chip-green" title={`Route: ${report._engine.route || report._engine.model}`}>
              🧠 {report._engine.provider}/{report._engine.model}
            </span>
          )}
        </div>
      </div>

      {error && <div className="error-box">⚠️ {error}</div>}

      {report?.summary && (
        <div className="summary-box">
          <strong>Summary:</strong> {report.summary}
        </div>
      )}

      {/* -------- objectives coverage -------- */}
      <h3 className="section-title">🎯 Objective coverage</h3>
      <CoverageChart coverage={coverage} />
      <div className="coverage-list">
        {coverage.map((c) => {
          const meta = STATUS_META[c.status] || STATUS_META.partial
          return (
            <div key={c.objective_id} className="coverage-row">
              <span className={`badge ${meta.cls}`}>{meta.icon} {meta.label}</span>
              <div className="coverage-body">
                <div className="coverage-objective">
                  {objText[c.objective_id] || c.objective_id}
                  <span className="pct-badge">{c.status === 'covered' ? '100%' : c.status === 'partial' ? '50%' : '0%'}</span>
                </div>
                <div className="bar-track">
                  <div
                    className="bar-fill"
                    style={{
                      width: c.status === 'covered' ? '100%' : c.status === 'partial' ? '50%' : '4%',
                      background: c.status === 'covered' ? '#38a169' : c.status === 'partial' ? '#ed8936' : '#e53e3e',
                    }}
                  />
                </div>
                {c.evidence && <div className="coverage-detail"><strong>Evidence:</strong> {c.evidence}</div>}
                {c.comment && <div className="coverage-detail">{c.comment}</div>}
              </div>
            </div>
          )
        })}
      </div>

      {/* -------- slide-by-slide -------- */}
      <h3 className="section-title">
        🎞️ Slide-by-slide feedback
        <span className="section-hint">(change the action on any slide before generating)</span>
      </h3>
      <div className="slides-list">
        {slides.map((s) => {
          const meta = VERDICT_META[s.verdict] || VERDICT_META.keep
          const action = slideActions[String(s.index)] || s.verdict
          return (
            <div key={s.index} className={`slide-card slide-card-${action}`}>
              <div className="slide-card-head">
                <span className="slide-number">Slide {s.index}</span>
                <span className={`badge ${meta.cls}`}>{meta.icon} {meta.label}</span>
                <select
                  className="action-select"
                  value={action}
                  onChange={(e) =>
                    setSlideActions((prev) => ({ ...prev, [String(s.index)]: e.target.value }))
                  }
                >
                  <option value="keep">✔️ Keep</option>
                  <option value="revise">✏️ Revise</option>
                  <option value="remove">🗑️ Remove</option>
                </select>
              </div>
              <div className="slide-title">{s.title}</div>
              {s.feedback && <div className="slide-feedback">{s.feedback}</div>}
              {s.matched_objective_ids?.length > 0 && (
                <div className="slide-tags">
                  {s.matched_objective_ids.map((id) => (
                    <span key={id} className="tag" title={objText[id]}>{id}</span>
                  ))}
                </div>
              )}
              {action === 'revise' && s.revised_content && (
                <div className="revised-preview">
                  <strong>Will become:</strong> {s.revised_title || s.title}
                  <ul>{s.revised_content.split('\n').map((b, i) => <li key={i}>{b}</li>)}</ul>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* -------- new slides / gaps -------- */}
      {newSlides.length > 0 && (
        <>
          <h3 className="section-title">✨ Proposed new slides (gap fillers)</h3>
          <div className="slides-list">
            {newSlides.map((ns, i) => (
              <div key={i} className="slide-card slide-card-new">
                <div className="slide-card-head">
                  <span className="slide-number">NEW — after slide {ns.after_index || 'start'}</span>
                  {ns.objective_ids?.map((id) => (
                    <span key={id} className="tag" title={objText[id]}>{id}</span>
                  ))}
                </div>
                <div className="slide-title">{ns.title}</div>
                <ul className="bullet-preview">
                  {String(ns.content || '').split('\n').map((b, j) => <li key={j}>{b}</li>)}
                </ul>
                {ns.reason && <div className="slide-feedback"><strong>Why:</strong> {ns.reason}</div>}
              </div>
            ))}
          </div>
          <div className="toggle-group">
            <span className="toggle-label">Insert these {newSlides.length} new slide(s)</span>
            <label className="switch">
              <input type="checkbox" checked={includeNew} onChange={(e) => setIncludeNew(e.target.checked)} />
              <span className="slider" />
            </label>
          </div>
        </>
      )}

      {report?.gaps?.length > 0 && (
        <div className="gaps-box">
          <strong>⚠️ Gaps to close:</strong>
          <ul>{report.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>
        </div>
      )}

      {/* -------- illustrations -------- */}
      <h3 className="section-title">🎨 Illustrations</h3>
      <div className="option-row">
        <button className="ghost-btn small" onClick={handleIllustrate} disabled={illustrating}>
          {illustrating ? '🎨 Painting… (up to ~30s)' : illustrations.length ? '🎨 Regenerate illustrations' : '🎨 Generate illustrations for the new slides'}
        </button>
        {illustrations.length > 0 && (
          <span className="section-hint">untick any you don't want — ticked ones are embedded into the deck when you download</span>
        )}
      </div>
      {illustrations.length > 0 && (
        <div className="illo-grid">
          {illustrations.map((il) => (
            <div key={il.title} className={`illo-card ${skipIllo[il.title] ? 'illo-off' : ''}`}>
              {il.image_url
                ? <img src={il.image_url} alt={il.title} loading="lazy" />
                : <div className="illo-mock">{il.error ? '⚠️ generation failed' : '🧩 mock mode — no image'}</div>}
              <label className="illo-label">
                <input
                  type="checkbox"
                  checked={!skipIllo[il.title]}
                  onChange={(e) => setSkipIllo((s) => ({ ...s, [il.title]: !e.target.checked }))}
                />
                <span>{il.title}</span>
              </label>
            </div>
          ))}
        </div>
      )}

      {/* -------- downloads -------- */}
      <h3 className="section-title">📥 Downloads</h3>
      <div className="report-dl-row">
        <span className="report-dl-label">📄 Analysis report / feedback:</span>
        <button className="ghost-btn small" onClick={handleReportHtml}>⬇️ Report (.html)</button>
        <button className="ghost-btn small" onClick={handleReportPrint}>🖨️ Print / Save as PDF</button>
        <button className="ghost-btn small" onClick={handleReportMd}>📝 Markdown</button>
      </div>
      <h3 className="section-title">🎬 Build the updated presentation</h3>
      <div className="mode-row">
        <button
          className={`mode-btn ${mode === 'edit' ? 'mode-btn-active' : ''}`}
          disabled={!editableOriginal}
          onClick={() => setMode('edit')}
          title={editableOriginal ? '' : 'Only available for .pptx uploads'}
        >
          ✏️ Edit my deck
          <small>{editableOriginal ? 'keep original design, apply changes' : 'needs a .pptx upload'}</small>
        </button>
        <button
          className={`mode-btn ${mode === 'rebuild' ? 'mode-btn-active' : ''}`}
          onClick={() => setMode('rebuild')}
        >
          🆕 Rebuild fresh deck
          <small>new deck from objectives + old content</small>
        </button>
      </div>

      <div className="plan-line">
        Plan: {actionCounts.keep || 0} kept · {actionCounts.revise || 0} revised · {actionCounts.remove || 0} removed
        {includeNew && newSlides.length > 0 ? ` · +${newSlides.length} new` : ''} → <strong>{mode === 'edit' ? 'original design preserved' : 'fresh rebuild (Comic Sans 36pt)'}</strong>
      </div>

      <div className="btn-row">
        <button className="ghost-btn" onClick={startOver}>← Start over</button>
        <button className="submit-btn" onClick={handleGenerate} disabled={generating}>
          {generating ? '📦 Building…' : '⬇️ Download Updated Presentation (.pptx)'}
        </button>
      </div>
        <div className="app-footer">
          Designed for teachers by a teacher — Mohammed Shehzad Khan (Teacher Mo)
        </div>
    </div>
  )
}

export default App
