// DRUMS: a topographic drum-pattern map and three drum-engine voices.
//
// The map is a 5x5 grid of nodes. Each node holds, for kick, snare and hats, a
// level 0..255 for each of 32 steps (32nd notes, one bar). X and Y move
// through the map, blending the four nearest nodes. A step sounds when its
// level beats the part's density, and accents above 192. CHAOS adds a random
// per-bar lift to every part. The node tables are this project's own, built
// from simple rules:
//   X: straight (four-on-the-floor kick, offbeat hats) .. broken (syncopated
//      kick, displaced snare, sixteenth hats)
//   Y: sparse .. busy (ghost notes, fills, more off-grid hits)
// plus a fixed pseudo-random variation per node, so neighbouring nodes differ.
#pragma once
#include <cmath>
#include <cstdint>

#include "synth_voice.h"

const int kDrumParts = 3;              // kick, snare, hats
const int kDrumSteps = 32;
const int kDrumNodes = 5;

struct DrumMap {
  uint8_t node[kDrumNodes][kDrumNodes][kDrumParts][kDrumSteps];

  static float hash(int a, int b, int c, int d) {        // fixed pseudo-random in [0, 1)
    uint32_t h = 2166136261u;
    for (int v : {a, b, c, d}) { h ^= uint32_t(v + 77); h *= 16777619u; h ^= h >> 13; }
    return (h & 0xffff) / 65536.0f;
  }

  // How strong a 32nd-note position is in the bar: downbeat > half > quarter > 8th > 16th > 32nd.
  static float metric(int s) {
    if (s % 16 == 0) return s == 0 ? 1.0f : 0.85f;
    if (s % 8 == 0) return 0.7f;
    if (s % 4 == 0) return 0.5f;
    if (s % 2 == 0) return 0.3f;
    return 0.1f;
  }

  static float level(int part, int s, float x, float y, float noise) {
    float m = metric(s), v = 0;
    bool eighth_off = s % 8 == 4, sixteenth = s % 2 == 0;
    if (part == 0) {                   // kick
      float straight = (s % 8 == 0) ? 0.95f : 0.15f * m;
      static const int kSync[] = {0, 6, 12, 20, 22, 28};
      float broken = s == 0 ? 1.0f : 0.25f * m;
      for (int k : kSync) if (s == k) broken = 0.8f;
      v = straight + (broken - straight) * x;
    } else if (part == 1) {            // snare: backbeat, displaced and ghosted as x and y grow
      float back = (s == 8 || s == 24) ? 0.95f : 0.12f * m;
      float moved = (s == 12 || s == 24 || s == 30) ? 0.85f : 0.2f * m;
      v = back + (moved - back) * x;
      if (sixteenth && !(s % 8 == 0)) v += 0.35f * y;             // ghost notes
    } else {                           // hats
      float off = eighth_off ? 0.9f : (s % 8 == 0 ? 0.45f : 0.15f * m);
      float busy = sixteenth ? 0.55f + 0.35f * m : 0.2f;
      v = off + (busy - off) * (0.5f * x + 0.5f * y);
    }
    v += (y - 0.5f) * 0.35f * (1.0f - m);                         // busier: weak steps rise
    v += (noise - 0.5f) * (0.25f + 0.25f * y);
    return std::max(0.0f, std::min(1.0f, v));
  }

  void init() {
    for (int i = 0; i < kDrumNodes; ++i)
      for (int j = 0; j < kDrumNodes; ++j)
        for (int p = 0; p < kDrumParts; ++p)
          for (int s = 0; s < kDrumSteps; ++s) {
            float x = i / float(kDrumNodes - 1), y = j / float(kDrumNodes - 1);
            node[i][j][p][s] = uint8_t(255.0f * level(p, s, x, y, hash(i, j, p, s)));
          }
  }

  // x, y in 0..1: the four nearest nodes, blended.
  uint8_t read(int part, int step, float x, float y) const {
    float fx = std::max(0.0f, std::min(0.999f, x)) * (kDrumNodes - 1);
    float fy = std::max(0.0f, std::min(0.999f, y)) * (kDrumNodes - 1);
    int i = int(fx), j = int(fy);
    float ax = fx - i, ay = fy - j;
    float a = node[i][j][part][step], b = node[i + 1][j][part][step];
    float c = node[i][j + 1][part][step], d = node[i + 1][j + 1][part][step];
    return uint8_t((a + (b - a) * ax) + ((c + (d - c) * ax) - (a + (b - a) * ax)) * ay);
  }
};

struct Drums {
  DrumMap map;
  SynthVoice voice[kDrumParts];
  int step = 0;
  long last_step = -1;                 // absolute step count at the last hit check
  uint8_t lift[kDrumParts]{};          // CHAOS: this bar's random lift per part
  uint32_t rng = 12345;
  int idle = 0;                        // chunks since PLAY went off: let the last hits ring out

  void init() {
    map.init();
    for (auto& v : voice) v.init(false);
  }

  float random01() { rng = rng * 1664525u + 1013904223u; return (rng >> 8) / 16777216.0f; }

  // Render one chunk into l/r (n = 48). beat_to: the drums' clock position (in beats) at the
  // chunk's end; every 1/8 beat crossed since the last chunk is a step.
  void render(float* l, float* r, int n, bool play, double beat_to, float x, float y, float chaos,
              const float* density, float level, float tone, float decay, float note) {
    static const int kEngine[kDrumParts] = {21, 22, 23};       // kick, snare, hi-hat engines
    static const float kNote[kDrumParts] = {0, 36, 60};        // above the key's low root
    static const float kPan[kDrumParts] = {0.5f, 0.45f, 0.6f};
    idle = play ? 0 : idle + 1;
    if (idle > 2000) return;           // ~2 s after stopping: silent, skip the voices
    long now = long(std::floor(beat_to * 8.0));                // 8 steps per beat
    if (!play || last_step < 0 || now < last_step || now - last_step > kDrumSteps) last_step = now;
    if (play) {
      while (last_step < now) {
        ++last_step;
        step = int(((last_step % kDrumSteps) + kDrumSteps) % kDrumSteps);   // the bar follows the clock
        if (step == 0)
          for (auto& d : lift) d = uint8_t(255.0f * chaos * 0.5f * random01());
        for (int p = 0; p < kDrumParts; ++p) {
          int v = std::min(255, map.read(p, step, x, y) + lift[p]);
          if (v > 255 - int(255.0f * density[p]) && density[p] > 0) {
            voice[p].patch.note = note + kNote[p];
            voice[p].gain = level * (v > 192 ? 1.0f : 0.65f);
            voice[p].trigger = 2;
          }
        }
      }
    }
    for (int p = 0; p < kDrumParts; ++p) {
      SynthVoice& v = voice[p];
      v.patch.engine = kEngine[p];
      v.patch.harmonics = 0.5f; v.patch.timbre = tone; v.patch.morph = decay;
      v.patch.decay = decay; v.patch.lpg_colour = 0.5f;
      for (int b = 0; b < n; b += 24) v.render(l + b, r + b, 24, kPan[p]);
    }
  }
};
