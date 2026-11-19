# Bridge Watch

Keeps the **ATEM Mini Extreme** streaming to the **ATEM Streaming Bridge** (local network only — nothing goes to the internet).

**Why:** the Bridge gets its address from the router, so after it's unplugged it can come back at a different one (on 2026-11-19 it moved from 192.168.11.30 to .35) and the Extreme kept streaming to the old address.

**What it does, every 20 seconds:**
1. Finds the Bridge by its hardware (MAC) address `7c-2e-0d-18-59-1d`, wherever it is on 192.168.11.x.
2. If the Extreme's stream address doesn't match, fixes it (keeps the stream key and bitrates).
3. If the Bridge is up and the Extreme isn't streaming, starts the stream.

It only changes the Extreme's stream while that stream points at a 192.168.11.x address (the Bridge), never another service such as StreamSpot.

Note: if you stop the Bridge stream on purpose, the helper starts it again within about 20 seconds. To pause it, end the task in Task Scheduler.

- Starts at Windows login: scheduled task **New Light Bridge Watch** (set up by `install.ps1`).
- Log: `bridge_watch.log` (this folder). Last Bridge address: `last_bridge_ip.txt`.
- Run one check by hand: `"C:\Program Files\Companion\resources\node-runtimes\node26\node.exe" bridge_watch.js --once`
- Uses the ATEM library that ships with Companion (`%APPDATA%\companion\modules\bmd-atem-3.21.2`). If Companion updates that module to a new version folder, update the path at the top of `bridge_watch.js`.
