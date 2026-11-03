// MODS: four slow modulation lanes from the vendored poly slope generator.
//
// One generator, run at a 1 kHz control rate (one sample per 48-frame chunk),
// gives four related outputs: phase-shifted copies (PHASE), different rates
// (FREQ) or a shape panned across the outputs (AMP). Each lane adds
// depth * output to one sound setting, so the setting sweeps around its knob.
#pragma once

const int kModLanes = 4;
const float kControlRate = 1000.0f;    // 48 kHz / 48-frame chunks

struct Mods {
  float out[kModLanes]{};              // latest values, -1 .. 1
  void init();
  ~Mods();
  // rate_hz: speed; shape, slope, smooth, shift: 0..1 like the module's knobs.
  void step(int output_mode, float rate_hz, float shape, float slope, float smooth, float shift);
 private:
  struct Impl;
  Impl* impl = nullptr;
};
