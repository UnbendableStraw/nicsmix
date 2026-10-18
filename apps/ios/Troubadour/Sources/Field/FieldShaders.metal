//  The sound field, natively.
//
//  A Metal port of the agent's WebGL field (field/field.js), which is itself a reproduction of the
//  Syng app's own renderer (ProtoTools particle system + Aurora SDF shapes; docs/SOUND-FIELD.md).
//  Four stages, all on the GPU — the web version runs the particle simulation on the CPU at 30 Hz,
//  which is what makes it lag; here the kernel runs on the GPU and the frame only interpolates:
//
//    1. scene      — room disc, capped-torus aura in the source's palette, flurry blobs, pulse halo
//    2. blit       — upscale the (deliberately low-res) scene into the full-size target
//    3. particles  — compute kernel (sim, fixed 30 Hz steps) + additive points displaced by the
//                    pulse texture, drawn over the scene
//    4. overlay    — glass orbs (puck, notch, handles, lift dot) refracting the scene through the
//                    app's lens table, then the speaker rings and the volume rim
//
//  Field space: the puck is the origin, the room rim is at radius 0.5, screen y is down.

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
    float4 geom;     // x: room radius  y: field radius  z: puck radius  (px)   w: time (s)
    float4 puck;     // xy: puck centre (px)   zw: unused
    float4 axis;     // xy: arc axis n   z: sin(spread*pi)   w: cos(spread*pi)
    float4 torus;    // x: ra  y: rb  z: capR  w: blend
    float4 caps;     // xy: cap centre 1   zw: cap centre 2   (field units)
    float4 env;      // x: energy  y: bass  z: spatial 0..1  w: halo gain
    float4 gains;    // x: glow  y: grain  z: flurry  w: scale factor (px per point)
    float4 pts;      // x: point scale  y: point gain  z: sim lerp  w: reactive low edge
    float4 pulse;    // x: head row  y: row scale  z: emitter radius  w: gain
    float4 cDeep, cCyan, cLight, cWhite, cBlue, cTeal, cRoom, cRoomEdge;
};

struct Particle {
    float4 pos;   // xyz: position (field units)   w: life
    float4 vel;   // xyz: velocity                 w: float timer
    float4 org;   // xyz: spawn origin             w: home radius
    float4 aux;   // x: seed  y: return timer  zw: previous xy (for interpolation)
};

struct SimUniforms {
    float4 axis;    // xy: n   z: sinA   w: cosA
    float4 torus;   // x: ra  y: rb  z: capR  w: blend
    float4 caps;    // xy: c1   zw: c2
    float4 cfg;     // x: dt  y: time  z: beat kick  w: reactive low edge
    float4 cfg2;    // x: puffGain  y: puffVmax  z: puffFloat  w: puffReach
};

struct OverlayUniforms {
    float4 res;     // xy: drawable size (px)   zw: room centre (px)
    float4 geom;    // x: room radius  y: puck radius  z: handle radius  w: volume 0..1
    float4 puck;    // xy: puck centre (px)   z: glyph on   w: omni 0..1 (1 = handles hidden)
    float4 hA;      // xy: handle A (px)   z: grabbed   w: unused
    float4 hB;      // xy: handle B (px)   z: grabbed   w: unused
    float4 nt;      // xy: notch (px)   z: direction (rad)   w: grabbed
    float4 ntSize;  // x: half width  y: half length (px)   z: lift dot grabbed  w: show volume
    float4 lift;    // xy: lift dot (px)   z: visible   w: unused
    float4 flags;   // x: ring count  y: listener x  z: listener y  w: has listener
};

// ---------------------------------------------------------------- shared SDF

// The ProtoTools capped torus: the arc between the two handles, thickened by `weight`, with rounded
// caps that grow as the arc widens. The exact function the web renderer and the app's kernel use.
static float fieldSDF(float3 p, float2 n, float sinA, float cosA, float4 torus, float4 caps) {
    float ra = torus.x, rb = torus.y, capR = torus.z, blend = torus.w;
    float yr = p.x * n.x + p.y * n.y;
    float xr = abs(p.x * (-n.y) + p.y * n.x);
    float k  = (cosA * xr > sinA * yr) ? (xr * sinA + yr * cosA) : sqrt(xr * xr + yr * yr);
    float k2 = sqrt(xr * xr + yr * yr);
    float pp = xr * xr + yr * yr + p.z * p.z;
    float d1 = sqrt(max(0.0, pp + ra * ra - 2.0 * ra * k))  - rb;
    float d2 = sqrt(max(0.0, pp + ra * ra - 2.0 * ra * k2)) - rb;
    float dS = length(float3(p.x - caps.x, p.y - caps.y, p.z)) - capR;
    float dE = length(float3(p.x - caps.z, p.y - caps.w, p.z)) - capR;
    float h  = clamp(0.5 * (dE - dS) / blend + 0.5, 0.0, 1.0);
    float sm = mix(dE, dS, h) - blend * h * (1.0 - h);
    return max(min(sm, d1), d2);
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

fragment float4 blitFragment(FSOut in [[stage_in]],
                             texture2d<float> src [[texture(0)]],
                             sampler smp [[sampler(0)]]) {
    return float4(src.sample(smp, in.uv).rgb, 1.0);
}

// ---------------------------------------------------------------- scene

fragment float4 sceneFragment(FSOut in [[stage_in]],
                              constant FieldUniforms &U [[buffer(0)]],
                              constant float4 *blobs    [[buffer(1)]],   // 24 flurry blobs: xy pos, z radius, w weight
                              constant float4 *halo     [[buffer(2)]],   // 6 pulse blobs, then 6 phase sets
                              texture2d<float> noise    [[texture(0)]],
                              sampler noiseSmp          [[sampler(0)]]) {
    float2 px = in.uv * U.res.xy;
    float2 C = U.res.zw, P = U.puck.xy;
    float R = max(U.geom.x, 1.0), Rf = max(U.geom.y, 1.0), puckR = max(U.geom.z, 1.0);

    float dC = length(px - C);
    float rr = dC / R;
    float inRoom = 1.0 - smoothstep(R - 1.0, R + 1.0, dC);

    float2 f = (px - P) / (2.0 * Rf);          // field space, rim at 0.5
    float d = fieldSDF(float3(f, 0.0), U.axis.xy, U.axis.z, U.axis.w, U.torus, U.caps);
    float fr = length(f);
    float px2f = 1.0 / (2.0 * Rf);             // one pixel in field units

    // room disc: dark navy-teal, darker toward the rim and the bottom (glass sphere shading)
    float3 col = mix(U.cRoom.rgb, U.cRoomEdge.rgb, smoothstep(0.35, 1.0, rr));
    col *= 1.0 - 0.28 * smoothstep(-0.2, 1.0, (px.y - C.y) / R);

    // aura: the SDF interior glows (palette ramp), soft spill outside, white core at the puck
    float inside = 1.0 - smoothstep(-6.0 * px2f, 2.0 * px2f, d);
    float spill  = exp(-max(d, 0.0) * 16.0);
    float core   = exp(-fr * 7.0);
    float g = inside * (0.11 + 0.12 * exp(-fr * 3.0)) + spill * 0.12 * (1.0 - inside);
    g += exp(-abs(d) * 30.0) * 0.16 * inside;
    g *= 0.80 + 0.45 * U.env.x;
    g += core * 0.22 * (1.0 + 0.5 * U.env.y);

    float fl = 0.0;
    float3 flc = float3(0.0);
    for (int i = 0; i < 24; i++) {
        float4 bb = blobs[i];
        float q = length(f - bb.xy) / max(bb.z, 0.001);
        float w = bb.w * (1.0 - smoothstep(0.1, 1.0, q));
        fl += w;
        flc += w * mix(U.cBlue.rgb, U.cTeal.rgb, float(i % 2));
    }
    g += fl * 0.10 * U.gains.z * inside;
    g *= U.gains.x;

    float3 aura = mix(mix(U.cDeep.rgb, U.cCyan.rgb, clamp(g * 2.0, 0.0, 1.0)),
                      U.cWhite.rgb, clamp((g - 0.5) * 2.0, 0.0, 1.0));

    // classic (sound all around): a smooth saturated disc, brighter toward the puck
    float gc = inside * (0.55 + 0.30 * exp(-fr * 2.2)) + spill * 0.25 * (1.0 - inside);
    gc *= 0.85 + 0.3 * U.env.x;
    float3 classic = mix(U.cBlue.rgb, mix(U.cCyan.rgb, U.cWhite.rgb, 0.35), clamp((gc - 0.55) * 2.2, 0.0, 1.0));
    classic = mix(classic, flc / max(fl, 0.001), clamp(fl * 0.35 * U.gains.z, 0.0, 0.45));
    classic *= 0.62 + 0.38 * exp(-fr * 2.6);        // depth: bright at the puck, deeper toward the rim, so the particles have something to show against
    classic += U.cWhite.rgb * core * 0.6 * (1.0 + 0.4 * U.env.y);

    float3 auraS = mix(col, aura, smoothstep(0.0, 0.55, g));
    float3 auraC = mix(col, classic, smoothstep(0.0, 0.5, gc));
    auraC = mix(auraC, auraC + flc * 0.12 * U.gains.z, inside);
    col = mix(auraC, auraS, U.env.z);

    // Aurora PulseField: soft wobbly blobs around the puck, one per band, layered translucently
    float acc = 0.0;
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

    // glow bleeding past the room rim (the lobes light up the edge)
    float outer = exp(-max(dC - R, 0.0) / (0.05 * R)) * (1.0 - inRoom) * smoothstep(0.0, 0.4, g) * 0.35;
    col += U.cCyan.rgb * outer;

    // grain (Aurora NoiseRGB)
    float t = U.geom.w;
    float n = noise.sample(noiseSmp, px / 96.0 + float2(t * 0.013, -t * 0.009)).r;
    col *= 1.0 + (n - 0.5) * 0.22 * U.gains.y;

    // glass room rim: faint bright ring, nothing outside the disc
    col += float3(0.10, 0.22, 0.28) * smoothstep(0.90, 0.985, rr) * (1.0 - smoothstep(0.985, 1.0, rr));
    col = mix(col, U.cCyan.rgb * outer, 1.0 - inRoom);
    return float4(col, 1.0);
}

// ---------------------------------------------------------------- particle simulation

kernel void simulateParticles(device Particle *parts   [[buffer(0)]],
                              constant SimUniforms &S  [[buffer(1)]],
                              constant float *flux     [[buffer(2)]],   // 24 bands
                              constant uint &count     [[buffer(3)]],
                              uint id [[thread_position_in_grid]]) {
    if (id >= count) return;
    Particle p = parts[id];
    float dt = S.cfg.x;
    float3 x = p.pos.xyz, v = p.vel.xyz;
    float life = p.pos.w - dt * 0.05 * 60.0;   // the app decrements per frame
    float flt = p.vel.w, home = p.org.w, seed = p.aux.x, rt = p.aux.y;
    float2 n = S.axis.xy;
    p.aux.zw = x.xy;                           // where it was, for the render interpolation

    // music -> puff: an outward impulse in this particle's own spectrum band (bass at the notch,
    // treble behind) plus a beat kick; puffed particles float free, then ease back to their radius.
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
        float vr = dot(v.xy, x.xy) / dist;     // a puff tops up to a ceiling, it never stacks
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
        const float eps = 0.001;
        float3 pr = x + v;
        float d0 = fieldSDF(pr, n, S.axis.z, S.axis.w, S.torus, S.caps);
        float3 grad = float3(fieldSDF(pr + float3(eps, 0, 0), n, S.axis.z, S.axis.w, S.torus, S.caps) - d0,
                             fieldSDF(pr + float3(0, eps, 0), n, S.axis.z, S.axis.w, S.torus, S.caps) - d0,
                             fieldSDF(pr + float3(0, 0, eps), n, S.axis.z, S.axis.w, S.torus, S.caps) - d0);
        float gl = length(grad);
        grad = gl > 1e-9 ? grad / gl : float3(0.0);
        // a floating particle barely feels the field, then it eases back in over the last 0.5 s
        float ks = flt > 0.5 ? 0.08 : (flt > 0.0 ? 0.08 + 0.92 * (1.0 - flt / 0.5) : 1.0);
        float3 f = -((d0 > 0.0 ? 1.2 : 0.5) * d0 * ks) * grad;
        v += f;
        nx = x + f + v;
    } else {
        float s = sin(life + S.cfg.y * 0.01);
        nx = p.org.xyz * s;
        life = 100.0;
    }

    float len = length(nx);
    if (len > 1.0) nx /= len;

    if (rt > 0.0 && home > 0.02) {             // back toward the radius it had before the puff
        float d2 = length(nx.xy);
        if (d2 > 0.02) {
            float g = (home - d2) * (flt > 0.0 ? 0.012 : 0.04);
            v.xy += nx.xy / d2 * g;
        }
        if (flt <= 0.0) rt -= dt;
    }
    float damp = flt > 0.0 ? 0.94 : 0.99;
    if (flt > 0.0 && dot(nx.xy, nx.xy) > 0.42) damp = 0.85;   // past the rim the puff dies quickly

    p.pos = float4(nx, life);
    p.vel = float4(v * damp, flt > 0.0 ? flt - dt : 0.0);
    p.org.w = home;
    p.aux.y = rt;
    parts[id] = p;
}

struct PointOut {
    float4 position  [[position]];
    float  pointSize [[point_size]];
    float  bright;
};

vertex PointOut particleVertex(uint vid [[vertex_id]],
                               device const Particle *parts [[buffer(0)]],
                               constant FieldUniforms &U    [[buffer(1)]],
                               texture2d<float> pulse       [[texture(0)]],
                               sampler pulseSmp             [[sampler(0)]]) {
    Particle pt = parts[vid];
    float2 n = U.axis.xy;
    float2 q = mix(pt.aux.zw, pt.pos.xy, U.pts.z);
    float dist = length(q);
    float ang = dist > 0.001 ? atan2(dot(q, float2(-n.y, n.x)), dot(q, n)) : 0.0;
    float u = 0.5 + 0.5 * ang / M_PI_F;                 // mirrored: bass at the notch, treble behind
    float v = U.pulse.y * abs(dist - U.pulse.z);
    float row = U.pulse.x + v;                          // newest row at head, older further along (repeat)
    float s1 = pulse.sample(pulseSmp, float2(u, row + 1.0 / 32.0)).r;
    float s2 = pulse.sample(pulseSmp, float2(u, row - 1.0 / 32.0)).r;
    float sd = fract(pt.aux.x * 7.13 + 0.37);
    float react = mix(0.12, 1.0, smoothstep(U.pts.w, U.pts.w + 0.15, sd));   // two populations
    float amt = sqrt(max(1.0 - v, 0.0)) * (s1 - s2) * U.pulse.w * react;
    float2 disp = (dist > 0.001 ? q / dist : float2(0.0)) * 0.05 * amt;
    float2 sp = U.puck.xy + (q + disp) * 2.0 * U.geom.y;
    float2 cs = sp / U.res.xy * 2.0 - 1.0;

    PointOut o;
    o.position = float4(cs.x, -cs.y, 0.0, 1.0);
    float a = abs(amt) * 2.0;
    o.pointSize = (0.9 + min(a * 1.2, 2.0)) * U.gains.w * U.pts.x * (0.7 + 0.6 * pt.aux.x);
    o.bright = 0.75 + a * 0.9;
    return o;
}

fragment float4 particleFragment(PointOut in [[stage_in]],
                                 float2 pc [[point_coord]],
                                 constant FieldUniforms &U [[buffer(0)]]) {
    float2 q = pc * 2.0 - 1.0;
    float a = max(0.0, 1.0 - length(q));
    return float4(float3(0.66, 0.92, 1.0) * in.bright * a * 0.5 * U.pts.y, 1.0);
}

// ---------------------------------------------------------------- overlay: orbs, rings, volume

static float lensAt(float r) {
    float x = clamp(r, 0.0, 1.0) * 63.0;
    int i = int(x);
    int j = min(63, i + 1);
    return mix(LENS[i], LENS[j], x - float(i));
}

// One glass orb (Aurora ShapeLayer "Orb"): refracts the scene through the lens table, with the
// gloss map's darken / contrast / rim approximated analytically so no app artwork is needed.
static float4 glassOrb(float2 px, float2 centre, float radius, float halfLen, float rot,
                       float darken, float tint, float glyphOn,
                       texture2d<float> scene, texture2d<float> glyph, sampler smp, float2 res) {
    float c = cos(-rot), s = sin(-rot);
    float2 dv = px - centre;
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
    float off = lensAt(r) * 2.0 - 0.5;                       // Aurora Orb: lensTable*2 - 0.5
    float2 local = float2(dir.x * off, along + dir.y * off) * radius;
    float cr = cos(rot), sr = sin(rot);
    float2 sp = centre + float2(local.x * cr - local.y * sr, local.x * sr + local.y * cr);
    float3 bg = scene.sample(smp, sp / res).rgb;
    bg = mix(bg, bg * float3(0.30, 0.62, 1.0) + float3(0.02, 0.16, 0.38), tint);

    // gloss: a soft highlight toward the top-left, a darker interior, a bright thin rim
    float2 h = l - float2(-0.34, -0.42);
    float spec = exp(-dot(h, h) * 5.5);
    float3 col = bg * mix(1.0, 0.72, darken * (1.0 - smoothstep(0.7, 0.95, r)));
    col += float3(0.86, 0.95, 1.0) * spec * (0.26 + 0.20 * darken);
    float rim = smoothstep(0.74, 0.90, r) * (1.0 - smoothstep(0.93, 1.0, r));
    col += float3(0.90, 0.98, 1.0) * rim * (0.35 + 1.05 * darken);
    col *= 1.0 - 0.18 * smoothstep(0.55, 1.0, r);

    if (glyphOn > 0.5) {
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
                                sampler smp                 [[sampler(0)]]) {
    float2 px = in.uv * O.res.xy;
    float3 col = scene.sample(smp, in.uv).rgb;
    float R = max(O.geom.x, 1.0);
    float scale = max(R / 138.6, 0.5);         // px per point at the reference layout (R = 0.385 W)

    // --- glass orbs: a contact shadow, the handles (or the lift dot in classic mode), then the puck ---
    // (the app's own shadow orb reaches 4.5 puck radii at 0.75 — over this renderer's brighter aura
    //  that reads as a grey donut, so it is kept tight to the glass)
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
        float4 a = glassOrb(px, O.hA.xy, hr, 0.0, 0.0, 0.35, 0.0, 0.0, scene, glyph, smp, O.res.xy);
        float4 b = glassOrb(px, O.hB.xy, hr, 0.0, 0.0, 0.35, 0.0, 0.0, scene, glyph, smp, O.res.xy);
        float4 nn = glassOrb(px, O.nt.xy, O.ntSize.x, O.ntSize.y, O.nt.z, 0.40, 0.0, 0.0, scene, glyph, smp, O.res.xy);
        col = mix(col, a.rgb, a.a * alpha);
        col = mix(col, b.rgb, b.a * alpha);
        col = mix(col, nn.rgb, nn.a * alpha);
    } else if (O.lift.z > 0.5) {
        float hr = max(O.geom.z * 0.85, 1.0);
        float gl = O.ntSize.z * (1.0 - smoothstep(0.0, 1.0, length(px - O.lift.xy) / (hr * 3.4)));
        col = mix(col, float3(1.0), 0.28 * gl);
        float4 ld = glassOrb(px, O.lift.xy, hr, 0.0, 0.0, 0.50, 0.0, 0.0, scene, glyph, smp, O.res.xy);
        col = mix(col, ld.rgb, ld.a);
    }
    float4 pk = glassOrb(px, O.puck.xy, O.geom.y, 0.0, 0.0, 0.25, 0.85, O.puck.z, scene, glyph, smp, O.res.xy);
    col = mix(col, pk.rgb, pk.a);

    // --- speaker rings (the app's SnappingEllipse) ---
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

    // --- volume rim: 44 ticks over a 104-degree arc at the bottom, the current one longer ---
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
