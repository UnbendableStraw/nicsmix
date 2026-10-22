# Syng sound-field control — reverse-engineered model

Recovered by decompiling the Syng iOS app (`Syng Space.app`, stripped arm64 Mach-O;
`ProtoTools` and `Continuum` are static Swift modules in the main binary) with Ghidra
plus a string/selector cross-reference extractor, and by frame-analysis of a screen
recording of the real app. This is the model the LAN web UI reproduces.

> Status: the **model, constants, and visuals** below are confirmed. The exact
> `position/width/rotation → object-positions` math that gets sent to the Cell
> (`SetObjectPositions`, see [DBUS-API.md](DBUS-API.md)) is best pinned from a **live
> D-Bus capture** while music plays — tracked in [ROADMAP.md](ROADMAP.md).

# What the app really does (2026-10-20, full decompile of the Swift side)

> This section supersedes the particle/colour/geometry claims further down where they differ. It comes from a
> complete Ghidra decompile of `SyngSpace` (37,800 functions) read alongside the Metal IR in `_re/shaders/`,
> the Swift field/vtable metadata (`tools/swiftmeta.py` in the review bundle), and the app's own `Auras_*.json`.
> The iOS app (`apps/ios/.../Field/`) implements this as of this date; the web field (`field/field.js`) and the
> Android port still implement the older model below.

## The Aerosol (the glitter) — `ProtoTools.Aerosol` + `ParticleGenerator` + `ParticleUniforms`

* **262,144 particles** (`0x40000`), 48 bytes each, drawn as points into a **512 × 512 rgba16Float** texture that
  is **never cleared**. Every frame, in this order: `MPSImageGaussianBlur(sigma: blurSize = 5)` in place on last
  frame's image → a full-size **black rectangle at alpha `blurFade` = 0.7** (normal blend, so ×0.3 persists) →
  the points, blended `src.rgb·src.a + dst` (Aurora `BlendMode` case 2). The glow around the glitter and the
  short trails are this feedback loop; the points themselves are 1–4 px.
* `updateParticle` runs **once per frame** with `deltaTime` in **seconds** (`CACurrentMediaTime` deltas), so
  `life -= dt·0.05` re-seeds a particle every 3–30 *minutes*. The previous port stepped a 30 Hz sim with
  `dt·0.05·60`, which re-seeded every 3–30 seconds — a constant churn of stray sparks the app does not have.
  The kernel is otherwise as documented below, plus `applyRandomAngle()` which (because `rand(float2(-90,90))`
  is a constant) is a fixed sub-degree twist followed by a **soft magnitude cap** (|f| ≥ 1 scaled by
  smoothstep(5/|f| − 4), zero past 1.25).
* `vertexShader` / `pulseForce()` — the *only* music reaction. `positionTransform` is a rotation by `angle`,
  `angleTransform = ((120 / spreadAngle°) / −2π, 0.5)`, `emitterRadius = 0.6 × 0.5 = 0.3` (field units, rim 0.5),
  `distanceScale = (2 / (2e − 1 + weight), 2 / (1 − 2e))` (inside / outside the emitter ring). So the pulse rows
  span **from the inner edge of the torus tube (0.1) through the emitter ring (0.3) to the rim (0.5)**; a
  pulse travels one row per feed packet. `pointSize = 1 + min(4|amt|, 3)` px of the 512 target, brightness
  `0.75 + 2|amt|`, fragment `(1 − r)·brightness`, alpha 1.
* **(prototype only — see the SurroundVisualizer section below)** `AudioAnalysisTools.PulseData` is 128 × 128 r32Float: one row per
  feed packet, newest row at index 0 (rows shift down), each row = the **per-channel loudness** of the stems
  (`stemsRms`, (dB + 50)/50 clamped 0..1) placed as knots around the arc — `L` at the left end, `R` at the right,
  `C` at column 64, the other left/right channels spread between — each knot a quadratic bump that reaches zero
  halfway to its neighbours, feathered to zero at both ends (the "spread" variant). `u = 0.5 − θ·(120/S°)/2π`
  puts `C` at the notch; at the maximum spread (S = 120°) the `R` bump sits 90° to the right. **Frequency plays
  no part.** The agent (b68) now publishes `rms` + `stems` on the viz feed for this; the cell's viz packet already
  carried `stemsRms` (L, R and the upmixer's extracted centre for a stereo source).

## The pulse texture, production path — `SyngSpace.SurroundVisualizer` + `AudioDataHistory` (2026-10-20, second pass)

> Supersedes the `PulseData` paragraph above. `AudioAnalysisTools.PulseData` (128 × 128, raw RMS knots) is the
> *prototype*; the shipping `UpmixTrack.aerosolUpdater` is fed from `SourceInstanceNode.surroundVisualizer.audioDataHistory`,
> which is what the Aerosol actually samples. This is why our particles reacted too little and too slowly.

* **Texture:** 512 × 32, `bgra8Unorm`. Rows are written at **decreasing** indices (`nextRow = (nextRow + 31) % 32`) and
  the Aerosol gets `pulseTextureOffset = (nextRow + 1) / 32` — the newest row; increasing `v` = increasing age; the
  sampler **wraps v** (and clamps u, linear filter). The shader's two taps at `±1/32` are therefore **±1 sample**, and the
  whole `d ∈ [0, 1]` band is 32 samples ≈ 1.07 s of history — not 4.2 s.
* **Tick:** a row is written every `sampleInterval = 1/30 s`, driven by the display loop (`previousSampleTime`), not per
  packet. After each row every emitter brightness is multiplied by **0.9**.
* **Per packet** (`VisualizationListener` → `SurroundVisualizer`): for each channel in the layout,
  `level = ((dB + 100) / 100)³` from `stemsRms` (dB = centi-dB / 100). A two-sample **PeakDetector** (`p0`, `p1`): when
  the new level is below `p1` and `p1 ≥ p0`, `p1` was a local maximum and becomes that channel's **emitter brightness**
  (assignment, not max). So each row carries peak-hold values decaying 10 %/tick; the gradient the shader reads is
  non-zero along the whole decay tail (≈ 0.19 × level — every particle on the tail glows a little) and large at each
  detected peak (a bright shell that travels outward one row per tick).
* **Row = Σ emitters:** CoreGraphics, black fill, then one axial gray gradient per emitter with `plusLighter` blending:
  `brightness · exp(−((u − loc) / spread)² / 0.05)`, `u = (deg + 180) / 360`, `spread = deg / 360`.
* **ChannelLayout tables** (location°, spread°; LFE, heights and wides never drive the pulse):

  | layout | emitters |
  |---|---|
  | Stereo (`"L R"`, the packet still carries L, R and the upmixer's C) | L −120, R +120, C 0 — spread 120 |
  | 5.1 (`"L R C Lfe Ls Rs"`) | L −50, R +50, C 0, Ls −130, Rs +130 — spread 50 |
  | 7.1 / 7.1.2 / 7.1.4 / 5.1.x | L −50, R +50, C 0, side ±105, rear ±150 — spread 50 |

  With `angleTransform.x = (120 / spread°) / −2π` the dot half-angle maps to ±60° of channel space, so for stereo the
  L and R bumps sit exactly on the two dots and C on the notch; a 7.1 mix's sides land just past the dots and the rears
  further round — the "lobes behind the dots".
* **Troubadour's one deliberate difference** (`cfg.particlesInClassic`, default on): the app removes the Aerosol in
  classic mode; we keep it as a full ring and use `angleTransform.x = 1 / −2π` with the u address wrapping, so the ring
  reacts as a 360° speaker map (L/R/C front for stereo; sides and rears around you for 7.1). It blends to the app's arc
  mapping with `expansionProgress` when spatial is switched on.

### What the app does at idle, and Troubadour's additions (2026-10-21)

Checked for the "the glitter still floats when nothing plays" question, against the kernel IR, the `ParticleUniforms`
fill (`uniforms[0]` = absolute `CACurrentMediaTime`, `[1]` = frame delta in seconds), a 1-D simulation of the pull
dynamics, both screen recordings and a live viz capture on the primary:

* **The app's kernel is static once settled.** The pull toward the capped torus is a position clamp, not a soft
  spring: `k = 1.2` outside / `0.5` inside with `x += 2f + v` overshoots across the surface and settles inside within
  ~30 frames (tangential drift from the fixed `applyRandomAngle` twist is ~0.01 units per 10 s). The only idle term,
  `org · sin(life + t/100)`, is dead code — `life` is a half and `life −= dt·0.05` rounds straight back. Nothing in the
  vertex shader is time-driven either. Both clips agree: before audio the field is aura only (and the glitter only
  exists in spatial mode — `IMG_2750.mov` lights up the moment the dots appear, 6.5 → 7.0 s).
* **The production value map is a cube, not (dB+50)/50.** `VizListener` converts `stemsRms` to dB (÷100) and
  `SurroundVisualizer` maps each stem to `((dB/100) + 1)³` (−25 dB → 0.42, −65 dB → 0.044); a two-sample peak
  detector per stem sets that stem's emitter to the last local maximum, rows are pushed every 1/30 s from the draw
  loop (`previousSampleTime` gate), emitters decay ×0.9 per row. The row is a CoreGraphics 512×1 RGBA8 strip, black,
  then per emitter an axial shading `value · exp(−20·((u − c)/w)²)` in plus-lighter blend (`c = (deg+180)/360`,
  `w = width°/360`). The cell's meter floor at silence is −64.6 dB (±0.1 dB jitter, 1.1 dB/packet release ramp), so
  the "silent" pulse texture carries ~0.04 micro-ripples — under a pixel of displacement. The iOS port already matched
  all of this; the remaining difference is perceptual (ripples ≈ 3 px + brightness, over a full-bright classic aura).
* **Troubadour's additions** (`Cfg.idleDrift`, `Cfg.puffMix`, `Cfg.classicSpreadScale`, `Cfg.revealClassic`; the
  first two are toggles in the field's look menu, each 0 = exactly the app):
  * idle drift — a slow coherent flow (`torusFlow()`, was `idleFlow()` in the kernel) the torus pull keeps on the surface, ≈12 px/s;
    **anti-clumping (2026-10-21):** the first version was an in-plane flow kicked into `v`; projected onto the tube by
    the pull it had sinks along the band's silhouettes, and the kept velocity added a centrifugal push, so over minutes
    the glitter piled up on the outer rim (the screen's edge in Fill the screen) — ~4–5× the band's density in a CPU
    model of the kernel. Now the flow is built on the torus (along-ring speed a function of θ only, around-tube speed a
    function of φ only ÷ ρ: zero surface divergence; on a spatial arc a stream function that vanishes at the arc's ends)
    and moves the position directly. The particles also rest at a per-particle depth in the tube (`Cfg.tubeSpread`,
    1 − 0.7·u^2.5 of rb, `extra2.w`) because a uniformly filled *shell* seen edge-on still has a 2.5× caustic at both
    rims. Modelled result: the band's density flat to ±10 % across its width, cell-count spread at the Poisson floor,
    no pile at arc ends. Fill mode folds anything pushed past the rim back in instead of squashing it into a tanh wall.
    The music path (pulse displacement, puff, beat kick) is untouched;
  * puff — the pulse gradient the app displaces by is also integrated into a per-particle radial offset (`vel.w`,
    gain 0.06, ×0.92/frame, ±0.08 clamp) added in the vertex shader, so a beat physically swells a band and eases
    back (a velocity kick would not work: the pull cancels it in two frames);
  * classic ring map — the stems' Gaussians are widened ×1.5 and wrap around the ring, so the three stereo bumps
    (±120°, 0°) meet instead of leaving dead sectors at ±60° and 180°; the glitter gets ×1.6 gain against the
    full-brightness classic aura.

## Geometry — `SyngSpace.UpmixTrack` (the production class; `ProtoTools.UpmixedObject` is its prototype)

| quantity | the app |
|---|---|
| field (`radius`, outerRadius) | `roomRadius − |puck − centre|`; while the puck is **snapped to a speaker** it eases to `min(that, 60 pt)` |
| puck orb (`handleRadius`) | 35 pt, ×1.2 while lifted |
| focus dots and the notch | at **`(puckRadius + fieldRadius) / 2`** from the puck |
| `spread` | the dot half-angle, default **π/4 (45°)**, clamped **25°…120°** (0.436…2.094 rad) |
| `spreadPercentage` (the arc the particles fill, ×π) | **`p(x) = x·(24x² − 10x + 11)/10`, x = spread/π** — 25° → 0.14, 45° → 0.25, 90° → 0.60, 120° → 1.0 (full ring). This is the "lobes ~25° behind the dots" open point: dots at ±95° give an arc of ±118° |
| caps (`startCap`/`endCap`) | on the dot radius, at angles `direction ± spreadPercentage·π` (the arc's ends), passed to the kernel — not derived in the shader |
| `weight` | **`1 − innerRadius/outerRadius`**, inner = `puckRadius + 2 pt`, outer = fieldRadius → ≈ 0.8 with the puck centred, less as the field shrinks (`ra = 0.5 − w/4`, `rb = w/4`) |
| `spreadAngle` (for the pulse mapping) | the dot half-angle in radians |
| classic (sound all around) | **no Aerosol at all** (`isAerosolEnabled` follows `expansionProgress`); the room shows the aura alone. "Classic = weight 2 sphere" was an invention |
| direction | screen angle, −π/2 = up; default −π/2 |

## Colour — `AuraGenerator` / `FlurryGenerator` / `ArtworkProcessor` / `AuraColorAdjustment`

* The aura is **52 soft additive colour discs** (`FlurrySettings`: birthRate 0.12 per frame, lifetime 10 s ± 50 %,
  radius 0.36–0.45 of the square, feather 0.89–1, velocity 0.5 × 0.001 per frame, a weak pull to the centre
  `(pos − 0.5)·−1e−5` and pairwise repulsion `−1e−4·rᵢ²·rⱼ²/d²`). Each disc: radius `r·(env + 0.16)`, opacity
  `(k·env)²`, `env = (1 − cos 2πt)/2` over its life, `k = min(1, 30/Σenv)`. Half of them are seeded 2 s into
  their lives at start.
* A disc's colour is **sampled from a 16³ RGB histogram** ("colorGenerator": a bin with probability ∝ its
  count, a random colour inside the bin). Per source that histogram is `Auras_<source>.json` (decoded:
  `_re/auras/colorgens.json`, embedded as `ColourGeneratorData.swift`). **With album artwork, `ArtworkProcessor`
  builds the same histogram from the cover (`CalculateColorBins`) — that is the "artwork colour flow".** The
  puck (`SoundObject.artwork`) shows the cover whenever the source has one.
* `AuraColorAdjustment`: `c /= |max|` when |max| > 1, `c = mix(c, normalize(c), normalization = 0.3)`, then the
  standard saturation matrix with `saturation = 2`. The aura is *not* rendered grey and recoloured.
* Composition: the room background is the (blurred) aura, dim; the Aerosol image is drawn over it as a
  luminance that **reveals the aura's colour** — which is why the glitter turns pink/green over an album's
  colours (`IMG_2750.mov`) and cyan over AirPlay's.

## Verified on the cells (2026-10-20, live captures on the primary 2AT6, `_local/captures/vizcap-*.bin`)

* The viz packet is `{sourceId, stems: [[88 centi-dB bins]], stemsRms: [...], stemsPeak: [...]}` at **30.4 packets/s**,
  for stereo and Atmos alike; the 88-bin spectrum is always a single (downmixed) row.
* **Atmos (Apple Music, APAC 7.1.4):** `channelLayout = "L R C Lfe Lss Rss Tfl Tfr Trl Trr Lrs Rrs"` and `stemsRms`
  has 12 entries **in exactly that order** (C the loudest, LFE 4th). So the per-channel knots come straight from the
  layout string — which is what agent b68's `stem_names()` does. `Lfe` must *not* be laid out as a left knot
  (it starts with "L"); the iOS row builder skips it.
* **Stereo (Apple Music, AAC-LC):** `channelLayout = "L R"` but `stemsRms` has **3** entries: two near-identical
  (−31.6 / −31.3 dB mean, corr 1.00) and a third ~4 dB louder (−27.6 dB) — the upmixer's extracted centre. The
  order is `L, R, C` (the layout's channels first, then the derived centre), as assumed; the row builder's
  literal "L"/"R"/"C" compares are right.
* AirPlay album playback never flushes between tracks (one flush per source start / album switch, then a single
  ~8 ms "Concealing Missing Samples" and nothing else for the whole album), so a gapless *album* is already a
  continuous timeline on the cell; the flush path only matters for seeks and source changes.

## Still unverified

* Which of PulseData's two textures (`clampedTexture` / `spreadTexture`) the Aerosol samples (the spread one
  is assumed: it is the one with feathered ends).
* The `pulseTextureOffset` the app feeds from the image (treated as a sub-packet phase here).
* The exact opacity slot of the flurry circle (`(k·env)²` vs `k·env`).

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

### Latency (b57)
A handle push is `source_set_stem_positions` → the source's shader → `set_object_positions` on the runtime,
synchronously, in 30–60 ms; cellbroker's 300 ms debounce only gates the cloud write (`skip_persist` skips it).
The agent must never queue pushes: `/api/soundfield` updates `FIELD` and returns, and a single latest-wins
worker sends whatever `FIELD` is when it is free. Keep D-Bus reads on that path off the shared `DBUS_LOCK`.

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

> Superseded in part by "What the app really does" above (deltaTime, the pulse texture, the geometry rules, classic mode, colour).

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

The palette does not have to come from the source. When the speakers' LEDs are lit, the agent puts their
live colour on the viz feed (b52) and the field derives its eight palette colours from it — hue from the
light, the value/saturation structure unchanged — so the room and the screen glow together.

## Addendum (2026-10-20, 12:00) — why the app's particles never look idle, and artwork

* **Motion with no kinematics.** `updateParticle` settles a particle on the torus in ~10 frames and `life` is a half
  (float16) that `−= dt·0.05` can never decrement from 10…100, so particles never re-seed either. All the perpetual
  motion in the Syng app is the *vertex* displacement: with the peak-hold rows the texture gradient `s1 − s2` is non-zero
  almost everywhere (≈ 0.19 × level on a decay tail), `normalize(p)·0.05·amt` moves every particle ~1–2 px of the 512
  target, and that value changes every 1/30 s tick as the rows shift — a radial jitter the never-cleared, blurred,
  0.3-persistence target smears into the short streaks visible in `IMG_2732.mov` (44 % of field pixels change by > 4/255
  frame to frame; 10 % by > 16). The raw-RMS rows of the first port had a flat gradient between onsets, which is why ours
  sat still and only a few reacted.
* **Artwork.** The cell publishes the AirPlay cover as a percent-encoded `data:image/jpeg;base64,…` URI in
  `com.syng.AudioSource.Artwork.artworkUrl`; the agent's `/api/artwork` tried to `urlopen` it and answered 502 forever, so
  the app never had a cover to build the histogram from. Agent b70 decodes data: URIs (http(s) covers are fetched as
  before). `CalculateColorBins` is `round(c·15)` into 16³ atomic counts — exactly `FieldModel.histogram`. The colour mode
  "The album artwork (Syng)" is now the default (cover colours when the source has one, else the source's `Auras`).

## Composition — measured from the app's clips (2026-10-20, final pass)

Luminance (0–255) on the room disc: **classic** (aura alone) ≈ 92; **spatial, glitter everywhere** ≈ 98 with sparks
to 158+; **spatial, a region without particles** ≈ 49–60; **particle region** 102–182 (brighter than the aura itself
near the notch). So the app composes `disc = aura × (dim + aerosol)` with dim ≈ 0.4 in spatial and 1.0 in classic,
the Aerosol term being HDR (dense/reacting particles exceed the aura's own brightness). That is also what the
steady state of 262k points at 0.75 brightness through the σ5 / 0.3-persistence feedback predicts (≈ 0.4–0.6 mean).
The port's scene shader now does exactly that: no floor gradient, no vertical shading, no six-blob "PulseField" halo
around the puck (`cfg.halo = 0`, legacy web look), no glass rim; a faint colour floor only so a black aura does not
vanish; the Aerosol's blur still bleeds a little past the rim (visible in the clips).

Other verified details this pass:
* `Particle.life` is a **half**: `−= dt·0.05` rounds back to the same value for 10…100, so the app's particles never
  re-seed (the port's float32 re-seeded every 3–30 min, a trickle of stray sparks). Now emulated with a half round.
* `cappedTorus`, `sampleNormalAndDistance` (eps 0.001), `applyRandomAngle` and the 1.2/0.5 pull match the IR term for
  term; the only cosmetic difference is `blend = 0` (app) vs `1e-5` (port) below half spread — same result.
* Geometry against `IMG_2732.mov`: dots/notch at 176 px from the puck = (48 + 305)/2; dot half-angle ≈ 97° →
  spreadPercentage 0.68 → caps at ±122° with capR ≈ rb (the rounded lobe ends). Left/right of the channel map checked:
  L lands left of the notch for both the ±60° arc map and the classic 360° map.
* **Per-source `FlurrySettings`** (radius range, feather, normalization, saturation) from `Auras_<source>.json` are now
  applied per source (the port used AirPlay's for all; Spotify's discs are larger with normalization 0.24 and
  saturation 1.75; Pure Data's are small).
* Each flurry disc is an `Aurora.Circle` with `fillOpacity = (k·env)²` **and** `strokeOpacity = k·env,
  strokeWidth = 0.05·r`; the stroke's exact feathered rendering is in the 1700-line Aurora ShapeLayer shader and is
  not ported (left out rather than guessed) — the one remaining known difference in the aura.
