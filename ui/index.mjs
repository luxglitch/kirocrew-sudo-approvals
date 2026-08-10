// Sudo Approvals — KiroCrew dashboard UI.
// Loaded by AppHost via /apps/sudo-approvals/ui/index.mjs. It uses host-provided
// React, app-sdk, and icons, so the app remains build-free and version-tolerant.

const React = window.__kirocrew_modules.react
const { useAppApi } = window.__kirocrew_modules['@kirocrew/app-sdk']
const { ShieldCheck, Check, X, Terminal, FolderOpen, Clock3, ShieldAlert } = window.__kirocrew_modules['lucide-react']

const { useState, useEffect, useRef, useCallback, createElement: h } = React

const DURATIONS = [
  [300, '5 min'],
  [900, '15 min'],
  [1800, '30 min'],
  [3600, '1 hour'],
  [7200, '2 hours'],
]

function parseReq(text) {
  let cmd = '', cwd = ''
  for (const line of String(text).split('\n')) {
    const value = line.trim()
    if (value.startsWith('CMD:')) cmd = value.slice(4).trim()
    else if (value.startsWith('CWD:')) cwd = value.slice(4).trim()
  }
  return { cmd, cwd }
}

function remainingLabel(total) {
  const seconds = Math.max(0, Number(total) || 0)
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours) return `${hours}h ${String(minutes).padStart(2, '0')}m ${String(secs).padStart(2, '0')}s`
  return `${minutes}m ${String(secs).padStart(2, '0')}s`
}

function SudoApprovals() {
  const api = useAppApi()
  const [state, setState] = useState({ pending: null, log: [], autoApprove: { active: false, remainingSeconds: 0 } })
  const [busy, setBusy] = useState(false)
  const [windowBusy, setWindowBusy] = useState(false)
  const [duration, setDuration] = useState(900)
  const [confirming, setConfirming] = useState(false)
  const [err, setErr] = useState(null)
  const timer = useRef(null)

  const poll = useCallback(() => {
    return api.get('/apps/sudo-approvals/api/state')
      .then(data => { setState(data); setErr(null) })
      .catch(error => setErr(error.message))
  }, [api])

  useEffect(() => {
    poll()
    timer.current = setInterval(poll, 1000)
    return () => clearInterval(timer.current)
  }, [poll])

  const respond = (id, decision) => {
    setBusy(true)
    api.post('/apps/sudo-approvals/api/respond', { id, decision })
      .then(poll)
      .catch(error => setErr(error.message))
      .finally(() => setBusy(false))
  }

  const changeWindow = payload => {
    setWindowBusy(true)
    api.post('/apps/sudo-approvals/api/auto-approve', payload)
      .then(() => { setConfirming(false); return poll() })
      .catch(error => setErr(error.message))
      .finally(() => setWindowBusy(false))
  }

  const pending = state.pending
  const request = pending ? parseReq(pending.text) : null
  const auto = state.autoApprove || { active: false, remainingSeconds: 0 }
  const durationName = DURATIONS.find(([seconds]) => seconds === duration)?.[1] || '15 min'

  return h('div', { className: 'px-6 pt-4 pb-8' },
    h('div', { className: 'flex items-end justify-between gap-4 pb-3' },
      h('div', null,
        h('div', { className: 'text-2xl font-bold tracking-tight text-text-strong flex items-center gap-2' },
          h(ShieldCheck, { size: 22 }), 'Sudo Approvals'),
        h('div', { className: 'text-muted text-sm mt-1' },
          'Review privileged command requests or enable a temporary approval window'),
      ),
    ),

    err && h('div', { className: 'text-danger text-sm py-2' }, 'Error: ', err),

    auto.active
      ? h('div', { className: 'border-2 border-warning rounded-lg p-4 mb-5 bg-warn-subtle' },
          h('div', { className: 'flex items-start gap-3' },
            h(ShieldAlert, { size: 19, className: 'text-warning shrink-0 mt-0.5' }),
            h('div', { className: 'min-w-0 flex-1' },
              h('div', { className: 'font-semibold text-text-strong' }, 'Auto-approve is active'),
              h('div', { className: 'text-sm text-warning font-mono mt-1' }, remainingLabel(auto.remainingSeconds), ' remaining'),
              h('div', { className: 'text-[12px] text-muted mt-1' },
                'Every new privileged command is approved without review until this window expires.'),
            ),
          ),
          h('button', {
            disabled: windowBusy,
            onClick: () => changeWindow({ action: 'disarm' }),
            className: 'w-full mt-3 py-2 rounded-md bg-danger text-sm font-semibold cursor-pointer hover:opacity-90 disabled:opacity-50',
            style: { color: 'var(--bg)' },
          }, 'Disarm now'),
        )
      : h('div', { className: 'border border-border rounded-lg p-4 mb-5 bg-card' },
          h('div', { className: 'flex items-center gap-2 font-semibold text-text-strong' },
            h(Clock3, { size: 17 }), 'Auto-approve window'),
          h('div', { className: 'text-[12px] text-muted mt-1 mb-3' },
            'Temporarily approve every new privileged command. The window resets when the backend restarts.'),
          h('div', {
            className: 'gap-1.5',
            style: { display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))' },
          },
            ...DURATIONS.map(([seconds, label]) => h('button', {
              key: seconds,
              disabled: windowBusy,
              onClick: () => { setDuration(seconds); setConfirming(false) },
              className: `py-1.5 px-1 rounded-md text-[11px] font-semibold border cursor-pointer transition-colors ${duration === seconds ? 'border-accent bg-accent-subtle text-accent' : 'border-border bg-bg-elevated text-muted hover:text-text'}`,
            }, label)),
          ),
          confirming
            ? h('div', { className: 'mt-3 rounded-md border border-warning bg-warn-subtle p-3' },
                h('div', { className: 'text-[12px] font-semibold text-text-strong' },
                  `Enable unattended privileged commands for ${durationName}?`),
                h('div', { className: 'text-[11px] text-muted mt-1' },
                  'New requests will be approved immediately. You can disarm at any time.'),
                h('div', { className: 'flex gap-2 mt-3' },
                  h('button', {
                    disabled: windowBusy,
                    onClick: () => changeWindow({ action: 'arm', durationSeconds: duration }),
                    className: 'flex-1 py-2 rounded-md text-sm font-semibold cursor-pointer hover:opacity-90 disabled:opacity-50 inline-flex items-center justify-center gap-2',
                    style: { background: 'var(--warn)', color: 'var(--bg)', border: '1px solid var(--warn)' },
                  }, h(ShieldAlert, { size: 15 }), `Enable for ${durationName}`),
                  h('button', {
                    disabled: windowBusy,
                    onClick: () => setConfirming(false),
                    className: 'px-4 py-2 rounded-md border border-border bg-bg-elevated text-sm text-text cursor-pointer hover:bg-bg-hover disabled:opacity-50',
                  }, 'Cancel'),
                ),
              )
            : h('button', {
                disabled: windowBusy,
                onClick: () => setConfirming(true),
                className: 'w-full mt-3 py-2.5 rounded-md text-sm font-semibold cursor-pointer hover:opacity-90 disabled:opacity-50 inline-flex items-center justify-center gap-2',
                style: { background: 'var(--warn)', color: 'var(--bg)', border: '1px solid var(--warn)' },
              }, h(ShieldAlert, { size: 16 }), 'Arm auto-approve'),
        ),

    pending
      ? h('div', { className: 'border-2 border-warning rounded-lg p-5 mb-6 bg-card shadow-sm' },
          h('div', { className: 'text-sm font-semibold text-text-strong mb-3 flex items-center gap-2' },
            h(Terminal, { size: 16 }), 'Approval requested',
            h('span', { className: 'text-muted font-normal ml-auto text-[12px]' }, pending.ts)),
          h('div', { className: 'space-y-2 mb-4' },
            h('div', { className: 'flex items-center gap-2 text-sm' },
              h(Terminal, { size: 14, className: 'text-muted shrink-0' }),
              h('code', { className: 'text-text font-mono bg-bg-elevated px-2 py-1 rounded break-all' }, request.cmd || pending.text)),
            request.cwd && h('div', { className: 'flex items-center gap-2 text-[13px] text-muted' },
              h(FolderOpen, { size: 14, className: 'shrink-0' }), request.cwd),
          ),
          h('div', { className: 'flex gap-3' },
            h('button', {
              disabled: busy,
              onClick: () => respond(pending.id, 'y'),
              className: 'flex-1 py-2.5 rounded-md bg-ok font-semibold text-sm cursor-pointer hover:opacity-90 disabled:opacity-50 inline-flex items-center justify-center gap-2',
              style: { color: 'var(--bg)' },
            }, h(Check, { size: 16 }), 'Approve'),
            h('button', {
              disabled: busy,
              onClick: () => respond(pending.id, 'n'),
              className: 'flex-1 py-2.5 rounded-md bg-danger font-semibold text-sm cursor-pointer hover:opacity-90 disabled:opacity-50 inline-flex items-center justify-center gap-2',
              style: { color: 'var(--bg)' },
            }, h(X, { size: 16 }), 'Deny'),
          ),
        )
      : h('div', { className: 'border border-border rounded-lg p-8 mb-6 bg-card text-center text-muted text-sm' },
          'No pending requests. Waiting for a privileged command request…'),

    h('div', null,
      h('div', { className: 'text-[13px] font-semibold uppercase tracking-[.04em] text-muted mb-2' }, 'Recent'),
      (state.log || []).length === 0
        ? h('div', { className: 'text-muted text-sm py-2' }, 'Nothing yet.')
        : h('div', { className: 'space-y-1' },
            ...(state.log || []).map((entry, index) => {
              const resolved = parseReq(entry.text)
              const approved = entry.decision === 'y'
              const label = entry.automatic ? 'AUTO-APPROVED' : approved ? 'APPROVED' : 'DENIED'
              const colors = entry.automatic
                ? 'bg-warn-subtle text-warning'
                : approved ? 'bg-ok-subtle text-ok' : 'bg-danger-subtle text-danger'
              return h('div', { key: index, className: 'flex items-center gap-3 py-2 px-3 rounded-md bg-bg-elevated border border-border' },
                h('span', { className: 'text-[12px] text-muted shrink-0 font-mono' }, entry.ts),
                h('span', { className: `text-[10px] px-2 py-[2px] rounded-full font-bold shrink-0 ${colors}` }, label),
                h('code', { className: 'text-[13px] text-text font-mono truncate' }, resolved.cmd || entry.text.replace(/\n/g, ' ')),
              )
            })),
    ),
  )
}

export default SudoApprovals
