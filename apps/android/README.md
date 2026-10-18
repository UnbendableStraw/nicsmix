# Troubadour for Android

Kotlin + Jetpack Compose, minSdk 26 (Android 8), no Gradle wrapper shipped — use Android Studio's, or a system
Gradle 8.x.

## Build

Android Studio: *File → Open* `apps/android`, let it sync, run on a phone on the same Wi-Fi as the cells.

Command line:
```sh
cd apps/android
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties     # wherever your SDK is
gradle assembleDebug                                              # → app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
```
Needs platform 34 and build-tools 34 (`sdkmanager "platforms;android-34" "build-tools;34.0.0"`).

## Layout

- `net/Agent.kt` — the whole API client (OkHttp + org.json): `get`, `post`, `upload`, and the hand-decoded models.
- `net/Fleet.kt` — discovery: NSD `_syng._tcp` → seeds → `/api/discover` on the first agent that answers; a
  multicast lock so mDNS actually arrives; manual addresses are kept in prefs.
- `ui/RoomsScreen.kt` — rooms list, theme (`Theme`, `Card`, `Pill`, `Puck`).
- `ui/RoomScreen.kt` — a room: **Room** (the sound field with a compact player under it), Speakers, Setup.
- `ui/SetupScreen.kt` — the native setup screens: Room, Layout & Soundcheck, LEDs, Sound & controls, Scenes,
  Network, Firmware, System — each one an `Ep` (one `GET /api/…`, polled, posts back) plus a layout. The cell's
  own `/setup` page is the last row, with its file chooser routed through `MainActivity`.
- `field/` — the sound field in **OpenGL ES 3.1**, the counterpart of the iOS Metal renderer:
  `FieldShaders.kt` (scene, blit, the ProtoTools simulation as a compute shader, additive points, and the
  overlay that refracts the glass orbs through the app's lens table), `FieldRenderer.kt` (the four passes, the
  sim stepped at the app's fixed 30 Hz with frames interpolating), `FieldModel.kt` (state, the app's spring,
  the SSE audio feed, palettes — including the live LED colour from agent b52) and `FieldView.kt`
  (`GLSurfaceView` + touch straight to the model, pinch for width, two-finger twist to aim, double-tap to
  centre). A device without ES 3.1 compute falls back to the cell's WebGL field.
- `PlayPauseTile.kt` — Quick Settings tile acting on the last room opened.
- `widget/` — two Glance home-screen widgets (Sound Field: picture + centre / classic–spatial / width / nudge buttons; LEDs: mode
  buttons + off), acting on the last room opened; `res/xml/widget_*.xml` are their provider specs.
- `res/xml/shortcuts.xml` — launcher long-press shortcuts (pause, mute), handled in `MainActivity`.

Cleartext HTTP to the LAN is allowed by `res/xml/network_security_config.xml`; the agent speaks plain HTTP on
port 8080 and never leaves the network.
