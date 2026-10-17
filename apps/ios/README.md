# Troubadour for iOS

A thin native shell around the agent that runs on every Troubadour cell. Native where the phone beats a browser
tab, the agent's own pages where they are already the right thing:

| native | embedded (the agent's pages) |
|---|---|
| **Bonjour discovery** — `_syng._tcp` is what celldirector advertises; the first agent found enumerates the LAN (`/api/discover`). No IP typing; the USB setup link `10.86.0.1` is tried too | **Sound field** — the dashboard, scrolled to the field (WebGL, the app's own renderer) |
| **Now Playing** — artwork, transport, sources, volume slider with haptics, mute, Sound mode, Scenes | **Setup** — rooms, Wi-Fi, room layout gestures, Soundcheck, firmware updates (the file picker opens Files, so a `.img.gz` can be installed from the phone) |
| **Speakers** — pucks, roles, Ping, long-press to name | |
| **Shortcuts / Siri** — room volume, play a scene, play/pause | |
| **Widgets** — sound field and LED controls on the home screen | |

## Widgets

Two home-screen widgets (small and medium), acting on the room the app last opened: **Sound Field** (the room from
above with the puck or the spatial dots; buttons for Centre, Classic/Spatial, width ±, nudge) and **LEDs** (current
mode; a button per reactive mode and Off). Buttons need iOS 17; on iOS 16 the widgets are pictures that open the app.
They live in the `TroubadourWidgets` extension and share the room through the App Group `group.space.troubadour`
— Xcode registers it with your team on the first build (automatic signing; App Groups need a paid developer
account). Tapping a widget opens that room (`troubadour://room`).

## Build

```sh
brew install xcodegen
cd apps/ios && xcodegen generate && open Troubadour.xcodeproj
```
Pick your team under Signing & Capabilities, run on a device on the same Wi-Fi as the cells. iOS asks for
Local Network access on first launch — that is the Bonjour browse plus HTTP to the speakers; nothing leaves
the LAN. Requires iOS 16.

No third-party dependencies. `Sources/Model/Agent.swift` is the whole API client (≈100 lines); the endpoints are
documented in `docs/SETUP.md`. The pages the app embeds are served by the cells, so an agent update updates the
app's field and setup screens without an app release.
