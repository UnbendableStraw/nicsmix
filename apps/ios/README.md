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
| **Widgets** — sound field (room plan or the full visualizer) and LED controls on the home screen | |
| **iPad** — its own layout: rooms sidebar, field + controls side by side, two-pane Setup, keyboard shortcuts | |

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
pass counter, the residual and the per-pair spread / reciprocity the b51 solver reports. Since the parity pass
(2026-10-21) the app covers everything the speaker's own page does except its debug panel: see CHANGELOG b73 for
the list (progress and capabilities on the now-playing row, the Sound sheet's ceiling / spread / sleep / spatial
flags / room correction, live speakers, room create / move, LED tuning and animations, network radios and static
addresses, firmware distribution, system health and eMMC decode, circle and listener editing on the plan).

## iPad

Not the phone layout on a bigger screen (`Views/PadViews.swift`). Rooms are a sidebar (`NavigationSplitView`), and
a room is one screen: in landscape the sound field fills the left of the display and a panel beside it carries
everything else — now playing with large artwork and transport, the volume bar inline, Syng/Direct, Centre /
Spatial / Mute, sources, scenes, and the speakers (Ping; touch-and-hold or right-click to rename). In portrait the
field sits on top and the panels form two columns under it. **Setup** (the segmented control in the toolbar) is two
panes: the sections on the left, the open page on the right. The field gets 40k particles (its area is four or five
times the phone's), and the split view's own swipe is switched off while the field is on screen, so a drag near the
left edge moves the puck instead of pulling out the sidebar.

Pointer hover effects throughout; with a keyboard: Space play/pause, ⌘← / ⌘→ previous / next, ⌘0 centre the sound,
⌘S spatial on/off, ⌘M mute. In Split View, Slide Over or a narrow Stage Manager window the size class goes compact
and the app switches to the phone layout, which is what fits there. The visualizer widget adds the iPad-only extra
large size.

## Troubadour Kids

A second app in the same project (target `TroubadourKids`, scheme *Troubadour Kids*, its own icon on the home
screen): pick a room (found by scanning — no settings, no adding speakers), then the room's live, music-reactive
sound field and a big volume slider. Moving the puck, snapping, Spatial (the ✨ button), pinch and twist all work and
look as in the main app, but `FieldModel.visualOnly` keeps it on screen: nothing goes to the speakers, and the
room's real sound field never overrides the child's. Volume is the one real control: the whole slider is 25 % of the
speakers' volume (`KidsVolume.cap`), on top of the ceiling set in the main app (Setup → Sound & controls), which the
speakers enforce whatever sends the volume. It remembers the last room; with one room it opens straight into it.
For a child's iPad, Guided Access (Settings → Accessibility) keeps them in the app.

## Troubadour TV (Apple TV)

A third app in the same project (target `TroubadourTV`, scheme *Troubadour TV*, tvOS 17): pick a room once, and from
then on it opens straight into that room's live sound field, full screen — the same field model, Metal renderer and
shaders as the phone (40k particles). The field follows the room (the puck, its width and aim, Spatial, the layout and
the colours follow whatever the phone or the speakers are doing) and the Siri Remote steers it:

* ◀ ▶ previous / next room · ▲ ▼ volume (3 % steps, a toast shows the level) · ⏯ play / pause · click shows what's
  playing (room, track, ATMOS badge, the album art) for a few seconds · Menu back to the rooms.
* **Hold click** opens the controls, a panel on the right: Classic / Spatial, Centre the puck, **Move the puck…**
  (the touch surface becomes a trackpad for the puck — swipe to move, it snaps to a speaker like a drag does; click or
  Menu when done; the ring's edges nudge it too), Narrower / Wider and Aim ⟲ ⟳ in Spatial, Previous / Play / Next
  and Quieter / Louder, Colours follow (LEDs / album artwork / the source's palette), Particles (Syng app physics /
  previous puff physics, Drift gently when idle, Puff with the music, artwork in the puck), **Always show what's
  playing** (the panel never fades), Choose a room. The look settings are remembered on the Apple TV.
* The screen saver stays off while a field is showing. tvOS asks for Local Network access on first launch. Shared
  code builds for tvOS: no touch handling or haptics there (`#if os(tvOS)` in `FieldView.swift`), no WidgetKit. tvOS
  wants an app icon set for App Store builds; running from Xcode works without one.

## Widgets

Three home-screen widgets, acting on the room the app last opened: **Sound Field** (small, medium — the room from
above with the puck or the spatial dots; buttons for Centre, Classic/Spatial, width ±, nudge), **Sound Field
Visualizer** (small, medium, large — the app's own field: aura, particles, glass puck and speaker rings, in the colour
the speakers' LEDs are glowing; Centre, Classic/Spatial and width buttons on medium and large) and **LEDs** (small,
medium — current mode; a button per reactive mode and Off).

The visualizer can't run the Metal view: iOS draws a widget once, in the background, where GPU work isn't allowed.
`TroubadourWidgets/FieldStill.swift` is the same renderer on the CPU — the scene shader line for line, the particle
kernel stepped until the cloud settles into the field's shape, the glass orbs through the app's lens table — fed
one `/api/state` and one `/api/viz/frame` through the app's `FieldModel`. It is a still (~0.25 s to draw a large
widget); the app asks WidgetKit to redraw it two seconds after every field change, a widget button redraws it, and
otherwise it refreshes every 15 minutes. Buttons need iOS 17; on iOS 16 the widgets are pictures that open the app.
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
the LAN. Requires iOS 16 (the Apple TV app: tvOS 17, scheme *Troubadour TV*, run on an Apple TV paired with Xcode).

No third-party dependencies. `Sources/Model/Agent.swift` is the whole API client (≈100 lines); the endpoints are
documented in `docs/SETUP.md`. The setup page is served by the cells, so an agent update updates that screen
without an app release.
