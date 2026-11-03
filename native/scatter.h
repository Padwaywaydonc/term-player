// SCATTER: the GRAINS page's fifth mode, the vendored newer granular processor.
//
// Runs natively at 48 kHz (its clean digital media setting, no resampling).
// The GRAINS knobs map onto it: POSITION -> time, SIZE -> size, TEXTURE ->
// shape, PITCH -> pitch, SPREAD -> random variation of time, size and pitch,
// DENSITY -> grain rate (below 0 steady, 0 none, above 0 random), FEEDBACK,
// FREEZE. Wet only and without its own reverb, as with the other modes.
#pragma once

struct Scatter {
  void init();
  ~Scatter();
  // density: -1..1; size, position, texture, spread, feedback: 0..1; pitch in semitones.
  void render(const float* in_l, const float* in_r, float* out_l, float* out_r, int n,
              float density, float size, float position, float texture, float pitch,
              float spread, float feedback, bool freeze);
 private:
  struct Impl;
  Impl* impl = nullptr;
};
