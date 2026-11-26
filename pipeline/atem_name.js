// Sets the ATEM recording filename for the day, so recordings come out named
// "Practice_10_28_2026" (Wednesdays) or "Worship_11_1_2026" (Sundays).
//
//   atem_name.cmd              name for today: Wednesday -> Practice, Sunday -> Worship (other days: nothing)
//   atem_name.cmd --name X     set this exact name instead
//   atem_name.cmd --read       just print each switcher's current recording name
//   --wait-min N               keep retrying a switcher that's off/unreachable for N minutes (default 240)
//
// Talks straight to the switchers (UDP 9910) with the atem-connection library that ships with
// Companion's ATEM module, run on Companion's bundled Node — so nothing extra to install.
// Switchers come from config.json "atem_hosts". Each run is appended to work/atem_name.log.
const fs = require('fs')
const path = require('path')

const ROOT = path.join(__dirname, '..')
const CONFIG = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'))
const HOSTS = CONFIG.atem_hosts || { 'ATEM Mini Extreme': '192.168.11.111' }
const LOG = path.join(ROOT, 'work', 'atem_name.log')

function log(msg) {
  const line = `${new Date().toLocaleString()}  ${msg}`
  console.log(line)
  try { fs.mkdirSync(path.dirname(LOG), { recursive: true }); fs.appendFileSync(LOG, line + '\n') } catch {}
}

function atemLib() {
  // Companion keeps the module in a versioned folder (bmd-atem-3.21.2); use the newest one.
  // Companion's modules: %APPDATA%\companion\modules on the PC,
  // ~/Library/Application Support/companion/modules on the Mac.
  const mods = process.platform === 'darwin'
    ? path.join(require('os').homedir(), 'Library', 'Application Support', 'companion', 'modules')
    : path.join(process.env.APPDATA, 'companion', 'modules')
  const dirs = fs.readdirSync(mods).filter((d) => d.startsWith('bmd-atem-'))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
  if (!dirs.length) throw new Error(`No Companion ATEM module in ${mods}`)
  return require(path.join(mods, dirs[dirs.length - 1], 'node_modules', 'atem-connection'))
}

function todaysName(now = new Date()) {
  const date = `${now.getMonth() + 1}_${now.getDate()}_${now.getFullYear()}`
  if (now.getDay() === 3) return `Practice_${date}`
  if (now.getDay() === 0) return `Worship_${date}`
  return null
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// One attempt: connect, optionally set the name, read it back. Resolves to the filename or throws.
function attempt(Atem, host, name) {
  return new Promise((resolve, reject) => {
    const atem = new Atem()
    const done = (err, val) => { clearTimeout(timer); atem.disconnect().catch(() => {}); err ? reject(err) : resolve(val) }
    const timer = setTimeout(() => done(new Error('no answer (switcher off or unreachable?)')), 8000)
    atem.on('error', () => {})
    atem.on('connected', async () => {
      try {
        if (name) {
          await atem.setRecordingSettings({ filename: name })
          await sleep(1000) // let the switcher echo the new state back
        }
        const current = atem.state.recording?.properties?.filename
        if (name && current !== name) throw new Error(`switcher still says "${current}"`)
        done(null, current)
      } catch (e) { done(e) }
    })
    atem.connect(host).catch((e) => done(e))
  })
}

async function main() {
  const args = process.argv.slice(2)
  const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : undefined }
  const read = args.includes('--read')
  const name = read ? null : (opt('--name') || todaysName())
  const waitMin = Number(opt('--wait-min') ?? 240)
  if (!read && !name) { log('Not a Wednesday or Sunday - nothing to set.'); return 0 }

  const { Atem } = atemLib()
  const pending = new Map(Object.entries(HOSTS))
  const deadline = Date.now() + waitMin * 60000
  let failed = 0
  if (name) log(`Setting recording name "${name}" on: ${[...pending.keys()].join(', ')}`)
  for (;;) {
    for (const [label, host] of [...pending]) {
      try {
        const got = await attempt(Atem, host, name)
        log(`  ${label} (${host}): ${name ? 'set to' : 'currently'} "${got}"`)
        pending.delete(label)
      } catch (e) {
        if (read || Date.now() >= deadline) { log(`  ${label} (${host}): FAILED - ${e.message}`); pending.delete(label); failed++ }
      }
    }
    if (!pending.size) break
    await sleep(5 * 60000) // a switcher that's still off gets another try every 5 minutes
  }
  return failed ? 1 : 0
}

main().then((code) => process.exit(code), (e) => { log(`ERROR: ${e.stack || e}`); process.exit(1) })
