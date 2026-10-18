# Troubadour for iOS

A thin native shell around the agent that runs on every Troubadour cell. Native where the phone beats a browser
tab, the agent's own pages where they are already the right thing:

| native | embedded (the agent's pages) |
|---|---|
| **Bonjour discovery** — `_syng._tcp` is what celldirector advertises; the first agent found enumerates the LAN (`/api/discover`). No IP typing; the USB setup link `10.86.0.1` is tried too | **The speaker's own page** — one tap from Setup, for anything the native screens do not surface (tone controls, scenes scheduling, cloud block, the raw room document) |
| **Room** — the sound field (Metal, `Sources/Field/`, see below) with what is playing under it: artwork, transport, sources, mute, sound mode, scenes | |
| **Setup** — Room (name, members, primary), Layout & Soundcheck, LEDs, Network (Wi-Fi, radios, the wired cell-to-cell link with live PTP), Firmware (install, roll back, upload a `.img.gz` from Files), System (live fleet, stats, sleep, housekeeping) | |
| **Speakers** — pucks, roles, Ping, long-press to name | |
| **Shortcuts / Siri** — room volume, play a scene, play/pause | |
| **Widgets** — sound field and LED controls on the home screen | |

## The sound field

`Sources/Field/` is the field, drawn by the GPU rather than embedded in a WebView — a port of the agent's
`field/field.js`, which is itself a reproduction of the app's renderer (`docs/SOUND-FIELD.md`):

- `FieldShaders.metal` — four passes: the **scene** (room disc, the capped-torus aura recoloured per source,
  flurry blobs, the PulseField halo) at 0.62 resolution because it is all soft gradients; a **blit** up to full
  size; the **particles** — a compute kernel for the simulation (the same ProtoTools physics: per-band puff
  impulses, float time, spring-back, damping) plus additive points displaced by the pulse texture; and the
  **overlay**, which refracts the scene through the app's own 64-entry lens table to draw the glass puck, notch,
  handles and lift dot, then the speaker rings and the volume rim.
- `FieldModel.swift` — state and the app's spring (stiffness 218.566, damping 29.568), the SSE audio feed
  (`/api/viz/stream`), snap-to-speaker, and throttled `/api/soundfield` + `/api/volume` posts. The palette
  follows the **speakers' LEDs** when they are lit (the agent puts their live colour on the viz feed, b52);
  the source's own palette is the fallback.
- `FieldRenderer.swift` — the passes, with the simulation stepped at the app's fixed 30 Hz and frames
  interpolating between steps, so motion is smooth at 120 Hz without changing the physics.
- `FieldView.swift` — the Metal view plus touch handling (UIKit touches, not SwiftUI gestures, so a drag tracks
  the finger with no recognizer delay): drag the puck, pull the dot above it out to go spatial, drag the notch to
  aim, a dot to widen, **pinch** for width, **two-finger twist** to aim, **double-tap** to centre, and the rim at
  the bottom for volume — with a haptic tick when the puck snaps onto a speaker and when spatial engages.

## Setup, natively

`Sources/Views/SetupView.swift` is a short list that pushes a screen per area; each one is an
`Endpoint` (one `GET /api/…`, refreshed on a timer, posts back) plus a layout, so the agent's JSON is
read straight out rather than modelled twice. `Sources/Views/LayoutView.swift` is the room plan:
drag a speaker to move it, tap it to turn it, fit a circle, hear it, then **Soundcheck ×3**, with the
pass counter, the residual and the per-pair spread / reciprocity the b51 solver reports.

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
documented in `docs/SETUP.md`. The setup page is served by the cells, so an agent update updates that screen
without an app release.
