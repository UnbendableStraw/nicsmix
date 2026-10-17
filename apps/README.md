# Troubadour companion apps

Two small native apps — `ios/` (SwiftUI) and `android/` (Kotlin + Compose) — for setting up and using Troubadour
cells from a phone. Both are thin shells around the agent that runs on every cell (port 8080): the phone is
only ever talking to the speakers on the LAN, there is no account and nothing leaves the network.

## What is native, what is the agent's page

| | native | embedded (the cell serves it) |
|---|---|---|
| Finding the cells | **Bonjour / NSD** for `_syng._tcp` (celldirector advertises it, stock or Troubadour), then `/api/discover` on the first agent found enumerates the whole LAN. The USB setup link `10.86.0.1` is always tried; addresses can also be typed | |
| Playing | **Now Playing**: artwork, transport, sources, volume slider with haptic ticks, mute, Sound mode (Syng / Direct), Scenes | |
| Sound field | **Metal**: the app's own renderer — room disc, capped-torus aura, 24k GPU particles, glass orbs, speaker rings, volume rim — with direct touch (drag the puck, pull the dot out to go spatial, aim the notch, widen a dot, pinch for width, twist to aim, double-tap to centre) and haptics on snap | |
| Speakers | pucks, roles, Ping, long-press to name a cell | |
| Setup | | `/setup`: rooms, Wi-Fi & network, room layout gestures, Soundcheck, firmware updates. The page's file picker is wired through, so a `.img.gz` can be installed from the phone's Files/Downloads |
| Automation | iOS: Siri / Shortcuts (room volume, scene, play/pause). Android: Quick Settings play/pause tile, launcher shortcuts (pause, mute) | |
| Home screen | **Widgets** on both: *Sound Field* (the room from above with the puck / spatial dots, buttons to centre, switch classic/spatial, widen or narrow, nudge) and *LEDs* (current mode, a button per reactive mode, off). They act on the room last opened in the app | |

The split is deliberate. The setup page is a long tail of settings and one source of truth, so an agent update
changes it in the apps without an app release. Everything a hand touches is native: discovery without IPs, a
volume slider you can feel, Siri and the quick-settings shade — and since iOS 0.2 the sound field itself, which
is the one screen where a WebView was costing real frames. The native field runs the same physics as the web one
(`field/field.js`, ported shader for shader) with the particle simulation moved into a Metal compute kernel and
the frame interpolating between sim steps, so it holds 120 Hz on a ProMotion phone instead of ~30.

## Build

- iOS: `brew install xcodegen; cd apps/ios; xcodegen generate; open Troubadour.xcodeproj` — pick your team, run on a device. iOS 16+. No dependencies. See `ios/README.md`.
- Android: open `apps/android` in Android Studio (or `gradle assembleDebug` with an SDK and `local.properties`). minSdk 26. See `android/README.md`.

Neither app is in a store; they are side-loaded developer builds, like the firmware itself. The API both use is
documented in `docs/SETUP.md` (the table of `/api/...` endpoints).
