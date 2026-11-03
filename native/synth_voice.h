// One synth-engine voice (the vendored macro-oscillator voice with its low-pass gate).
#pragma once
#include <cstring>

#include "plaits/dsp/voice.h"
#include "stmlib/utils/buffer_allocator.h"

// One synth-engine voice. Balls ping its low-pass gate with a trigger; rings hold it
// open with the level input, so a ring note gets darker as well as quieter as it fades.
struct SynthVoice {
  plaits::Voice voice;
  char ram[16384];
  plaits::Patch patch;
  plaits::Modulations mod;
  float gain = 0;
  int trigger = 0;                     // blocks left with the trigger input high
  void init(bool held) {
    stmlib::BufferAllocator allocator(ram, sizeof(ram));
    voice.Init(&allocator);
    std::memset(&patch, 0, sizeof patch);
    std::memset(&mod, 0, sizeof mod);
    patch.note = 60; patch.harmonics = 0.5f; patch.timbre = 0.5f; patch.morph = 0.5f;
    patch.decay = 0.5f; patch.lpg_colour = 0.5f;
    mod.trigger_patched = !held;
    mod.level_patched = held;
  }
  void render(float* l, float* r, int n, float pan) {      // n <= 24
    plaits::Voice::Frame frames[24];
    mod.trigger = trigger > 0 ? 1.0f : 0.0f;
    if (trigger > 0) --trigger;
    voice.Render(patch, mod, frames, n);
    for (int i = 0; i < n; ++i) {
      float out = frames[i].out / 32768.0f, aux = frames[i].aux / 32768.0f;
      l[i] += gain * (out * (1 - pan) + 0.35f * aux * pan);
      r[i] += gain * (out * pan + 0.35f * aux * (1 - pan));
    }
  }
};
