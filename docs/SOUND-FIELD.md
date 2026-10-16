# Syng sound-field control — reverse-engineered model

Recovered by decompiling the Syng iOS app (`Syng Space.app`, stripped arm64 Mach-O;
`ProtoTools` and `Continuum` are static Swift modules in the main binary) with Ghidra
plus a string/selector cross-reference extractor, and by frame-analysis of a screen
recording of the real app. This is the model the LAN web UI reproduces.

> Status: the **model, constants, and visuals** below are confirmed. The exact
> `position/width/rotation → object-positions` math that gets sent to the Cell
> (`SetObjectPositions`, see [DBUS-API.md](DBUS-API.md)) is best pinned from a **live
> D-Bus capture** while music plays — tracked in [ROADMAP.md](ROADMAP.md).

## The control is ONE puck

The app shows a single **listener puck** at the centre of a circular room view — not a
separate "listener" and "source". The underlying object is `SyngSpace.SourceInstanceNode`,
whose published (`Combine`) state is:

| Property | Meaning |
|---|---|
| `normalizedPosition` | puck position in the room (2-D, normalized) |
| `normalizedRadius` | distance of the image from centre |
| `rotationInDegrees` / `snappedRotationInDegrees` | field direction, with a snapped variant |
| `surroundWidthInDegrees` | **spread of the stereo image, as an angle** (not a linear "focus") |

Defaults read from the initializer: **`surroundWidthInDegrees ≈ 25°`**, radius/focus ≈ **0.7**.
Rotation math runs in degrees against a `ReferenceCircleCalculator` (`roomCircle`,
`cellPosition`, `cellRotation`, `referenceCircle`), with snapping around a **0.25** step and
a **0.01** threshold, 180° reference.

## Interaction model (from the app)

1. **Move** — drag the puck to move the listening position.
2. **Enable spatial** — drag the small dot sitting just above the puck away from it; this
   turns on spatial processing and exposes **two focus dots** on two stereo lobes, plus a
   top **notch** marking the field direction.
3. **Refocus & aim** — drag a focus dot: the spread sets `surroundWidthInDegrees`, the
   dots' midpoint sets `rotationInDegrees`. **Rotation only does anything in spatial mode**
   (the dot above the puck is inert in plain stereo).
4. **Collapse** — bring the focus dots back to the puck to turn spatial off.
5. **Volume** — the diagonal hatch bars on the **bottom rim** of the circle are the volume
   slider; they are *not* part of the sound-field controls.

Four faint rings at the room positions are the **speakers** (Cells).

## Motion — the "instant but smooth" feel

The puck animates on SwiftUI/`UISpringTimingParameters`:

```
mass = 1.0,  stiffness = 218.566,  damping = 29.568,  initialVelocity = 0
```

→ damping ratio ζ = 29.568 / (2·√218.566) = **1.00** (critically damped — no overshoot),
natural frequency ω₀ = √218.566 ≈ **14.78 rad/s** (~2.35 Hz), settle ≈ 0.4 s. Equivalent to
SwiftUI `.spring(response ≈ 0.42, dampingFraction ≈ 1.0)`. The grab animation also scales the
puck (`CGAffineTransformScale`) and nudges it a few points. The web field integrates this
spring per frame (`-K·x − C·v`, K=218.566, C=29.568).

## Spatial field visuals

A bright, glittery cyan **two-lobe cloud** emanates from the puck: a bright core plus two
lobes at `direction ± spread`, dense fine sparkle texture, a tight **spectrograph ring**
wrapped around the puck, and a rounded **notch** capsule at the direction. The sparkles are
pushed outward on beats (the spectrograph "blows" the glitter). The renderer in
`ProtoTools` is `ObjectRenderer` → `PositionComputePass` → `DrawRenderPass` with
`ParticleUniforms`; the room/aura background is `AuraGenerator`/`FlurryGenerator`/`Aerosol`
particles with `ColorBinCalculator` mapping audio bins → colour. The web UI approximates
this in canvas.

## The real render engine (from the decrypted app's Metal shaders)

Confirmed by decompiling `ProtoTools_ProtoToolsShaders` and `Aurora_AuroraShaders`
(`default.metallib`) from the decrypted IPA. The field is **not** drawn with simple
gradients — it is a GPU particle system plus SDF vector shapes:

- **ProtoTools particle system** — `struct Particle { float3 position, velocity;
  half3 origin; half life; bool born }`, advanced each frame by `updateParticle`.
  Particles are emitted inside a **`cappedTorus`** SDF (the arc between
  `leftHandle` / `centerHandle` / `rightHandle` = the two focus handles + the puck),
  with uniforms `radius, weight, spread, spreadAngle, angle, emitterRadius,
  distanceScale`. They are pushed outward by **`pulseForce()`** sampled from a
  `pulseTexture` (the audio envelope) — this is the "spectrograph blows the glitter".
  Rendered as **additive soft points** (`point_size` + round `point_coord` falloff).
- **Colour** comes from `CalculateColorBins` (audio → colour histogram) fed into
  `AuraColorAdjustment` / `RoomBackgroundAdjustment`, which apply a **`float3x3
  colorMatrix` + normalization** to recolour the aura from the audio.
- **Aurora shape layer** — SDF primitives `Orb` (the puck), `Capsule` (the notch),
  `Torus` / `Arc` (rings/ripples), `PulseField`, with soft `alphaFromDistance`
  edges, `softness`, `capStyle`, and inner/outer blend modes.
- **`SmoothSpring`** (AudioAnalysisTools) confirms the critically-damped spring.

**Our reproduction** (`syng-agent`, build 18) mirrors this in **WebGL**: an SDF aura
fragment shader (dark navy-teal disc + cyan arc glow + white puck-core bloom) plus a
**9,000-particle additive GPU system** emitted in the arc and pushed by the live
audio pulse, with the crisp shapes (puck/notch/handles/rings/hatch) drawn on a 2D
overlay canvas. Falls back to the 2D canvas renderer where WebGL is unavailable.
The page runs in the viewer's browser, so the i.MX8M only serves it.

## Real colour palette (sampled from the running app)

| Region | Colour |
|---|---|
| Puck glow / notch | `#8ad5f3` |
| Inner disc | `#70cfe7` |
| Mid disc | `#3498be` |
| Outer / edge | `#3587a4` → `#4390ab` |
| Resting disc (dark) | `#1a4f66` → `#071c27` |
| Sparkles | `#ffffff` |
| Background | `#000000` |

Font is **Graphik** (shipped in `syng-ui_SyngUI.bundle`, commercial — not used; we
stay on the system rounded font). The app's `.m4v` clips at the bundle root are 3D
product renders of the Cell; the `NewFeatures20/*.m4v` are grayscale **gesture-mask**
tutorials (`classic`, `focus`, `focus-move`, `focus-spin`, `layer-pin`) composited
over a gradient — they confirmed the puck + two-handle + notch geometry exactly.

## Key decompiled symbols (for future work)

- `SourceInstanceNode` initializer — the state model + defaults (0.7, 25.0).
- `ReferenceCircleCalculator` — cell/field geometry on the room circle (180° default).
- `ProtoTools.RadialSlider` / `RadialSliderAnimationHelper` — the radial control.
- `ProtoTools.UpmixHandleObject` / `SurroundVisualizer` — the handle → width + the visual.
- `SyngSpace.DragGestureCoordinator` / `RotationDragGestureHandler` /
  `RoomPanningDragGestureHandler` — the gesture routing.
- `Continuum.*` (Spring, Velocity, SpringEvaluator, VelocityFilter2, MotivationChanges) —
  the bespoke motion engine used elsewhere in the app.

Extraction tooling (run on a Mac with Ghidra): `~/Downloads/syng_ghidra.sh` +
`DumpDecomp3.java` (decompile-all, keep functions referencing the sound-field selectors);
the string/constant values above were read straight from the binary's `__const`.

## Build 19 — the field reproduced from the decrypted app (2026-10-15/16)

Everything below was read out of `Syng Space 2.1.0` (decrypted IPA), not guessed. Tools are in
`tools/` (`split_metallib.py` + `llvm-dis` for the shaders, `carx.py` + lzfse for `Assets.car`),
the disassembled shaders are in `_re/shaders/*.ll`, the renderer is `field/field.js`
(standalone preview: `field/demo.html`, synthetic music, "Tweaks" sliders).

### The particle system (ProtoTools `updateParticle` / `vertexShader` / `fragmentShader`)

```
struct Particle { float3 position, velocity; half3 origin; half life; bool born }
spawn:    position = origin = rand3(id*0.125) in [-0.5,0.5]^3, velocity 0, life = rand(10..100)
per frame: life -= dt*0.05
  if life > 0:  p   = position + velocity
                (n, d) = normal/distance of the field SDF at p   (central differences, eps 0.001)
                f   = (d > 0 ? 1.2 : 0.5) * d * (-n)          // pulled onto the SDF surface
                velocity += f ;  position = position + f + velocity
  else:         position = origin * sin(life + time*0.01) ; life = 100     // re-seed
  if |position| > 1: normalize ;  velocity *= 0.99
```
The SDF (`cappedTorus`, z included — particles sit on the *surface* of a 3-D tube):
```
spread = clamp(U.spread, 0.05, 1) ;  arc half-angle = spread*π  (sc = sin/cos)
dir    = (sin(U.angle+π), cos(U.angle+π)) ;  w = U.weight*0.25 ;  ra = 0.5 - w ;  rb = w
p.x = |dot(p, dir)| ;  p.y = dot(p, perp(dir))
k  = (sc.y*p.x > sc.x*p.y) ? dot(p.xy, sc) : |p.xy|            // IQ capped torus
d1 = sqrt(|p|² + ra² - 2·ra·k) - rb                           // the arc tube
d2 = sqrt(|p|² + ra² - 2·ra·|p.xy|) - rb                      // the full ring
capR = rb * smoothstep((spread-0.5)*5) ; blend = spread>0.5 ? 0.1 : 0
caps = smooth-min( |p-startCap| - capR , |p-endCap| - capR , blend )
d  = max( min(caps, d1), d2 )
```
Field space: puck at the origin, rim at radius **0.5**; the field radius on screen is
**R_room − |puck offset|** (the lit disc shrinks as you drag the puck, exactly as in the
`NewFeatures20/*.m4v` masks). Weight **2.0** = a sphere = *classic / sound all around*;
spatial uses weight **0.8** → ra = 0.3, rb = 0.2 (fitted to the tutorial masks within ±2°:
the lit extent at r = 0.3/0.5/0.7/0.9 R is dot-angle + 42/38/31/22°). The dot handles sit on
the torus centre line (0.6 R), the notch too; the arc half-angle = the dot angle.

> Open point: the user's app screenshot (dots at ±95°) shows lobes centred ~25° further
> back than this rule predicts — probably caught mid-animation (the visual springs after
> the handles). Tweak sliders `Field weight` / `Handle radius` and `cfg.arcFrac` exist to
> match by eye; a screen recording of the app would settle it.

Render (vertex shader): the particle is displaced *radially* by the audio **pulse texture**
(u = angle around the puck via `angleTransform`, v = `distanceScale·|dist − emitterRadius|`,
rows scroll with time): `amt = sqrt(1−v)·(tex(v+1/32) − tex(v−1/32))`, `disp = normalize(p)·0.05·amt`,
`pointSize = 1 + min(4|amt|, 3)`, brightness `0.75 + 2|amt|`, round soft points, additive.
The texture is the spectrogram, so each frequency pushes the glitter at "its" angle — the
web build mirrors 24 bins around the notch (bass at the notch, treble behind) and scrolls at
`pulseRowsPerSec` (56).

### Colour (`AuraColorAdjustment`, `CalculateColorBins`, `Auras_*.json`)

The aura is rendered grey, then recoloured by a `float3x3 colorMatrix` + `normalization`
(mix of the colour with its normalised direction). `CalculateColorBins` is a 16×16×16 RGB
histogram kernel; each source ships an `Auras_<source>` JSON with `colorGenerator` = that
histogram as a 64×64 16-bit PNG (`_re/auras/`). Decoded AirPlay palette: `#00bbdd`,
`#0099cc`, `#eeffff`, `#ddeeff` (mean `#56c2dd`) → the cyan/white signature. Spotify is
dark green, Tidal dark teal, HDMI navy, PureData orange, USB-C/system audio neutral grey.
`flurrySettings` (the big soft blobs behind the glitter): 52 particles, birthRate 0.12,
lifetime 10 s ±50 %, radius 0.36–0.45, feather 0.89–1.0, saturation 2, velocity 0.5.

### Glass (Aurora `ShapeLayer` "Orb" content)

Puck, handles and notch are *refracting orbs*: the shader samples the scene behind the orb at
`centre + dir · (2·lensTable(r) − 0.5) · radius` (`OrbLensTable`, 1024 half-floats: 0.25 at
the centre rising to 0.74 at r≈0.9 then dropping — mild magnification with a flipped rim),
then applies a gloss map: `c = sat(bg − 1 + g.r); c = sat(1 − (1−c)/g.g); c = sat(c + g.b)`.
Maps extracted and embedded in `field/textures.js`: `OrbB` (puck gloss, 1170²), `OrbC`
(bubble gloss with the bright rim, for the handles/notch), the lens table, `NoiseRGB` grain,
and the real AirPlay glyph (`SourceGlyphs_Airplay.pdf`). `OrbE/OrbG` are the soft shadow
discs under the puck (the dark ring you see around it).

### Gestures (from the mask videos)

* lift the dot above the puck past ~0.26 R_field → spatial on, the dot becomes the notch
  (direction); the two focus dots appear at ±95° (what the app showed).
* drag a dot → spread changes **symmetrically** about the notch (the other dot mirrors);
  min 12°, max 178°; drag it onto the puck (< 0.2 R_field) → spatial off (weight → 2).
* drag the notch → rotates the field. Grabbed handles get a soft white halo (3.4×).
* puck drag: grab-scale 1.1, critically-damped spring (K 218.566, C 29.568) on everything.
* volume: the rim ticks below the room, ±52°, quiet on the left → loud clockwise.

### API (b19)

`POST /api/soundfield {aim°, distance m, spread°, rot°, spatial}` — puck = sound image at
(aim, distance) from the listener; spatial on = the 5-object fan of radius `SOUND_FAN_R`
(env, 1.0 m) around the image pointing along `rot`; spatial off or spread ≥ 300 = the captured
10-object surround ring translated to the image. `/api/state` → `room.field` now carries
`rot` and `spatial`. Default distance is 0 (puck rests on the listener, like the app).
