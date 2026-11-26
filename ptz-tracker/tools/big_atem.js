// Talks to the ATEM (2 M/E Constellation HD) for the New Light Cameras app.
// Runs on Companion's bundled Node with the atem-connection library from Companion's ATEM
// module, so nothing extra has to be installed.
//
//   node big_atem.js <ip> status                     -> JSON: model, format, program/preview, input names
//   node big_atem.js <ip> rename <input> <long> <short>
//   node big_atem.js <ip> stills <outdir> <ffmpeg>   -> a still of every camera input (inNN.jpg + stills.json)
//
// The ATEM can't send live video over the network. It CAN capture a still of its M/E 1 program into
// the media pool and hand it over, so "stills" cuts M/E 1 program through every input in turn
// (about 4 s each) and puts the original program back at the end. Setup only, never during a service.
const fs = require('fs')
const path = require('path')
const { execFileSync } = require('child_process')

// Companion's modules: %APPDATA%\companion\modules on the PC,
// ~/Library/Application Support/companion/modules on the Mac.
const mods = process.platform === 'darwin'
  ? path.join(require('os').homedir(), 'Library', 'Application Support', 'companion', 'modules')
  : path.join(process.env.APPDATA, 'companion', 'modules')
const modDir = fs.readdirSync(mods).filter((d) => d.startsWith('bmd-atem-'))
  .sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).pop()
const LIB = path.join(mods, modDir, 'node_modules', 'atem-connection')
const { Atem } = require(LIB)
const K = require(path.join(LIB, 'dist', 'lib', 'converters', 'colorConstants')).ColorConvertConstantsBT709

const [ip, cmd, ...args] = process.argv.slice(2)
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
const out = (obj) => { process.stdout.write(JSON.stringify(obj)); process.exit(0) }
const fail = (msg) => { process.stdout.write(JSON.stringify({ error: msg })); process.exit(1) }

// The library's own RGBA conversion crashes on the ATEM's stills (alpha out of range), so convert here.
const clamp = (v) => (v < 0 ? 0 : v > 255 ? 255 : Math.round(v))
function yuvToRgb(data) {
  const rgb = Buffer.alloc((data.length / 4) * 3)
  const split = (r) => [(((r & 0x3ff) << 6) - K.YOffset) / K.YRange, (((r & 0xffc00) >> 4) - K.CbCrOffset) / K.HalfCbCrRange]
  for (let i = 0, o = 0; i < data.length; i += 8) {
    const [ya, cb] = split(data.readUint32BE(i))
    const [yb, cr] = split(data.readUint32BE(i + 4))
    for (const y of [ya, yb]) {
      rgb[o++] = clamp(y + K.KRi * cr)
      rgb[o++] = clamp(y - K.KRKRioKG * cr - K.KBKBioKG * cb)
      rgb[o++] = clamp(y + K.KBi * cb)
    }
  }
  return rgb
}

const atem = new Atem({ disableMultithreaded: true })
setTimeout(() => fail(`The ATEM didn't answer at ${ip}`), cmd === 'stills' ? 240000 : 8000)

atem.on('connected', async () => {
  await wait(600)
  const s = atem.state
  const cameraInputs = Object.values(s.inputs).filter((i) => i.internalPortType === 0)
  try {
    if (cmd === 'status') {
      out({
        product: s.info.productIdentifier,
        videoMode: s.settings.videoMode,
        me: s.video.mixEffects.map((m) => ({ program: m.programInput, preview: m.previewInput })),
        inputs: cameraInputs.map((i) => ({ id: i.inputId, name: i.longName, short: i.shortName })),
        names: Object.fromEntries(Object.values(s.inputs).map((i) => [i.inputId, i.longName])),
      })
    } else if (cmd === 'rename') {
      const [id, long, short] = args
      await atem.setInputSettings({ longName: long.slice(0, 20), shortName: short.slice(0, 4) }, Number(id))
      await wait(500)
      const i = atem.state.inputs[id]
      out({ ok: true, id: Number(id), name: i.longName, short: i.shortName })
    } else if (cmd === 'stills') {
      const [dir, ffmpeg] = args
      fs.mkdirSync(dir, { recursive: true })
      const used = () => atem.state.media.stillPool.map((p, i) => (p && p.isUsed ? i : -1)).filter((i) => i >= 0)
      const before0 = new Set(used())
      const orig = { program: s.video.mixEffects[0].programInput, preview: s.video.mixEffects[0].previewInput }
      // The still is the PROGRAM picture, keys included, so take every M/E 1 upstream key and
      // every downstream key off air while scanning (otherwise lyrics show on every input),
      // and put them back exactly as they were at the end.
      const uskOn = s.video.mixEffects[0].upstreamKeyers.map((k) => !!(k && k.onAir))
      const dskOn = s.video.downstreamKeyers.map((k) => !!(k && k.onAir))
      const restoreKeys = async () => {
        for (const [i, on] of uskOn.entries()) if (on) await atem.setUpstreamKeyerOnAir(true, 0, i)
        for (const [i, on] of dskOn.entries()) if (on) await atem.setDownstreamKeyOnAir(true, i)
      }
      for (const [i, on] of uskOn.entries()) if (on) await atem.setUpstreamKeyerOnAir(false, 0, i)
      for (const [i, on] of dskOn.entries()) if (on) await atem.setDownstreamKeyOnAir(false, i)
      await wait(400)
      const result = {}
      try {
      for (const inp of cameraInputs) {
        const n = inp.inputId
        await atem.changeProgramInput(n, 0)
        await wait(700)
        const before = new Set(used())
        await atem.captureMediaPoolStill()
        await wait(2500)
        const slot = used().find((i) => !before.has(i))
        if (slot === undefined) { result[n] = { error: 'no still captured' }; continue }
        let raw = null
        for (let t = 0; t < 4 && !raw; t++) {
          try { raw = await atem.downloadStill(slot, 'yuv') } catch { await wait(1500) }
        }
        if (!before0.has(slot)) await atem.clearMediaPoolStill(slot)
        if (!raw) { result[n] = { error: 'download failed' }; continue }
        const rgb = yuvToRgb(raw)
        let sum = 0, count = 0
        for (let p = 0; p < rgb.length; p += 3 * 499) { sum += rgb[p] + rgb[p + 1] + rgb[p + 2]; count += 3 }
        const brightness = Math.round(sum / count)
        const file = `in${String(n).padStart(2, '0')}.jpg`
        const tmp = path.join(dir, `in${n}.rgb`)
        fs.writeFileSync(tmp, rgb)
        execFileSync(ffmpeg, ['-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', `1920x${rgb.length / 3 / 1920}`,
          '-i', tmp, '-vf', 'scale=480:-2', '-q:v', '4', path.join(dir, file)])
        fs.unlinkSync(tmp)
        result[n] = { file, brightness, picture: brightness > 3 }
        await wait(300)
      }
      } finally {
        await atem.changeProgramInput(orig.program, 0)
        await atem.changePreviewInput(orig.preview, 0)
        await restoreKeys()
        await wait(400)
      }
      const summary = { taken: Date.now() / 1000, videoMode: s.settings.videoMode, inputs: result }
      fs.writeFileSync(path.join(dir, 'stills.json'), JSON.stringify(summary, null, 1))
      out(summary)
    } else {
      fail('unknown command')
    }
  } catch (e) {
    fail(e.message)
  }
})
atem.connect(ip)
