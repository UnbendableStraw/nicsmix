# Troubadour companion apps

Two small native apps — `ios/` (SwiftUI) and `android/` (Kotlin + Compose) — for setting up and using Troubadour
cells from a phone. Both are thin shells around the agent that runs on every cell (port 8080): the phone is
only ever talking to the speakers on the LAN, there is no account and nothing leaves the network.

## What is native, what is the agent's page

| | native | embedded (the cell serves it) |
|---|---|---|
| Finding the cells | **Bonjour / NSD** for `_syng._tcp` (celldirector advertises it, stock or Troubadour), then `/api/discover` on the first agent found enumerates the whole LAN. The USB setup link `10.86.0.1` is always tried; addresses can also be typed | |
| Playing | **Now Playing**: artwork, transport, sources, volume slider with haptic ticks, mute, Sound mode (Syng / Direct), Scenes | |
| Sound field | | the dashboard scrolled to the field (WebGL) |
| Speakers | pucks, roles, Ping, long-press to name a cell | |
| Setup | | `/setup`: rooms, Wi-Fi & network, room layout gestures, Soundcheck, firmware updates. The page's file picker is wired through, so a `.img.gz` can be installed from the phone's Files/Downloads |
| Automation | iOS: Siri / Shortcuts (room volume, scene, play/pause). Android: Quick Settings play/pause tile, launcher shortcuts (pause, mute) | |
| Home screen | **Widgets** on both: *Sound Field* (the room from above with the puck / spatial dots, buttons to centre, switch classic/spatial, widen or narrow, nudge) and *LEDs* (current mode, a button per reactive mode, off). They act on the room last opened in the app | |

The split is deliberate: the sound field and setup pages are one source of truth and already in the Syng look,
so an agent update changes them in the apps without an app release. The native parts are the ones where a phone
beats a browser tab — discovery without IPs, a volume slider you can feel, Siri and the quick-settings shade.

## Build

- iOS: `brew install xcodegen; cd apps/ios; xcodegen generate; open Troubadour.xcodeproj` — pick your team, run on a device. iOS 16+. No dependencies. See `ios/README.md`.
- Android: open `apps/android` in Android Studio (or `gradle assembleDebug` with an SDK and `local.properties`). minSdk 26. See `android/README.md`.

Neither app is in a store; they are side-loaded developer builds, like the firmware itself. The API both use is
documented in `docs/SETUP.md` (the table of `/api/...` endpoints).
