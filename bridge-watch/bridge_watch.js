// New Light: keep the ATEM Mini Extreme streaming to the ATEM Streaming Bridge.
//
// The Bridge gets its network address from the router (DHCP), so after it is unplugged it can
// come back at a different address (2026-11-19: 192.168.11.30 -> .35), and the Extreme then
// streams to nowhere. Every 20 seconds this helper:
//   1. finds the Bridge by its hardware (MAC) address, wherever it is on 192.168.11.x,
//   2. if the Extreme's stream address doesn't match, fixes it (keeps the key and bitrates),
//   3. if the Bridge is up and the Extreme is not streaming, starts the stream.
// It only touches the Extreme's stream when that stream is the Bridge one (rtmp://192.168.11.x),
// never another service such as StreamSpot. Local network only; nothing goes to the internet.
//
// Run with Companion's bundled node (it has the ATEM library):
//   "C:\Program Files\Companion\resources\node-runtimes\node26\node.exe" bridge_watch.js
// Started at Windows login by the "New Light Bridge Watch" scheduled task (install.ps1).
const path = require('path')
const fs = require('fs')
const net = require('net')
const { execFile } = require('child_process')
const { Atem } = require(path.join(process.env.APPDATA, 'companion/modules/bmd-atem-3.21.2/node_modules/atem-connection'))

const EXTREME = '192.168.11.111'
const BRIDGE_MAC = '7c-2e-0d-18-59-1d'
const SUBNET = '192.168.11.'
const EVERY_MS = 20000
const STATE_FILE = path.join(__dirname, 'last_bridge_ip.txt')
const LOG = path.join(__dirname, 'bridge_watch.log')
const ONCE = process.argv.includes('--once')

const log = (msg) => {
  const line = `${new Date().toLocaleString('en-US', { hour12: false })} ${msg}\n`
  try {
    if (fs.existsSync(LOG) && fs.statSync(LOG).size > 1_000_000) fs.writeFileSync(LOG, fs.readFileSync(LOG).subarray(-200_000))
    fs.appendFileSync(LOG, line)
  } catch {}
  process.stdout.write(line)
}

const rtmpOpen = (ip, ms = 700) => new Promise((resolve) => {
  const s = net.connect({ host: ip, port: 1935 })
  const done = (ok) => { s.destroy(); resolve(ok) }
  s.setTimeout(ms, () => done(false))
  s.once('connect', () => done(true))
  s.once('error', () => done(false))
})

const arpTable = () => new Promise((resolve) => execFile('arp', ['-a'], { windowsHide: true }, (e, out) => resolve(out || '')))
const macOf = async (ip) => {
  const m = (await arpTable()).split('\n').find((l) => l.trim().startsWith(ip + ' '))
  return m ? m.trim().split(/\s+/)[1].toLowerCase() : null
}

async function findBridge() {
  // fast path: the last address it had
  const last = fs.existsSync(STATE_FILE) ? fs.readFileSync(STATE_FILE, 'utf8').trim() : ''
  if (last && (await rtmpOpen(last)) && (await macOf(last)) === BRIDGE_MAC) return last
  // already in the ARP table?
  for (const l of (await arpTable()).split('\n')) {
    const [ip, mac] = l.trim().split(/\s+/)
    if (mac && mac.toLowerCase() === BRIDGE_MAC && (await rtmpOpen(ip))) return ip
  }
  // sweep the subnet for anything listening on RTMP (1935), then check its MAC
  const ips = Array.from({ length: 254 }, (_, i) => SUBNET + (i + 1))
  const open = []
  await Promise.all(ips.map(async (ip) => { if (await rtmpOpen(ip, 600)) open.push(ip) }))
  for (const ip of open) if ((await macOf(ip)) === BRIDGE_MAC) return ip
  return null
}

const atem = new Atem({ disableMultithreaded: true })
let atemUp = false
atem.on('connected', () => { atemUp = true; log('connected to the Extreme') })
atem.on('disconnected', () => { atemUp = false; log('lost the Extreme (will keep retrying)') })
atem.on('error', () => {})
atem.connect(EXTREME)

let lastSeen = null
let busy = false
async function check() {
  if (busy) return
  busy = true
  try {
    const ip = await findBridge()
    if (!ip) {
      if (lastSeen !== 'none') log('Bridge not found on the network (unplugged?)')
      lastSeen = 'none'
      return
    }
    if (lastSeen !== ip) { log(`Bridge found at ${ip}`); lastSeen = ip; fs.writeFileSync(STATE_FILE, ip) }
    if (!atemUp || !atem.state?.streaming) return
    const st = atem.state.streaming
    const svc = st.service || {}
    if (!/^rtmp:\/\/192\.168\.11\.\d+/.test(svc.url || '')) return // not the Bridge stream: leave it alone
    const want = `rtmp://${ip}:1935/app`
    if (svc.url !== want) {
      if (st.status?.state !== 1) { await atem.stopStreaming().catch(() => {}); await new Promise((r) => setTimeout(r, 2000)) }
      await atem.setStreamingService({ serviceName: svc.serviceName || 'ATEM Streaming Bridge', url: want, key: svc.key, bitrates: svc.bitrates })
      log(`Extreme stream address changed ${svc.url} -> ${want}`)
      await new Promise((r) => setTimeout(r, 1000))
    }
    if (atem.state.streaming.status?.state === 1) { // 1 = idle
      await atem.startStreaming()
      log('Extreme was not streaming to the Bridge: started it')
    }
  } catch (e) {
    log('check failed: ' + (e && e.message ? e.message : e))
  } finally {
    busy = false
  }
}

// Only one copy runs (a second copy can't take this port and quits).
if (!ONCE) {
  const lock = net.createServer()
  lock.on('error', () => { process.stdout.write('already running\n'); process.exit(0) })
  lock.listen(48891, '127.0.0.1')
}

log('bridge watch starting')
setTimeout(async function loop() {
  await check()
  if (ONCE) { setTimeout(() => process.exit(0), 3000); return }
  setTimeout(loop, EVERY_MS)
}, 4000)
