//  The sound field, natively — now built the way the Syng app builds it.
//
//  Everything below was read out of the decrypted Syng Space 2.1.0 app (ProtoTools / Aurora / AudioAnalysisTools
//  Metal shaders + the Swift that drives them; docs/SOUND-FIELD.md "What the app really does"). The field is
//  five GPU stages:
//
//    1. flurry     — the aura: ~52 soft additive colour blobs (AuraGenerator.FlurryGenerator) drawn into a small
//                    square texture, then normalised + saturated (AuraColorAdjustment: colorMatrix + normalization)
//    2. particles  — the Aerosol: 262,144 particles pulled onto a capped-torus surface (updateParticle, exact), drawn
//                    as additive points displaced by the pulse texture (vertexShader/fragmentShader, exact) into a
//                    512×512 half-float target that is NEVER cleared: each frame it is Gaussian-blurred (σ 5) and
//                    faded (a black rect at blurFade alpha) before the new points land — the glow and the short
//                    trails come from that feedback, not from big point sprites
//    3. scene      — the room disc: the aura shows dimly everywhere, the Aerosol reveals it brightly (the particle
//                    image is a luminance mask over the aura, which is why album colours ride the glitter)
//    4. overlay    — glass orbs (puck with artwork or glyph, notch, handles, lift dot) refracting the scene through
//                    the app's lens table, speaker rings, the volume rim
//
//  Field space: the puck is the origin, the field rim is at radius 0.5, +y is UP (the app's normalised handle
//  space: ((p - centre) / outerRadius) * 0.5 with y flipped). Angles are the app's screen angles (radians,
//  y down, -π/2 = up).
//
//  The previous renderer's "puff" simulation (an onset impulse per spectrum band, float timers, home radius)
//  is kept behind S.mode == 0 so the two can be compared; it is not what the app does.

#include <metal_stdlib>
using namespace metal;

// Aurora Orb lens table (refraction offset by radius), from the decrypted app.
constant float LENS[64] = {
    0.251,0.261,0.271,0.281,0.291,0.302,0.312,0.322,0.332,0.342,0.352,0.362,0.372,0.383,0.393,0.403,
    0.413,0.423,0.433,0.443,0.453,0.463,0.472,0.482,0.492,0.502,0.512,0.521,0.531,0.541,0.550,0.560,
    0.569,0.579,0.588,0.597,0.606,0.616,0.625,0.633,0.642,0.651,0.659,0.667,0.676,0.684,0.691,0.699,
    0.706,0.712,0.719,0.725,0.730,0.734,0.738,0.741,0.742,0.741,0.738,0.731,0.719,0.697,0.657,0.570
};

struct FieldUniforms {
    float4 res;      // xy: drawable size (px)   zw: room centre (px)
    float4 geom;     // x: room radius  y: outer (field) radius  z: puck radius  (px)   w: time (s)
    float4 puck;     // xy: puck centre (px)   z: aerosol on (0..1, the app's expansion)   w: classic (1 = no particles)
    float4 axis;     // x: angle (screen rad)  y: spreadAngle (rad, half-angle)  z: spreadPercentage  w: weight
    float4 torus;    // x: ra  y: rb  z: capR  w: blend   (field units, from the kernel's own rules)
    float4 caps;     // xy: start cap   zw: end cap   (field units, y up)
    float4 env;      // x: energy  y: bass  z: spatial 0..1  w: halo gain
    float4 gains;    // x: aura (background) gain  y: grain  z: particle (reveal) gain  w: px per point
    float4 pts;      // x: point size scale (drawable px per 512-target px)  y: brightness gain  z: legacy sim lerp  w: legacy react edge
    float4 pulse;    // x: pulseTextureOffset (newest row / 32)  y: angleTransform.x  z: emitter radius  w: pulse gain
    float4 auraMap;  // xy: aura square origin (px)  z: side (px)  w: 1 = pulse texture wraps in u (classic 360° map)
    float4 cRoom, cRoomEdge, cWhite, cTint;   // room base colours; cTint = the colour the aerosol is tinted with when the aura is flat
    float4 misc;     // x: aura reveal floor  y: aura dim (classic)  z: legacy pulse scale  w: legacy aura mode (1 = previous SDF aura look)
    float4 aeroMap;  // field units -> the Aerosol target's clip space: clip = p * xy + zw   (normal: (2, 2, 0, 0))
    float4 aeroUV;   // where the Aerosol image sits in this pass, in pixels: xy origin, zw size (normal: the square around the puck)
    float4 fill;     // x: fill scale k (1 normally)  y: 1 = fill the screen (aeroMap.xy is then the puck in 0..1 of the screen)
};

struct Particle {
    float4 pos;   // xyz: position (field units)   w: life
    float4 vel;   // xyz: velocity                 w: float timer (legacy)
    float4 org;   // xyz: spawn origin             w: home radius (legacy)
    float4 aux;   // x: seed  y: return timer (legacy)  zw: previous xy
};

struct SimUniforms {
    float4 axis;    // x: angle  y: spreadAngle  z: spreadPercentage  w: weight
    float4 torus;   // x: ra  y: rb  z: capR  w: blend
    float4 caps;    // xy: start cap   zw: end cap
    float4 cfg;     // x: dt (s)  y: time (s)  z: beat kick (legacy)  w: reactive low edge (legacy)
    float4 cfg2;    // x: puffGain  y: puffVmax  z: puffFloat  w: puffReach (legacy)
    float4 mode;    // x: 1 = the app's kernel, 0 = legacy puff physics   y: legacy step dt (s)  zw: unused
    float4 pulse;   // x: pulseTextureOffset  y: angleTransform.x  z: emitterRadius  w: gain   (as FieldUniforms.pulse)
    float4 extra;   // x: idle drift amplitude  y: puff mix  z: 1 = pulse u wraps (classic)  w: screen angle (rad)
    float4 extra2;  // x: fill scale k  y: beat kick (this frame's, already × gain)  z: energy 0..1  w: tube spread 0..1
};

// pulseForce()'s sampling, shared by the vertex shader (the app) and the kernel (Troubadour's puff): the signed
// pulse gradient at a particle, already scaled by sqrt(1 − d) — what the app multiplies into its displacement.
static float pulseAmount(float2 p, float angle, float weight, float4 pulse, bool wrap, float k,
                         texture2d<float> tex, sampler clampSmp, sampler wrapSmp) {
    float cs = cos(angle), sn = sin(angle);
    float2 q = float2(cs * p.x - sn * p.y, sn * p.x + cs * p.y);          // positionTransform: rotate by angle
    float r2 = dot(q, q);
    float theta = 0.0, r = 0.0;
    if (r2 >= 0.000333) { theta = atan2(q.y, q.x); r = sqrt(r2); }
    float e = pulse.z;                                                   // emitterRadius (0.6 * 0.5 = 0.3)
    float scaleIn  = (2.0 * e - 1.0 + weight) > 0.001 ? 2.0 / (2.0 * e - 1.0 + weight) : 1999.9999;
    float scaleOut = (1.0 - 2.0 * e) > 0.001 ? 2.0 / (1.0 - 2.0 * e) : 1999.9999;
    float scale = (e < r) ? scaleOut : scaleIn;
    float u = theta * pulse.y + 0.5;                                     // angleTransform
    float d = scale * abs(r - e);
    // k > 1 (fill the screen): the rows are read k times faster across the field, so the rings stay as far apart
    // on screen as in the normal circle and repeat outward (the v address wraps), while the fade below still
    // runs to the field's rim — the whole screen reacts instead of a magnified copy of the small field.
    float v0 = pulse.x + d * k;
    float s1, s2;
    if (wrap) {
        s1 = tex.sample(wrapSmp, float2(u, v0 + 1.0 / 32.0)).r;
        s2 = tex.sample(wrapSmp, float2(u, v0 - 1.0 / 32.0)).r;
    } else {
        s1 = tex.sample(clampSmp, float2(u, v0 + 1.0 / 32.0)).r;
        s2 = tex.sample(clampSmp, float2(u, v0 - 1.0 / 32.0)).r;
    }
    return sqrt(max(1.0 - d, 0.0)) * (s1 - s2) * pulse.w;
}

// Idle drift on the torus itself. On the full ring: along it by an amount that depends only on where a particle sits
// around its tube (θ), and around the tube by an amount that depends only on where it sits along the ring (φ) ÷ its
// distance from the axis — on a torus that pair has zero surface divergence, so it stirs the glitter without herding
// it anywhere. On an arc (spatial) the along-ring part comes from a stream function that vanishes at the arc's ends,
// so particles circulate (out along one side of the tube, back along the other) instead of piling into the caps.
// The older in-plane flow, once the pull flattened it onto the tube, had sinks along the band's outer and inner
// silhouettes and slowly piled particles up there — the screen's edge when filling it.
static float3 torusFlow(float3 x, float ra, float t, float seed, float axisAngle, float spread) {
    float rxy = length(x.xy);
    float rho = max(rxy, 0.25 * ra);
    float2 c = x.xy / rho;
    float th = atan2(x.z, rxy - ra);
    float ph = atan2(x.y, x.x);
    float st = sin(th), ct = cos(th);
    float3 ePh = float3(-c.y, c.x, 0.0), eTh = float3(-st * c.x, -st * c.y, ct);
    float around = (sin(3.0 * ph + t * 0.19 + 6.2832 * seed) + 0.6 * cos(5.0 * ph - t * 0.13 + 1.3)) * ra / rho;
    float g1 = th + t * 0.23 + 6.2832 * seed, g2 = 2.0 * th - t * 0.17 + 2.1;
    float sp = clamp(spread, 0.05, 1.0);
    if (sp > 0.98) {
        float along = sin(g1) + 0.6 * sin(g2);
        return along * ePh + around * eTh;
    }
    // the arc's own angle β (the capped torus' frame: centred on perp, half-width sp·π, β̂ = −φ̂)
    float a = axisAngle + M_PI_F;
    float2 dir = float2(sin(a), cos(a)), perp = float2(-dir.y, dir.x);
    float eta = clamp(atan2(dot(x.xy, dir), dot(x.xy, perp)) / (sp * M_PI_F), -1.0, 1.0);
    float G = sin(g1) + 0.6 * sin(g2), Gp = cos(g1) + 1.2 * cos(g2);
    float r = length(float2(rxy - ra, x.z));
    float along = cos(1.5707963 * eta) * Gp;
    around += r * sin(1.5707963 * eta) * G / (2.0 * sp * rho);
    return -along * ePh + around * eTh;
}

struct OverlayUniforms {
    float4 res;     // xy: drawable size (px)   zw: room centre (px)
    float4 geom;    // x: room radius  y: puck radius  z: handle radius  w: volume 0..1
    float4 puck;    // xy: puck centre (px)   z: glyph on   w: omni 0..1 (1 = handles hidden)
    float4 hA;      // xy: handle A (px)   z: grabbed   w: unused
    float4 hB;      // xy: handle B (px)   z: grabbed   w: unused
    float4 nt;      // xy: notch (px)   z: direction (rad, screen)   w: grabbed
    float4 ntSize;  // x: half width  y: half length (px)   z: lift dot grabbed  w: show volume
    float4 lift;    // xy: lift dot (px)   z: visible   w: unused
    float4 flags;   // x: ring count  y: listener x  z: listener y  w: has listener
    float4 art;     // x: artwork in the puck (0/1)  y: artwork aspect  zw: unused
};

struct FlurryBlob { float4 geo; float4 col; };   // geo: xy centre (0..1 square), z radius (fraction of side), w alpha; col: rgb + feather

// ---------------------------------------------------------------- the app's capped torus (updateParticle.metal)

// p.x/p.y are the app's normalised handle space (y up). dir = (sin(angle + π), cos(angle + π)).
// sc = (sin, cos) of the arc half-angle (spreadPercentage * π). ra/rb from weight. Caps from the CPU.
static float cappedTorus(float3 p, float2 dir, float2 sc, float ra, float rb, float4 caps, float spreadPct) {
    float2 perp = float2(-dir.y, dir.x);
    float px = abs(dot(p.xy, dir));
    float py = dot(p.xy, perp);
    float2 q = float2(px, py);
    float k  = (sc.y * px > sc.x * py) ? dot(q, sc) : length(q);
    // the app's "full ring" term uses sin/cos of half(3.14) = 3.140625: k2 == |q| for every p that matters
    float k2 = length(q);
    float pp = dot(q, q) + p.z * p.z;
    float d1 = sqrt(max(pp + ra * ra - 2.0 * ra * k, 0.0)) - rb;
    float d2 = sqrt(max(pp + ra * ra - 2.0 * ra * k2, 0.0)) - rb;
    float t    = clamp((spreadPct - 0.5) * 5.0, 0.0, 1.0);
    float capR = rb * t * t * (3.0 - 2.0 * t);
    float dS = length(float3(p.x - caps.x, p.y - caps.y, p.z)) - capR;
    float dE = length(float3(p.x - caps.z, p.y - caps.w, p.z)) - capR;
    float blend = spreadPct > 0.5 ? 0.1 : 1e-5;
    float h  = clamp(0.5 * (dE - dS) / blend + 0.5, 0.0, 1.0);
    float sm = mix(dE, dS, h) - blend * h * (1.0 - h);
    return max(min(sm, d1), d2);
}

// (normal, distance) by central differences, eps 0.001 — sampleNormalAndDistance()
static float4 sampleNormalAndDistance(float3 p, float4 axis, float4 torus, float4 caps) {
    float spread = clamp(axis.z, 0.05, 1.0);
    float2 sc = float2(sin(spread * 3.140625), cos(spread * 3.140625));
    float a = axis.x + M_PI_F;
    float2 dir = float2(sin(a), cos(a));
    float ra = torus.x, rb = torus.y;
    const float e = 0.001;
    float d  = cappedTorus(p, dir, sc, ra, rb, caps, spread);
    float dx = cappedTorus(p + float3(e, 0, 0), dir, sc, ra, rb, caps, spread) - cappedTorus(p - float3(e, 0, 0), dir, sc, ra, rb, caps, spread);
    float dy = cappedTorus(p + float3(0, e, 0), dir, sc, ra, rb, caps, spread) - cappedTorus(p - float3(0, e, 0), dir, sc, ra, rb, caps, spread);
    float dz = cappedTorus(p + float3(0, 0, e), dir, sc, ra, rb, caps, spread) - cappedTorus(p - float3(0, 0, e), dir, sc, ra, rb, caps, spread);
    float3 n = float3(dx, dy, dz);
    float l = length(n);
    n = l > e ? n / l : float3(0.0);
    return float4(n, d);
}

// applyRandomAngle(f, -90, 90): rand() of the constant (-90, 90) pair is a constant, so the "random" rotation is a
// fixed sub-degree twist; what matters is the soft magnitude cap it ends with: |f| >= 1 is scaled by
// smoothstep(5/|f| - 4) and vanishes past 1.25.
static float3 applyRandomAngle(float3 f) {
    const float a = 0.7373 * 0.017453292;   // fract(sin(dot((-90,90),(12.9898,78.233))) * 43758.5453) in degrees
    float c = cos(a), s = sin(a);
    float3 r = float3(c * f.x - s * f.y, s * f.x + c * f.y, f.z);          // z
    r = float3(c * r.x + s * r.z, r.y, -s * r.x + c * r.z);                // y
    r = float3(r.x, c * r.y - s * r.z, s * r.y + c * r.z);                 // x
    float l = length(r);
    if (l < 1.0) return r;
    float t = clamp(5.0 / l - 4.0, 0.0, 1.0);
    return (r / l) * (t * t * (3.0 - 2.0 * t));
}

struct FSOut {
    float4 position [[position]];
    float2 uv;
};

vertex FSOut fullscreenVertex(uint vid [[vertex_id]]) {
    float2 p = float2((vid == 2) ? 3.0 : -1.0, (vid == 1) ? 3.0 : -1.0);
    FSOut o;
    o.position = float4(p, 0.0, 1.0);
    o.uv = float2(p.x * 0.5 + 0.5, 0.5 - p.y * 0.5);
    return o;
}

// ---------------------------------------------------------------- 1. the aura (FlurryGenerator + AuraColorAdjustment)

// Each flurry particle is an Aurora Circle: additive, radius = side * r * (envelope + 0.16), opacity (k * envelope)^2,
// feather 0.89..1 (almost a pure radial fade). The colour adjustment that follows: divide by |max| when it exceeds 1,
// mix with the normalised direction (normalization 0.3), then the saturation matrix (saturation 2).
fragment float4 flurryFragment(FSOut in [[stage_in]],
                               constant FlurryBlob *blobs [[buffer(0)]],
                               constant float4 &cfg       [[buffer(1)]])   // x: count  y: normalization  z: saturation  w: max magnitude
{
    float2 uv = in.uv;
    float3 c = float3(0.0);
    int n = int(cfg.x);
    for (int i = 0; i < n && i < 64; i++) {
        FlurryBlob b = blobs[i];
        float d = length(uv - b.geo.xy);
        float r = max(b.geo.z, 1e-4);
        float feather = clamp(b.col.w, 0.0, 1.0);
        float cov = 1.0 - smoothstep(r * (1.0 - feather), r, d);
        c += b.col.rgb * (b.geo.w * cov);
    }
    float mag = cfg.w;
    if (mag > 1.0) c /= mag;
    float len = length(c);
    float3 nrm = len > 1e-5 ? c / len : c;
    c = mix(c, nrm, cfg.y);
    float s = cfg.z;
    float3x3 sat = float3x3(float3(0.213 + 0.787 * s, 0.213 - 0.213 * s, 0.213 - 0.213 * s),
                            float3(0.715 - 0.715 * s, 0.715 + 0.285 * s, 0.715 - 0.715 * s),
                            float3(0.072 - 0.072 * s, 0.072 - 0.072 * s, 0.072 + 0.928 * s));
    c = saturate(sat * c);
    return float4(c, 1.0);
}

// ---------------------------------------------------------------- 2. the Aerosol

kernel void simulateParticles(device Particle *parts   [[buffer(0)]],
                              constant SimUniforms &S  [[buffer(1)]],
                              constant float *flux     [[buffer(2)]],   // 24 bands (legacy physics only)
                              constant uint &count     [[buffer(3)]],
                              texture2d<float> pulse   [[texture(0)]],
                              sampler pulseSmp         [[sampler(0)]],
                              sampler pulseWrap        [[sampler(1)]],
                              uint id [[thread_position_in_grid]]) {
    if (id >= count) return;
    Particle p = parts[id];
    float3 x = p.pos.xyz, v = p.vel.xyz;
    p.aux.zw = x.xy;

    if (S.mode.x > 0.5) {
        // ---- the app's updateParticle, line for line. deltaTime is in seconds (CACurrentMediaTime deltas), so
        // life -= dt * 0.05 means a particle re-seeds every few minutes, not every few seconds. ----
        // the app keeps `life` as a half: 10…100 minus dt·0.05 rounds straight back, so particles never re-seed
        float life = float(half(float(half(p.pos.w)) - S.cfg.x * 0.05));
        // ---- Troubadour: two kicks the app does not have, both scaled by the real dt (the app's kernel is per frame) ----
        float fr = S.cfg.x * 60.0;                        // frames' worth of this step
        float k = max(S.extra2.x, 1.0);                   // fill scale: on-screen sizes stay those of the normal circle
        if (S.extra.x > 0.0) {
            // idle drift: ~1/4000 of the field radius per frame at 1 (the speed the old velocity kick settled at);
            // the music quickens it (×1…3 with the energy). It moves the particle directly rather than through `v`:
            // velocity kept in a straight line while the ring curves away is a centrifugal push, which herded the
            // drifting particles round their tube to its outer rim.
            x += torusFlow(x, S.torus.x, S.cfg.y, p.aux.x, S.axis.x, S.axis.z) * (2.5e-4 * S.extra.x * fr * (1.0 + 2.0 * S.extra2.z) / k);
        }
        // puff: the torus pull is stiff enough to cancel any velocity within two frames, so the swell lives in its
        // own per-particle offset (vel.w, unused by the app's kernel): the pulse gradient pumps it, it eases back at
        // ×0.92 per frame, and the vertex shader adds it radially — the previous physics' feel on the app's rings
        float puff = p.vel.w;
        if (S.extra.y > 0.0 || S.extra2.y > 0.0) {
            float amt = pulseAmount(x.xy, S.extra.w, S.axis.w, S.pulse, S.extra.z > 0.5, k, pulse, pulseSmp, pulseWrap);
            puff += amt * 0.09 * S.extra.y * fr / k;
            // the beat kick: every particle, weighted 0.35…1 by its own seed, a quarter of them inward
            float w = 0.35 + 0.65 * fract(p.aux.x * 7.31 + 0.17);
            float dirSign = fract(p.aux.x * 3.71) < 0.25 ? -0.6 : 1.0;
            puff += S.extra2.y * w * dirSign * fr / k;
            puff *= pow(0.92, fr);
            puff = clamp(puff, -0.10 / k, 0.10 / k);
        } else {
            puff = 0.0;
        }
        // where this particle rests in its tube: most near the skin as in the app, a tail deeper in (1 − 0.7·u^2.5 of
        // the tube's radius). A hollow shell seen edge-on piles up along its silhouettes (the band's rim and the ring
        // round the puck); this spread keeps the band's density even across its width (±10 % in a model of it).
        // Outside the tube the pull is the app's; inside, the settle to that depth is the app's inner gain.
        float u = fract(sin(p.aux.x * 91.7 + 3.1) * 43758.5453);
        float depth = S.extra2.w * 0.7 * pow(u, 2.5) * S.torus.y;
        float3 nx;
        if (life > 0.0) {
            float3 pr = x + v;
            float4 nd = sampleNormalAndDistance(pr, S.axis, S.torus, S.caps);
            float3 f = (nd.w > 0.0 ? 1.2 * nd.w : 0.5 * (nd.w + depth)) * (-nd.xyz);
            f = applyRandomAngle(f);
            v = f + v;
            nx = x + f + v;
        } else {
            nx = p.org.xyz * sin(life + S.cfg.y * 0.01);
            life = 100.0;
        }
        float len = length(nx);
        if (len > 1.0) nx /= len;
        p.pos = float4(nx, life);
        p.vel = float4(v * 0.99, puff);
        parts[id] = p;
        return;
    }

    // ---- legacy: the previous renderer's puff physics (kept for comparison) ----
    float dt = S.mode.y;
    float life = p.pos.w - dt * 0.05 * 60.0;
    float flt = p.vel.w, home = p.org.w, seed = p.aux.x, rt = p.aux.y;
    float a = S.axis.x + M_PI_F;
    float2 dir = float2(sin(a), cos(a));
    float2 n = float2(-dir.y, dir.x);
    float sdr = fract(seed * 7.13 + 0.37);
    float tt = clamp((sdr - S.cfg.w) / 0.15, 0.0, 1.0);
    float react = 0.12 + 0.88 * tt * tt * (3.0 - 2.0 * tt);
    float dist = length(x.xy);
    if (dist > 0.02) {
        float ang = atan2(-x.x * n.y + x.y * n.x, x.x * n.x + x.y * n.y);
        float ub = clamp((0.5 + 0.5 * ang / M_PI_F) * 24.0 - 0.5, 0.0, 23.999);
        int b0 = int(ub);
        int b1 = min(23, b0 + 1);
        float tb = ub - float(b0);
        float fl = 0.75 * (flux[b0] * (1.0 - tb) + flux[b1] * tb)
                 + 0.25 * (flux[max(0, b0 - 1)] * (1.0 - tb) + flux[min(23, b1 + 1)] * tb);
        float home0 = home > 0.02 ? home : dist;
        float att = clamp(1.0 - (dist - home0) / max(0.05, S.cfg2.w), 0.0, 1.0);
        float imp = react * (S.cfg2.x * fl + S.cfg.z) * att;
        float vr = dot(v.xy, x.xy) / dist;
        if (imp > 0.0002 && vr < S.cfg2.y && dist < 0.6) {
            float add = min(imp, S.cfg2.y - vr);
            v.xy += x.xy / dist * add;
            if (flt <= 0.0 && rt <= 0.0) {
                home = home > 0.02 ? home + clamp(dist - home, -0.02, 0.02) : dist;
            }
            flt = max(flt, S.cfg2.z * react);
            rt = S.cfg2.z * 1.5;
        }
    }
    float3 nx;
    if (life > 0.0) {
        float3 pr = x + v;
        float4 nd = sampleNormalAndDistance(pr, S.axis, S.torus, S.caps);
        float ks = flt > 0.5 ? 0.08 : (flt > 0.0 ? 0.08 + 0.92 * (1.0 - flt / 0.5) : 1.0);
        float3 f = -((nd.w > 0.0 ? 1.2 : 0.5) * nd.w * ks) * nd.xyz;
        v += f;
        nx = x + f + v;
    } else {
        nx = p.org.xyz * sin(life + S.cfg.y * 0.01);
        life = 100.0;
    }
    float len = length(nx);
    if (len > 1.0) nx /= len;
    if (rt > 0.0 && home > 0.02) {
        float d2 = length(nx.xy);
        if (d2 > 0.02) {
            float g = (home - d2) * (flt > 0.0 ? 0.012 : 0.04);
            v.xy += nx.xy / d2 * g;
        }
        if (flt <= 0.0) rt -= dt;
    }
    float damp = flt > 0.0 ? 0.94 : 0.99;
    if (flt > 0.0 && dot(nx.xy, nx.xy) > 0.42) damp = 0.85;
    p.pos = float4(nx, life);
    p.vel = float4(v * damp, flt > 0.0 ? flt - dt : 0.0);
    p.org.w = home;
    p.aux.y = rt;
    parts[id] = p;
}

// Fong's elliptical-grid mapping, unit disc -> square: the disc's rim lands exactly on the square's edge and the
// interior stays smooth (no corners pinched to a point), so a circular field can fill a rectangle edge to edge.
static float2 discToSquare(float2 q) {
    float u = q.x, v = q.y, u2 = u * u, v2 = v * v, tw = 2.8284271;
    float x = 0.5 * sqrt(max(2.0 + u2 - v2 + tw * u, 0.0)) - 0.5 * sqrt(max(2.0 + u2 - v2 - tw * u, 0.0));
    float y = 0.5 * sqrt(max(2.0 - u2 + v2 + tw * v, 0.0)) - 0.5 * sqrt(max(2.0 - u2 + v2 - tw * v, 0.0));
    return clamp(float2(x, y), -1.0, 1.0);
}

struct PointOut {
    float4 position  [[position]];
    float  pointSize [[point_size]];
    float  bright;
};

// vertexShader + pulseForce(), exact. The pulse texture is SyngSpace.AudioDataHistory: 512 columns (the 360° channel
// map, u = (deg + 180)/360, one Gaussian per layout emitter) × 32 rows (one per 1/30 s tick; (nextRow+1)/32 is the
// newest, older rows follow it, the v address wraps). u = θ·angleTransform.x + 0.5 where θ is the particle's angle in
// the field rotated by `angle`; v = pulseTextureOffset + distanceScale·|r − emitter|; the two taps are ±1 row.
vertex PointOut particleVertex(uint vid [[vertex_id]],
                               device const Particle *parts [[buffer(0)]],
                               constant FieldUniforms &U    [[buffer(1)]],
                               texture2d<float> pulse       [[texture(0)]],
                               sampler pulseSmp             [[sampler(0)]],
                               sampler pulseWrap            [[sampler(1)]]) {
    Particle pt = parts[vid];
    float2 p = (U.pts.z > 0.0) ? mix(pt.aux.zw, pt.pos.xy, U.pts.z) : pt.pos.xy;   // interpolation only for the legacy 30 Hz sim
    if (U.misc.w < 0.5) {                                                  // app physics: Troubadour's puff offset (field units)
        float r0 = length(p);
        if (r0 > 0.02) p += p / r0 * pt.vel.w;
    }

    // pulseForce(): the angle map is (120/spread°)/−2π (the app) or 1/−2π in classic (360° map, u wraps)
    float k = max(U.fill.x, 1.0);
    float amt = pulseAmount(p, U.axis.x, U.axis.w, U.pulse, U.auraMap.w > 0.5, k, pulse, pulseSmp, pulseWrap);
    float r = length(p);
    float2 disp = (r > 0.0 ? p / r : float2(0.0)) * 0.05 * amt;           // normalize(pos.xy) * 0.05 * amt (clip units of the normal square)
    // normal: clip = 2p + disp (field 0.5 -> 1). Filling the screen the field maps onto a screen-shaped target and the
    // displacement keeps its normal on-screen size (÷ k).
    float2 cp;
    if (U.fill.y > 0.5) {
        // Fill the screen: the field's circle is mapped onto the screen's rectangle (its rim = the screen's edges),
        // so nothing ever drifts off one side and back. The puck is the field's centre: each quadrant of the disc
        // stretches to the screen edge on its side, wherever the puck is.
        float2 q = (p + disp * (0.5 / k)) * 2.0;                          // unit disc
        float rq = length(q);
        // past the rim (a beat's puff, a ripple) a particle folds back in, like a bounce, instead of being squashed
        // into a thin bright line against the screen's edge
        if (rq > 1.0) q *= max(2.0 - rq, 0.5) / rq;
        float2 sq = discToSquare(q);                                         // -1…1, y up
        float2 pc = U.aeroMap.xy;                                            // the puck, 0…1 of the screen (y down)
        float sx = pc.x + sq.x * (sq.x < 0.0 ? pc.x : 1.0 - pc.x);
        float sy = pc.y - sq.y * (sq.y > 0.0 ? pc.y : 1.0 - pc.y);
        cp = float2(sx * 2.0 - 1.0, 1.0 - sy * 2.0);
    } else {
        cp = (p + disp * (0.5 / k)) * U.aeroMap.xy + U.aeroMap.zw;
    }

    PointOut o;
    o.position = float4(cp.x, cp.y, 0.0, 1.0);
    float a = abs(amt) * 2.0;
    o.pointSize = (1.0 + min(a * 2.0, 3.0)) * U.pts.x;                    // 1 + min(4|amt|, 3) px of the 512 target
    o.bright = (0.75 + a) * U.pts.y;                                      // 0.75 + 2|amt|
    return o;
}

// fragmentShader: (1 - |pc·2-1|) * color, alpha 1; blended src.rgb*src.a + dst (BlendMode case 2)
fragment float4 particleFragment(PointOut in [[stage_in]], float2 pc [[point_coord]]) {
    float2 q = pc * 2.0 - 1.0;
    float a = max(0.0, 1.0 - length(q));
    return float4(float3(a * in.bright), 1.0);
}

// the per-frame fade of the never-cleared Aerosol target: a black rect at blurFade alpha, normal blending
fragment float4 fadeFragment(FSOut in [[stage_in]], constant float4 &fade [[buffer(0)]]) {
    return float4(0.0, 0.0, 0.0, fade.x);
}

// ---------------------------------------------------------------- 3. the scene

fragment float4 sceneFragment(FSOut in [[stage_in]],
                              constant FieldUniforms &U [[buffer(0)]],
                              constant float4 *halo     [[buffer(1)]],   // 6 pulse blobs, then 6 phase sets
                              texture2d<float> aura     [[texture(0)]],
                              texture2d<float> aerosol  [[texture(1)]],
                              texture2d<float> noise    [[texture(2)]],
                              sampler clampSmp          [[sampler(0)]],
                              sampler repeatSmp         [[sampler(1)]]) {
    float2 px = in.uv * U.res.xy;
    float2 C = U.res.zw, P = U.puck.xy;
    float R = max(U.geom.x, 1.0), puckR = max(U.geom.z, 1.0);

    float dC = length(px - C);
    float rr = dC / R;
    float inRoom = 1.0 - smoothstep(R - 1.0, R + 1.0, dC);

    // the aura square covers the room's bounding square (flurry positions live in the unit square)
    float2 auv = (px - U.auraMap.xy) / max(U.auraMap.z, 1.0);
    float3 auraC = aura.sample(clampSmp, clamp(auv, 0.0, 1.0)).rgb;

    // the Aerosol image: normally a square of side 2·outerRadius centred on the puck; filling the screen, the screen
    float2 puv = (px - U.aeroUV.xy) / max(U.aeroUV.zw, float2(1.0));
    float inSq = (puv.x >= 0.0 && puv.x <= 1.0 && puv.y >= 0.0 && puv.y <= 1.0) ? 1.0 : 0.0;
    float aero = aerosol.sample(clampSmp, clamp(puv, 0.0, 1.0)).r * inSq * U.puck.z;

    // The app's composition, measured from its own frames (docs/SOUND-FIELD.md, "composition"): the disc is the
    // blurred aura at full strength in classic, dimmed to ~0.4 in spatial, and the Aerosol image adds aura × glitter on
    // top (an HDR term — dense/reacting particles push past the aura's own brightness). Nothing else: no floor
    // gradient, no halo, no rim. A faint floor keeps a black aura from vanishing entirely.
    float dim = mix(U.misc.x, U.misc.y, U.puck.w);         // spatial 0.42 … classic 1.0
    float3 col = auraC * (dim * U.gains.x) + U.cRoom.rgb * 0.12;
    float3 tint = max(auraC, U.cTint.rgb * 0.2);
    col += (tint + 0.12 * U.cWhite.rgb) * aero * U.gains.z;

    // (legacy) Aurora PulseField blobs around the puck — not in the app; cfg.halo = 0 keeps them off
    float acc = 0.0;
    if (U.env.w > 0.001 && length(px - P) < puckR * 4.0) {
        for (int k = 0; k < 6; k++) {
            float4 pe = halo[k];
            float4 ph = halo[6 + k];
            float2 c = P + pe.xy * puckR;
            float2 dv = px - c;
            float dd = length(dv) / puckR;
            float th = atan2(dv.y, dv.x);
            float rad = pe.z * (1.0 + ph.z * cos(2.0 * th + ph.x) + ph.w * cos(3.0 * th + ph.y));
            float cov = 1.0 - smoothstep(rad - 0.22, rad + 0.22, dd);
            acc = acc + cov * pe.w * (1.0 - acc);
        }
        acc *= U.env.w * mix(0.75, 1.0, U.env.z);
        col = mix(col, mix(U.cWhite.rgb, float3(1.0), 0.5), clamp(acc, 0.0, 0.97));
    }

    // the Aerosol's blur bleeds a little past the rim where the field reaches it (visible in the app's clips)
    float outer = exp(-max(dC - R, 0.0) / (0.05 * R)) * (1.0 - inRoom) * aero * 0.35;
    col += tint * outer;

    // grain (Aurora NoiseRGB)
    float t = U.geom.w;
    float n = noise.sample(repeatSmp, px / 96.0 + float2(t * 0.013, -t * 0.009)).r;
    col *= 1.0 + (n - 0.5) * 0.22 * U.gains.y;

    col = mix(col, tint * outer, 1.0 - inRoom);
    return float4(col, 1.0);
}

fragment float4 blitFragment(FSOut in [[stage_in]],
                             texture2d<float> src [[texture(0)]],
                             sampler smp [[sampler(0)]]) {
    return float4(src.sample(smp, in.uv).rgb, 1.0);
}

// ---------------------------------------------------------------- 4. overlay: orbs, rings, volume

static float lensAt(float r) {
    float x = clamp(r, 0.0, 1.0) * 63.0;
    int i = int(x);
    int j = min(63, i + 1);
    return mix(LENS[i], LENS[j], x - float(i));
}

static float4 glassOrb(float2 px, float2 centre, float radius, float halfLen, float rot,
                       float darken, float tint, float glyphOn, float artOn, float artAspect,
                       texture2d<float> scene, texture2d<float> glyph, texture2d<float> art, sampler smp, float2 res) {
    float2 dv = px - centre;
    float reach = max(radius, halfLen) + 2.0;
    if (dot(dv, dv) > reach * reach) return float4(0.0);       // most of the screen: skip the trig and the samples
    float c = cos(-rot), s = sin(-rot);
    float2 l = float2(dv.x * c - dv.y * s, dv.x * s + dv.y * c) / max(radius, 0.5);
    float along = 0.0;
    if (halfLen > radius) {
        float ext = halfLen / radius - 1.0;
        along = clamp(l.y, -ext, ext);
        l.y -= along;
    }
    float r = length(l);
    float aa = 1.0 - smoothstep(1.0 - 2.0 / max(radius, 2.0), 1.0, r);
    if (aa <= 0.001) return float4(0.0);

    float2 dir = r > 0.0001 ? l / r : float2(0.0);
    float off = lensAt(r) * 2.0 - 0.5;
    float2 local = float2(dir.x * off, along + dir.y * off) * radius;
    float cr = cos(rot), sr = sin(rot);
    float2 sp = centre + float2(local.x * cr - local.y * sr, local.x * sr + local.y * cr);
    float3 bg = scene.sample(smp, sp / res).rgb;
    bg = mix(bg, bg * float3(0.30, 0.62, 1.0) + float3(0.02, 0.16, 0.38), tint);

    if (artOn > 0.5) {
        // the album cover sits in the orb (SoundObject.artwork), slightly magnified, refracted like the scene
        float2 auv = float2(0.5 + 0.5 * l.x * 0.92 * off * 2.0, 0.5 + 0.5 * l.y * 0.92 * off * 2.0);
        if (artAspect > 1.0) auv.x = 0.5 + (auv.x - 0.5) / artAspect; else if (artAspect > 0.0) auv.y = 0.5 + (auv.y - 0.5) * artAspect;
        float3 ac = art.sample(smp, clamp(auv, 0.0, 1.0)).rgb;
        bg = mix(bg, ac, 0.92 * (1.0 - smoothstep(0.86, 1.0, r)));
    }

    float2 h = l - float2(-0.34, -0.42);
    float spec = exp(-dot(h, h) * 5.5);
    float3 col = bg * mix(1.0, 0.72, darken * (1.0 - smoothstep(0.7, 0.95, r)));
    col += float3(0.86, 0.95, 1.0) * spec * (0.26 + 0.20 * darken);
    float rim = smoothstep(0.74, 0.90, r) * (1.0 - smoothstep(0.93, 1.0, r));
    col += float3(0.90, 0.98, 1.0) * rim * (0.35 + 1.05 * darken);
    col *= 1.0 - 0.18 * smoothstep(0.55, 1.0, r);

    if (glyphOn > 0.5 && artOn < 0.5) {
        float2 guv = float2(0.5 + 0.5 * l.x * 1.30, 0.5 + 0.5 * l.y * 1.30);
        float a = glyph.sample(smp, guv).a;
        col = mix(col, float3(1.0), a * 0.95);
    }
    return float4(col, aa);
}

static float ringMask(float2 px, float2 c, float radius, float w) {
    float d = abs(length(px - c) - radius);
    return 1.0 - smoothstep(w * 0.5, w * 0.5 + 1.5, d);
}

fragment float4 overlayFragment(FSOut in [[stage_in]],
                                constant OverlayUniforms &O [[buffer(0)]],
                                constant float4 *rings      [[buffer(1)]],   // xy px, z active, w snapped
                                texture2d<float> scene      [[texture(0)]],
                                texture2d<float> glyph      [[texture(1)]],
                                texture2d<float> art        [[texture(2)]],
                                sampler smp                 [[sampler(0)]]) {
    float2 px = in.uv * O.res.xy;
    float3 col = scene.sample(smp, in.uv).rgb;
    float R = max(O.geom.x, 1.0);
    float scale = max(R / 138.6, 0.5);

    float dsh = length(px - O.puck.xy) / max(O.geom.y * 2.0, 1.0);
    col = mix(col, float3(0.02, 0.08, 0.14), 0.40 * smoothstep(1.0, 0.30, dsh));

    float omni = O.puck.w;
    if (omni < 0.999) {
        float alpha = 1.0 - omni;
        float hr = max(O.geom.z, 1.0);
        float ga = O.hA.z * (1.0 - smoothstep(0.0, 1.0, length(px - O.hA.xy) / (hr * 3.4)));
        float gb = O.hB.z * (1.0 - smoothstep(0.0, 1.0, length(px - O.hB.xy) / (hr * 3.4)));
        float gn = O.nt.w * (1.0 - smoothstep(0.0, 1.0, length(px - O.nt.xy) / (max(O.ntSize.y, 1.0) * 2.7)));
        col = mix(col, float3(1.0), 0.28 * clamp(ga + gb + gn, 0.0, 1.0));
        float4 a = glassOrb(px, O.hA.xy, hr, 0.0, 0.0, 0.35, 0.0, 0.0, 0.0, 1.0, scene, glyph, art, smp, O.res.xy);
        float4 b = glassOrb(px, O.hB.xy, hr, 0.0, 0.0, 0.35, 0.0, 0.0, 0.0, 1.0, scene, glyph, art, smp, O.res.xy);
        float4 nn = glassOrb(px, O.nt.xy, O.ntSize.x, O.ntSize.y, O.nt.z, 0.40, 0.0, 0.0, 0.0, 1.0, scene, glyph, art, smp, O.res.xy);
        col = mix(col, a.rgb, a.a * alpha);
        col = mix(col, b.rgb, b.a * alpha);
        col = mix(col, nn.rgb, nn.a * alpha);
    } else if (O.lift.z > 0.5) {
        float hr = max(O.geom.z * 0.85, 1.0);
        float gl = O.ntSize.z * (1.0 - smoothstep(0.0, 1.0, length(px - O.lift.xy) / (hr * 3.4)));
        col = mix(col, float3(1.0), 0.28 * gl);
        float4 ld = glassOrb(px, O.lift.xy, hr, 0.0, 0.0, 0.50, 0.0, 0.0, 0.0, 1.0, scene, glyph, art, smp, O.res.xy);
        col = mix(col, ld.rgb, ld.a);
    }
    float4 pk = glassOrb(px, O.puck.xy, O.geom.y, 0.0, 0.0, 0.25, 0.85, O.puck.z, O.art.x, O.art.y, scene, glyph, art, smp, O.res.xy);
    col = mix(col, pk.rgb, pk.a);

    int nr = int(O.flags.x);
    float rr = R * 0.085;
    for (int i = 0; i < nr && i < 8; i++) {
        float4 rg = rings[i];
        float snapped = rg.w;
        float rad = rr * mix(1.0, 1.18, snapped);
        float a = ringMask(px, rg.xy, rad, mix(2.0, 2.6, snapped) * scale);
        float alpha = mix(mix(0.16, 0.26, rg.z), 0.75, snapped);
        col = mix(col, float3(0.92, 0.98, 1.0), a * alpha);
        col = mix(col, float3(0.80, 0.88, 0.92), ringMask(px, rg.xy, rr * 0.62, 1.2 * scale) * 0.09);
        col = mix(col, float3(0.78, 0.88, 0.92), (1.0 - smoothstep(rad - 1.0, rad, length(px - rg.xy))) * 0.04);
    }
    if (O.flags.w > 0.5) {
        col = mix(col, float3(0.90, 0.95, 0.97), ringMask(px, O.flags.yz, 4.0 * scale, 1.2 * scale) * 0.45);
    }

    if (O.ntSize.w > 0.5) {
        const float VH = 52.0 * M_PI_F / 180.0;
        float2 dv = px - O.res.zw;
        float dist = length(dv);
        float Rv = R * 1.10, tl = R * 0.065;
        if (dist > Rv - 4.0 * scale && dist < Rv + tl * 1.8) {
            float a = atan2(dv.y, dv.x);
            if (abs(a - M_PI_F * 0.5) < VH + 0.08) {
                float n = 44.0;
                float f = (M_PI_F * 0.5 + VH - a) / (2.0 * VH);
                float ff = round(clamp(f, 0.0, 1.0) * n) / n;
                float ang = M_PI_F * 0.5 + VH - ff * 2.0 * VH;
                float2 u = float2(cos(ang), sin(ang));
                bool on = ff <= O.geom.w + 1e-6;
                bool cur = abs(ff - O.geom.w) <= 0.5 / n;
                float2 p0 = O.res.zw + u * Rv;
                float2 p1 = O.res.zw + u * (Rv + tl * (cur ? 1.6 : 1.0));
                float2 pa = px - p0, ba = p1 - p0;
                float t = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-4), 0.0, 1.0);
                float w = (cur ? 2.2 : 1.6) * scale * 0.5;
                float m = 1.0 - smoothstep(w, w + 1.2, length(pa - ba * t));
                col = mix(col, float3(1.0), m * (on ? (cur ? 1.0 : 0.85) : 0.28));
            }
        }
    }
    return float4(col, 1.0);
}
