// WARP: cross-modulation of a carrier with the balls (the vendored modulator).
//
// Carrier: the rings, the drums, or the modulator's own oscillator at the
// chord's root (SINE, TRI, SAW). Modulator: the balls. ALGO sweeps through
// crossfade, fold, analog and digital ring modulation, XOR, compare, spectral,
// morph and the vocoder; TIMBRE is the algorithm's parameter.
#pragma once

struct Warp {
  void init();
  ~Warp();
  // carrier_kind 0 rings, 1 drums (external carrier in), 2..4 internal sine/tri/saw.
  // Writes the mono result to out (n <= 48).
  void render(int carrier_kind, const float* carrier, const float* modulator_in, float* out, int n,
              float algo, float timbre, float drive, float note);
 private:
  struct Impl;
  Impl* impl = nullptr;
};
