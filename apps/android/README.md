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
- `ui/RoomScreen.kt` — a room: Now Playing (native), Field (WebView `/#fieldwrap`), Speakers, Setup (WebView
  `/setup`, file chooser routed through `MainActivity` so firmware uploads work).
- `PlayPauseTile.kt` — Quick Settings tile acting on the last room opened.
- `widget/` — two Glance home-screen widgets (Sound Field: picture + centre / classic–spatial / width / nudge buttons; LEDs: mode
  buttons + off), acting on the last room opened; `res/xml/widget_*.xml` are their provider specs.
- `res/xml/shortcuts.xml` — launcher long-press shortcuts (pause, mute), handled in `MainActivity`.

Cleartext HTTP to the LAN is allowed by `res/xml/network_security_config.xml`; the agent speaks plain HTTP on
port 8080 and never leaves the network.
