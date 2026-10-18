package space.troubadour.app.field

/**
 * The sound field in OpenGL ES 3.1 — the same four passes as the iOS renderer
 * (apps/ios/.../Field/FieldShaders.metal), which is itself a port of the agent's WebGL field:
 *
 *   1. scene    — room disc, capped-torus aura in the live palette, flurry blobs, pulse halo
 *   2. blit     — upscale the low-res scene
 *   3. particles— a compute kernel for the ProtoTools simulation + additive points
 *   4. overlay  — glass orbs through the app's lens table, speaker rings, volume rim
 *
 * Field space: the puck is the origin, the room rim is at 0.5, screen y is down.
 */
object FieldShaders {

    private const val COMMON = """
uniform vec4 uRes;      // xy: drawable px   zw: room centre px
uniform vec4 uGeom;     // x: room radius  y: field radius  z: puck radius  w: time
uniform vec4 uPuck;     // xy: puck centre px
uniform vec4 uAxis;     // xy: arc axis n   z: sinA   w: cosA
uniform vec4 uTorus;    // ra, rb, capR, blend
uniform vec4 uCaps;     // c1.xy, c2.xy  (field units)
uniform vec4 uEnv;      // energy, bass, spatial 0..1, halo gain
uniform vec4 uGains;    // glow, grain, flurry, px-per-dp
uniform vec4 uPts;      // point scale, point gain, sim lerp, reactive low edge
uniform vec4 uPulse;    // head row, row scale, emitter radius, gain
uniform vec4 cDeep, cCyan, cLight, cWhite, cBlue, cTeal, cRoom, cRoomEdge;
"""

    private const val SDF = """
float fieldSDF(vec3 p, vec2 n, float sinA, float cosA, vec4 torus, vec4 caps) {
    float ra = torus.x, rb = torus.y, capR = torus.z, blend = torus.w;
    float yr = p.x * n.x + p.y * n.y;
    float xr = abs(p.x * (-n.y) + p.y * n.x);
    float k  = (cosA * xr > sinA * yr) ? (xr * sinA + yr * cosA) : sqrt(xr * xr + yr * yr);
    float k2 = sqrt(xr * xr + yr * yr);
    float pp = xr * xr + yr * yr + p.z * p.z;
    float d1 = sqrt(max(0.0, pp + ra * ra - 2.0 * ra * k))  - rb;
    float d2 = sqrt(max(0.0, pp + ra * ra - 2.0 * ra * k2)) - rb;
    float dS = length(vec3(p.x - caps.x, p.y - caps.y, p.z)) - capR;
    float dE = length(vec3(p.x - caps.z, p.y - caps.w, p.z)) - capR;
    float h  = clamp(0.5 * (dE - dS) / blend + 0.5, 0.0, 1.0);
    float sm = mix(dE, dS, h) - blend * h * (1.0 - h);
    return max(min(sm, d1), d2);
}
"""

    /** One oversized triangle; uv has its origin at the top left, like the textures. */
    const val FULLSCREEN_VS = """#version 310 es
precision highp float;
out vec2 vUv;
void main() {
    vec2 p = vec2(gl_VertexID == 2 ? 3.0 : -1.0, gl_VertexID == 1 ? 3.0 : -1.0);
    vUv = vec2(p.x * 0.5 + 0.5, 0.5 - p.y * 0.5);
    gl_Position = vec4(p, 0.0, 1.0);
}"""

    const val BLIT_FS = """#version 310 es
precision highp float;
in vec2 vUv;
uniform sampler2D uSrc;
layout(location = 0) out vec4 fragColor;
void main() { fragColor = vec4(texture(uSrc, vUv).rgb, 1.0); }"""

    val SCENE_FS = """#version 310 es
precision highp float;
in vec2 vUv;
$COMMON
uniform vec4 uBlob[24];
uniform vec4 uHaloE[6];
uniform vec4 uHaloP[6];
uniform sampler2D uNoise;
layout(location = 0) out vec4 fragColor;
$SDF
void main() {
    vec2 px = vUv * uRes.xy;
    vec2 C = uRes.zw, P = uPuck.xy;
    float R = max(uGeom.x, 1.0), Rf = max(uGeom.y, 1.0), puckR = max(uGeom.z, 1.0);
    float dC = length(px - C);
    float rr = dC / R;
    float inRoom = 1.0 - smoothstep(R - 1.0, R + 1.0, dC);
    vec2 f = (px - P) / (2.0 * Rf);
    float d = fieldSDF(vec3(f, 0.0), uAxis.xy, uAxis.z, uAxis.w, uTorus, uCaps);
    float fr = length(f);
    float px2f = 1.0 / (2.0 * Rf);

    vec3 col = mix(cRoom.rgb, cRoomEdge.rgb, smoothstep(0.35, 1.0, rr));
    col *= 1.0 - 0.28 * smoothstep(-0.2, 1.0, (px.y - C.y) / R);

    float inside = 1.0 - smoothstep(-6.0 * px2f, 2.0 * px2f, d);
    float spill  = exp(-max(d, 0.0) * 16.0);
    float core   = exp(-fr * 7.0);
    float g = inside * (0.11 + 0.12 * exp(-fr * 3.0)) + spill * 0.12 * (1.0 - inside);
    g += exp(-abs(d) * 30.0) * 0.16 * inside;
    g *= 0.80 + 0.45 * uEnv.x;
    g += core * 0.22 * (1.0 + 0.5 * uEnv.y);

    float fl = 0.0;
    vec3 flc = vec3(0.0);
    for (int i = 0; i < 24; i++) {
        vec4 bb = uBlob[i];
        float q = length(f - bb.xy) / max(bb.z, 0.001);
        float w = bb.w * (1.0 - smoothstep(0.1, 1.0, q));
        fl += w;
        flc += w * mix(cBlue.rgb, cTeal.rgb, mod(float(i), 2.0));
    }
    g += fl * 0.10 * uGains.z * inside;
    g *= uGains.x;

    vec3 aura = mix(mix(cDeep.rgb, cCyan.rgb, clamp(g * 2.0, 0.0, 1.0)), cWhite.rgb, clamp((g - 0.5) * 2.0, 0.0, 1.0));

    float gc = inside * (0.55 + 0.30 * exp(-fr * 2.2)) + spill * 0.25 * (1.0 - inside);
    gc *= 0.85 + 0.3 * uEnv.x;
    vec3 classic = mix(cBlue.rgb, mix(cCyan.rgb, cWhite.rgb, 0.35), clamp((gc - 0.55) * 2.2, 0.0, 1.0));
    classic = mix(classic, flc / max(fl, 0.001), clamp(fl * 0.35 * uGains.z, 0.0, 0.45));
    classic *= 0.62 + 0.38 * exp(-fr * 2.6);        // depth: bright at the puck, deeper toward the rim, so the particles have something to show against
    classic += cWhite.rgb * core * 0.6 * (1.0 + 0.4 * uEnv.y);

    vec3 auraS = mix(col, aura, smoothstep(0.0, 0.55, g));
    vec3 auraC = mix(col, classic, smoothstep(0.0, 0.5, gc));
    auraC = mix(auraC, auraC + flc * 0.12 * uGains.z, inside);
    col = mix(auraC, auraS, uEnv.z);

    float acc = 0.0;
    for (int k = 0; k < 6; k++) {
        vec4 pe = uHaloE[k];
        vec4 ph = uHaloP[k];
        vec2 c = P + pe.xy * puckR;
        vec2 dv = px - c;
        float dd = length(dv) / puckR;
        float th = atan(dv.y, dv.x);
        float rad = pe.z * (1.0 + ph.z * cos(2.0 * th + ph.x) + ph.w * cos(3.0 * th + ph.y));
        float cov = 1.0 - smoothstep(rad - 0.22, rad + 0.22, dd);
        acc = acc + cov * pe.w * (1.0 - acc);
    }
    acc *= uEnv.w * mix(0.75, 1.0, uEnv.z);
    col = mix(col, mix(cWhite.rgb, vec3(1.0), 0.5), clamp(acc, 0.0, 0.97));

    float outer = exp(-max(dC - R, 0.0) / (0.05 * R)) * (1.0 - inRoom) * smoothstep(0.0, 0.4, g) * 0.35;
    col += cCyan.rgb * outer;

    float t = uGeom.w;
    float n = texture(uNoise, px / 96.0 + vec2(t * 0.013, -t * 0.009)).r;
    col *= 1.0 + (n - 0.5) * 0.22 * uGains.y;

    col += vec3(0.10, 0.22, 0.28) * smoothstep(0.90, 0.985, rr) * (1.0 - smoothstep(0.985, 1.0, rr));
    col = mix(col, cCyan.rgb * outer, 1.0 - inRoom);
    fragColor = vec4(col, 1.0);
}"""

    /** The ProtoTools particle kernel: one invocation per particle, the app's fixed 30 Hz step. */
    val SIM_CS = """#version 310 es
precision highp float;
layout(local_size_x = 64) in;
layout(std430, binding = 0) buffer Parts { vec4 d[]; };   // 4 vec4 per particle: pos+life, vel+float, org+home, aux
uniform vec4 uAxis;
uniform vec4 uTorus;
uniform vec4 uCaps;
uniform vec4 uCfg;    // dt, time, beat kick, reactive low edge
uniform vec4 uCfg2;   // puffGain, puffVmax, puffFloat, puffReach
uniform float uFlux[24];
uniform int uCount;
$SDF
void main() {
    uint gid = gl_GlobalInvocationID.x;
    if (int(gid) >= uCount) return;
    uint b = gid * 4u;
    vec4 pos = d[b], vel = d[b + 1u], org = d[b + 2u], aux = d[b + 3u];
    float dt = uCfg.x;
    vec3 x = pos.xyz, v = vel.xyz;
    float life = pos.w - dt * 0.05 * 60.0;
    float flt = vel.w, home = org.w, seed = aux.x, rt = aux.y;
    vec2 n = uAxis.xy;
    aux.zw = x.xy;

    float sdr = fract(seed * 7.13 + 0.37);
    float tt = clamp((sdr - uCfg.w) / 0.15, 0.0, 1.0);
    float react = 0.12 + 0.88 * tt * tt * (3.0 - 2.0 * tt);
    float dist = length(x.xy);
    if (dist > 0.02) {
        float ang = atan(-x.x * n.y + x.y * n.x, x.x * n.x + x.y * n.y);
        float ub = clamp((0.5 + 0.5 * ang / 3.14159265) * 24.0 - 0.5, 0.0, 23.999);
        int b0 = int(ub);
        int b1 = min(23, b0 + 1);
        float tb = ub - float(b0);
        float fl = 0.75 * (uFlux[b0] * (1.0 - tb) + uFlux[b1] * tb)
                 + 0.25 * (uFlux[max(0, b0 - 1)] * (1.0 - tb) + uFlux[min(23, b1 + 1)] * tb);
        float home0 = home > 0.02 ? home : dist;
        float att = clamp(1.0 - (dist - home0) / max(0.05, uCfg2.w), 0.0, 1.0);
        float imp = react * (uCfg2.x * fl + uCfg.z) * att;
        float vr = dot(v.xy, x.xy) / dist;
        if (imp > 0.0002 && vr < uCfg2.y && dist < 0.6) {
            float add = min(imp, uCfg2.y - vr);
            v.xy += x.xy / dist * add;
            if (flt <= 0.0 && rt <= 0.0) home = home > 0.02 ? home + clamp(dist - home, -0.02, 0.02) : dist;
            flt = max(flt, uCfg2.z * react);
            rt = uCfg2.z * 1.5;
        }
    }

    vec3 nx;
    if (life > 0.0) {
        float eps = 0.001;
        vec3 pr = x + v;
        float d0 = fieldSDF(pr, n, uAxis.z, uAxis.w, uTorus, uCaps);
        vec3 grad = vec3(fieldSDF(pr + vec3(eps, 0.0, 0.0), n, uAxis.z, uAxis.w, uTorus, uCaps) - d0,
                         fieldSDF(pr + vec3(0.0, eps, 0.0), n, uAxis.z, uAxis.w, uTorus, uCaps) - d0,
                         fieldSDF(pr + vec3(0.0, 0.0, eps), n, uAxis.z, uAxis.w, uTorus, uCaps) - d0);
        float gl = length(grad);
        grad = gl > 1e-9 ? grad / gl : vec3(0.0);
        float ks = flt > 0.5 ? 0.08 : (flt > 0.0 ? 0.08 + 0.92 * (1.0 - flt / 0.5) : 1.0);
        vec3 fo = -((d0 > 0.0 ? 1.2 : 0.5) * d0 * ks) * grad;
        v += fo;
        nx = x + fo + v;
    } else {
        float s = sin(life + uCfg.y * 0.01);
        nx = org.xyz * s;
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

    d[b] = vec4(nx, life);
    d[b + 1u] = vec4(v * damp, flt > 0.0 ? flt - dt : 0.0);
    org.w = home;
    aux.y = rt;
    d[b + 2u] = org;
    d[b + 3u] = aux;
}"""

    // The particles come in as vertex attributes from the same buffer the compute shader writes —
    // ES 3.1 does not promise storage blocks in the vertex stage, but attributes work everywhere.
    val PART_VS = """#version 310 es
precision highp float;
layout(location = 0) in vec4 aPos;    // xyz position, w life
layout(location = 1) in vec4 aAux;    // x seed, y return timer, zw previous xy
$COMMON
uniform sampler2D uPulseTex;
out float vBright;
void main() {
    vec4 pos = aPos;
    vec4 aux = aAux;
    vec2 n = uAxis.xy;
    vec2 q = mix(aux.zw, pos.xy, uPts.z);
    float dist = length(q);
    float ang = dist > 0.001 ? atan(dot(q, vec2(-n.y, n.x)), dot(q, n)) : 0.0;
    float u = 0.5 + 0.5 * ang / 3.14159265;
    float v = uPulse.y * abs(dist - uPulse.z);
    float row = uPulse.x + v;
    float s1 = texture(uPulseTex, vec2(u, row + 1.0 / 32.0)).r;
    float s2 = texture(uPulseTex, vec2(u, row - 1.0 / 32.0)).r;
    float sd = fract(aux.x * 7.13 + 0.37);
    float react = mix(0.12, 1.0, smoothstep(uPts.w, uPts.w + 0.15, sd));
    float amt = sqrt(max(1.0 - v, 0.0)) * (s1 - s2) * uPulse.w * react;
    vec2 disp = (dist > 0.001 ? q / dist : vec2(0.0)) * 0.05 * amt;
    vec2 sp = uPuck.xy + (q + disp) * 2.0 * uGeom.y;
    vec2 cs = sp / uRes.xy * 2.0 - 1.0;
    gl_Position = vec4(cs.x, -cs.y, 0.0, 1.0);
    float a = abs(amt) * 2.0;
    gl_PointSize = (0.9 + min(a * 1.2, 2.0)) * uGains.w * uPts.x * (0.7 + 0.6 * aux.x);
    vBright = 0.75 + a * 0.9;
}"""

    val PART_FS = """#version 310 es
precision mediump float;
in float vBright;
uniform vec4 uPts;
layout(location = 0) out vec4 fragColor;
void main() {
    vec2 q = gl_PointCoord * 2.0 - 1.0;
    float a = max(0.0, 1.0 - length(q));
    fragColor = vec4(vec3(0.66, 0.92, 1.0) * vBright * a * 0.5 * uPts.y, 1.0);
}"""

    /** Glass orbs (Aurora lens table), speaker rings and the volume rim, over the rendered scene. */
    val OVERLAY_FS = """#version 310 es
precision highp float;
in vec2 vUv;
uniform vec4 uO0;   // res.xy, centre.xy
uniform vec4 uO1;   // R, puckR, handleR, volume
uniform vec4 uO2;   // puck.xy, glyphOn, omni
uniform vec4 uO3;   // handle A xy, grabbed, -
uniform vec4 uO4;   // handle B xy, grabbed, -
uniform vec4 uO5;   // notch xy, dir, grabbed
uniform vec4 uO6;   // notch half width, half length, lift grabbed, show volume
uniform vec4 uO7;   // lift dot xy, visible, -
uniform vec4 uO8;   // ring count, listener xy, has listener
uniform vec4 uRings[8];
uniform sampler2D uScene;
uniform sampler2D uGlyph;
layout(location = 0) out vec4 fragColor;

const float LENS[64] = float[64](
    0.251,0.261,0.271,0.281,0.291,0.302,0.312,0.322,0.332,0.342,0.352,0.362,0.372,0.383,0.393,0.403,
    0.413,0.423,0.433,0.443,0.453,0.463,0.472,0.482,0.492,0.502,0.512,0.521,0.531,0.541,0.550,0.560,
    0.569,0.579,0.588,0.597,0.606,0.616,0.625,0.633,0.642,0.651,0.659,0.667,0.676,0.684,0.691,0.699,
    0.706,0.712,0.719,0.725,0.730,0.734,0.738,0.741,0.742,0.741,0.738,0.731,0.719,0.697,0.657,0.570);

float lensAt(float r) {
    float x = clamp(r, 0.0, 1.0) * 63.0;
    int i = int(x);
    int j = min(63, i + 1);
    return mix(LENS[i], LENS[j], x - float(i));
}

vec4 glassOrb(vec2 px, vec2 centre, float radius, float halfLen, float rot, float darken, float tint, float glyphOn) {
    float c = cos(-rot), s = sin(-rot);
    vec2 dv = px - centre;
    vec2 l = vec2(dv.x * c - dv.y * s, dv.x * s + dv.y * c) / max(radius, 0.5);
    float along = 0.0;
    if (halfLen > radius) {
        float ext = halfLen / radius - 1.0;
        along = clamp(l.y, -ext, ext);
        l.y -= along;
    }
    float r = length(l);
    float aa = 1.0 - smoothstep(1.0 - 2.0 / max(radius, 2.0), 1.0, r);
    if (aa <= 0.001) return vec4(0.0);
    vec2 dir = r > 0.0001 ? l / r : vec2(0.0);
    float off = lensAt(r) * 2.0 - 0.5;
    vec2 local = vec2(dir.x * off, along + dir.y * off) * radius;
    float cr = cos(rot), sr = sin(rot);
    vec2 sp = centre + vec2(local.x * cr - local.y * sr, local.x * sr + local.y * cr);
    vec3 bg = texture(uScene, sp / uO0.xy).rgb;
    bg = mix(bg, bg * vec3(0.30, 0.62, 1.0) + vec3(0.02, 0.16, 0.38), tint);
    vec2 h = l - vec2(-0.34, -0.42);
    float spec = exp(-dot(h, h) * 5.5);
    vec3 col = bg * mix(1.0, 0.72, darken * (1.0 - smoothstep(0.7, 0.95, r)));
    col += vec3(0.86, 0.95, 1.0) * spec * (0.26 + 0.20 * darken);
    float rim = smoothstep(0.74, 0.90, r) * (1.0 - smoothstep(0.93, 1.0, r));
    col += vec3(0.90, 0.98, 1.0) * rim * (0.35 + 1.05 * darken);
    col *= 1.0 - 0.18 * smoothstep(0.55, 1.0, r);
    if (glyphOn > 0.5) {
        vec2 guv = vec2(0.5 + 0.5 * l.x * 1.30, 0.5 + 0.5 * l.y * 1.30);
        float a = texture(uGlyph, guv).a;
        col = mix(col, vec3(1.0), a * 0.95);
    }
    return vec4(col, aa);
}

float ringMask(vec2 px, vec2 c, float radius, float w) {
    float d = abs(length(px - c) - radius);
    return 1.0 - smoothstep(w * 0.5, w * 0.5 + 1.5, d);
}

void main() {
    vec2 px = vUv * uO0.xy;
    vec3 col = texture(uScene, vUv).rgb;
    float R = max(uO1.x, 1.0);
    float scale = max(R / 138.6, 0.5);

    float dsh = length(px - uO2.xy) / max(uO1.y * 2.0, 1.0);
    col = mix(col, vec3(0.02, 0.08, 0.14), 0.40 * smoothstep(1.0, 0.30, dsh));

    float omni = uO2.w;
    if (omni < 0.999) {
        float alpha = 1.0 - omni;
        float hr = max(uO1.z, 1.0);
        float ga = uO3.z * (1.0 - smoothstep(0.0, 1.0, length(px - uO3.xy) / (hr * 3.4)));
        float gb = uO4.z * (1.0 - smoothstep(0.0, 1.0, length(px - uO4.xy) / (hr * 3.4)));
        float gn = uO5.w * (1.0 - smoothstep(0.0, 1.0, length(px - uO5.xy) / (max(uO6.y, 1.0) * 2.7)));
        col = mix(col, vec3(1.0), 0.28 * clamp(ga + gb + gn, 0.0, 1.0));
        vec4 a = glassOrb(px, uO3.xy, hr, 0.0, 0.0, 0.35, 0.0, 0.0);
        vec4 b = glassOrb(px, uO4.xy, hr, 0.0, 0.0, 0.35, 0.0, 0.0);
        vec4 nn = glassOrb(px, uO5.xy, uO6.x, uO6.y, uO5.z, 0.40, 0.0, 0.0);
        col = mix(col, a.rgb, a.a * alpha);
        col = mix(col, b.rgb, b.a * alpha);
        col = mix(col, nn.rgb, nn.a * alpha);
    } else if (uO7.z > 0.5) {
        float hr = max(uO1.z * 0.85, 1.0);
        float gl = uO6.z * (1.0 - smoothstep(0.0, 1.0, length(px - uO7.xy) / (hr * 3.4)));
        col = mix(col, vec3(1.0), 0.28 * gl);
        vec4 ld = glassOrb(px, uO7.xy, hr, 0.0, 0.0, 0.50, 0.0, 0.0);
        col = mix(col, ld.rgb, ld.a);
    }
    vec4 pk = glassOrb(px, uO2.xy, uO1.y, 0.0, 0.0, 0.25, 0.85, uO2.z);
    col = mix(col, pk.rgb, pk.a);

    int nr = int(uO8.x);
    float rr = R * 0.085;
    for (int i = 0; i < 8; i++) {
        if (i >= nr) break;
        vec4 rg = uRings[i];
        float snapped = rg.w;
        float rad = rr * mix(1.0, 1.18, snapped);
        float a = ringMask(px, rg.xy, rad, mix(2.0, 2.6, snapped) * scale);
        float alpha = mix(mix(0.16, 0.26, rg.z), 0.75, snapped);
        col = mix(col, vec3(0.92, 0.98, 1.0), a * alpha);
        col = mix(col, vec3(0.80, 0.88, 0.92), ringMask(px, rg.xy, rr * 0.62, 1.2 * scale) * 0.09);
        col = mix(col, vec3(0.78, 0.88, 0.92), (1.0 - smoothstep(rad - 1.0, rad, length(px - rg.xy))) * 0.04);
    }
    if (uO8.w > 0.5) {
        col = mix(col, vec3(0.90, 0.95, 0.97), ringMask(px, uO8.yz, 4.0 * scale, 1.2 * scale) * 0.45);
    }

    if (uO6.w > 0.5) {
        float VH = 52.0 * 3.14159265 / 180.0;
        vec2 dv = px - uO0.zw;
        float dist = length(dv);
        float Rv = R * 1.10, tl = R * 0.065;
        if (dist > Rv - 4.0 * scale && dist < Rv + tl * 1.8) {
            float a = atan(dv.y, dv.x);
            if (abs(a - 3.14159265 * 0.5) < VH + 0.08) {
                float n = 44.0;
                float f = (3.14159265 * 0.5 + VH - a) / (2.0 * VH);
                float ff = floor(clamp(f, 0.0, 1.0) * n + 0.5) / n;
                float ang = 3.14159265 * 0.5 + VH - ff * 2.0 * VH;
                vec2 u = vec2(cos(ang), sin(ang));
                bool on = ff <= uO1.w + 1e-6;
                bool cur = abs(ff - uO1.w) <= 0.5 / n;
                vec2 p0 = uO0.zw + u * Rv;
                vec2 p1 = uO0.zw + u * (Rv + tl * (cur ? 1.6 : 1.0));
                vec2 pa = px - p0, ba = p1 - p0;
                float t = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-4), 0.0, 1.0);
                float w = (cur ? 2.2 : 1.6) * scale * 0.5;
                float m = 1.0 - smoothstep(w, w + 1.2, length(pa - ba * t));
                col = mix(col, vec3(1.0), m * (on ? (cur ? 1.0 : 0.85) : 0.28));
            }
        }
    }
    fragColor = vec4(col, 1.0);
}"""
}
