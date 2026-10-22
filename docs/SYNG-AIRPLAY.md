# AirPlay on the Syng Cell: findings for Syng

Observed on Syng Cell Alpha speakers (stock `airplay2source`, sha256 `dbcfb3cf…`, built 2026-07-15) in a five-speaker
group playing AirPlay 2 from an Apple TV and an iPhone, with Syng Studio 0.5.2 for reference. Everything below is from the speakers' own journals and their
D-Bus interfaces; no Syng component was modified to observe it.

## 1. airplay2source aborts when a spatial stream starts, then AirPlay is gone for 2+ minutes

**What the user sees:** the group stops responding to AirPlay; a minute or two later it can be selected again.

**Journal (airplay2source), twice so far, both as a Dolby Atmos stream starts:**

```
_GeneralAudioUpdateStreamFormat: Format Changed:kAirPlayAudioFormat_APAC_48KHz_7_1_4(68)
AirPlay2Source.cpp:1629: initializeFormat: Switching to number of channels: 12
AirPlay2Source.cpp:1673: New channel layout: L R C Lfe Lss Rss Tfl Tfr Trl Trr Lrs Rrs
...
terminate called after throwing an instance of 'sdbus::Error'
  what():  [System.Error.EBUSY] Failed to exit a variant (Device or resource busy)
```

The other occurrence ends `[System.Error.ENXIO] Failed to enter a variant (No such device or address)`. systemd then
logs `Main process exited, code=dumped, status=6/ABRT` **2 min 14 s later**: `/usr/bin/syng-coredump` hands a ~150 MB
core to systemd-coredump, which compresses it until its own runtime limit stops it (`systemd-coredump@….service:
Service reached runtime time limit`), leaving half-written `.#core.airplay2source.*` files (~180 MB) on /data. AirPlay
is unavailable the whole time. Not every spatial stream start crashes; it is intermittent.

**What we can say about the cause:** the exception is an `sdbus::Error` that nothing catches, so `std::terminate`
aborts the process. In `AirPlay2Source::initializeFormat` the call to `Provider::setChannelLayout` *is* guarded (its
failure logs "D-Bus error occurred while setChannelLayout: …" and carries on), so the fatal one comes from other D-Bus
work while a stream starts. "Failed to enter a variant" / "Failed to exit a variant" are sdbus-c++'s errors for
*reading* a message (`sd_bus_message_enter_container` / `exit_container`). ENXIO on enter means the next item isn't
the expected variant; EBUSY on exit means the variant held more than was read. Our best reading: airplay2source is
deserializing an incoming message (a signal or a reply it handles in a callback without a try/catch) whose variant
carries a different type than it expects. Two threads reading or building one message at once can also cause it. We
haven't identified the message.

**Suggested fixes:**
- Catch `sdbus::Error` around the D-Bus property updates made while a stream starts or changes format (as is already
  done for `setChannelLayout`), and log instead of terminating.
- Check which incoming signals and replies airplay2source deserializes while a stream starts (or changes format), and
  their variant types. Catch `sdbus::Error` in those handlers. If the stream thread and the event loop both use the
  connection, keep that to one thread.
- Keep a crash from costing minutes: a smaller `ProcessSizeMax`/`ExternalSizeMax` for this service, or `LimitCORE=0`.
  With `LimitCORE=0` systemd-coredump skips the dump and the process is reaped in about 0.3 s, so the service's
  `RestartSec=3` is all the user notices. We run this as a workaround.

## 2. The "Stereo" badge is wrong during Atmos

While a 12-channel APAC 7.1.4 stream plays, the AirPlay source reports:

```
badgingType          Stereo
channelLayout        L R C Lfe Lss Rss Tfl Tfr Trl Trr Lrs Rrs
outputChannelLayout  1 2 3 4 5 6 7 8 9 10 11 12
```

`AirPlay2Source.cpp:1469: Badging Type: Stereo` is logged at every stream start, Atmos or not. Users can't tell
whether Atmos is playing. Deriving the badge from the negotiated format (or the channel layout) would fix it.

## 3. Atmos height only works for speakers marked high, and that mark needs the cloud

How Atmos is rendered (from `cellbroker/shaders.py` and `ProxyManager.py`, which ship as Python source): a 7.1.4
source gets `variable_71_4_shader`, which places all 12 channels as objects around the room (with the puck at
radius 0, the fixed `staging_71_4` layout). The four height channels are always rendered. When every speaker is on
height layer 0, they are extra objects on the floor ring at ±45° and ±135°. When some speaker is on a higher layer,
they move up to 0.6 × that layer, and `set_room_state` turns `enableHeightProcessing` on for every source
(`have_height_cells`). This all looks deliberate and sound.

The catch is how a speaker gets marked high. Neither the Syng Space app nor the speaker sets it; only Syng Studio's
"height layer" switch does. `cell_set_height_layer` in cellbroker then publishes `HEIGHT_LAYER_UPDATE` to the Syng
cloud over MQTT, and the new value only takes effect when the room state comes back down. In practice:

- Ordinary owners can't mark wall- or ceiling-mounted speakers as height speakers, because Studio is a creator tool.
  Their Atmos plays with the height channels at ear height.
- A room that can't reach the cloud can't change the height layer at all. It never hears back.

Suggestion: offer the height layer in the Syng Space app (per speaker, next to its position), and apply it locally
as well as through the cloud.

Separately, `enableBeyondStereoDuplication` (also a writable property of every music source, read by syngsoundserver)
is never set by anything we could find, including cellbroker, Syng Space and Studio. What is it for, and should
something set it?

## 4. A buffered stream goes silent at a track change and stays silent

With Apple Music on an Apple TV or iPhone sending to a Cell group, the next track sometimes never plays. Every Cell is
silent, the source still reports "playing", and HomePods in the same session carry on. The receiver logs one line at
the boundary, then nothing:

```
_bufProcessPacket  err = -6743          (kSizeErr, mid-stream, no flush or teardown around it)
```

A skip produces `Flushing receiver complete: flushSeq 0 …`, then another -6743, and it stays silent. A second skip
produces a flush with a real `flushSeq`, then `ProcessPkt Flush Complete …`, and audio returns. A pause then play
also clears it. On one primary: 33 mid-stream -6743s in 20 hours of listening, most followed within a second by the
sender tearing the stream down (fine), several with no recovery for minutes (some of those may be pauses).

It looks as if `_bufProcessPacket` rejects the first packet of the next track in a continued buffered stream (size
check, perhaps a packet larger than the receiver's buffer slot after a format or bitrate change) and the buffer stops
being fed without the error reaching the session. Suggestion: treat that error as a reason to resynchronise (flush to
the next good packet) rather than stall, and log every rejected packet, not just the first.

## Minor

`Unable to create an APAC decoder. Missing Magic Cookie.` is logged at the start of every Atmos stream. The decoder is
created a moment later once the sender's format config arrives (`APACDecoder_Initialize … numChannels = 12`), so this
first warning is noise, but it reads like a failure.
