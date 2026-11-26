// See mods.h. Its own translation unit: the vendored headers' table macros stay apart.
#include "mods.h"

#include <algorithm>

#include "tides2/poly_slope_generator.h"

struct Mods::Impl {
  tides::PolySlopeGenerator generator;
  stmlib::GateFlags gates[1] = {stmlib::GATE_FLAG_LOW};
  int mode = -1;
};

void Mods::init() { impl = new Impl(); impl->generator.Init(); }

Mods::~Mods() { delete impl; }

void Mods::step(int output_mode, float rate_hz, float shape, float slope, float smooth, float shift,
                float ramp) {
  static const tides::OutputMode kModes[3] = {
      tides::OUTPUT_MODE_SLOPE_PHASE, tides::OUTPUT_MODE_FREQUENCY, tides::OUTPUT_MODE_AMPLITUDE};
  output_mode = std::max(0, std::min(2, output_mode));
  if (output_mode != impl->mode) {
    impl->generator.Reset();
    impl->mode = output_mode;
  }
  tides::PolySlopeGenerator::OutputSample sample;
  impl->generator.Render(tides::RAMP_MODE_LOOPING, kModes[output_mode], tides::RANGE_CONTROL,
                         rate_hz / kControlRate, slope, shape, smooth, shift, impl->gates,
                         ramp >= 0 ? &ramp : nullptr, &sample, 1);
  for (int k = 0; k < kModLanes; ++k) out[k] = std::max(-1.0f, std::min(1.0f, sample.channel[k] / 5.0f));
}
