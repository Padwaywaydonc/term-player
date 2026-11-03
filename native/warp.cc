// See warp.h. Its own translation unit: the vendored headers' table macros stay apart.
#include "warp.h"

#include <algorithm>
#include <cmath>
#include <cstdint>

#include "warps/dsp/modulator.h"

const float kWarpAlgoMax = 0.95f;       // at 1.0 the vocoder's release freezes: silence
const float kMatchMax = 8.0f;          // level matcher range: the algorithms differ by up to ~40 dB

struct Warp::Impl {
  warps::Modulator modulator;
  float prev_c = 0, prev_m = 0, lp = 0;
  float dry_power = 0, wet_power = 0;  // slow (~0.3 s) level followers for the matcher
};

// The filter-bank tables are designed for the module's 96 kHz: run it there, 2x.
void Warp::init() { impl = new Impl(); impl->modulator.Init(96000.0f); }

Warp::~Warp() { delete impl; }

static int16_t to_short(float x) {
  return int16_t(std::max(-32767.0f, std::min(32767.0f, x * 32767.0f)));
}

void Warp::render(int carrier_kind, const float* carrier, const float* modulator_in, float* out, int n,
                  float algo, float timbre, float drive, float note) {
  warps::Parameters* p = impl->modulator.mutable_parameters();
  algo = std::max(0.0f, std::min(kWarpAlgoMax, algo));
  p->channel_drive[0] = drive;
  p->channel_drive[1] = drive;
  p->modulation_algorithm = algo;
  p->modulation_parameter = std::max(0.0f, std::min(1.0f, timbre));
  p->carrier_shape = carrier_kind >= 2 ? std::min(3, carrier_kind - 1) : 0;
  p->note = note;
  p->frequency_shift_pot = 0.5f; p->frequency_shift_cv = 0.0f; p->phase_shift = 0.0f;
  warps::ShortFrame in[96], wout[96];
  bool internal = p->carrier_shape != 0;
  for (int i = 0; i < n; ++i) {        // 48k -> 96k: linear interpolation
    float c = internal ? 0.0f : carrier[i], m = modulator_in[i];
    in[2 * i].l = to_short(0.5f * (impl->prev_c + c)); in[2 * i].r = to_short(0.5f * (impl->prev_m + m));
    in[2 * i + 1].l = to_short(c);               in[2 * i + 1].r = to_short(m);
    impl->prev_c = c; impl->prev_m = m;
  }
  impl->modulator.Process(in, wout, 2 * n);
  float dry = 0, wet = 0;
  for (int i = 0; i < n; ++i) {        // 96k -> 48k: average the pair after a one-pole low-pass
    impl->lp += 0.5f * (wout[2 * i].l - impl->lp);
    float a = impl->lp;
    impl->lp += 0.5f * (wout[2 * i + 1].l - impl->lp);
    out[i] = 0.5f * (a + impl->lp) / 32768.0f;
    float d = (internal ? 0.0f : carrier[i]) + modulator_in[i];
    dry += d * d; wet += out[i] * out[i];
  }
  // Level match: DRIVE and ALGO change the character, not the loudness (they swing it ~40 dB).
  impl->dry_power += 0.07f * (dry / n - impl->dry_power);
  impl->wet_power += 0.07f * (wet / n - impl->wet_power);
  float gain = std::sqrt((impl->dry_power + 1e-9f) / (impl->wet_power + 1e-9f));
  gain = std::max(1.0f / kMatchMax, std::min(kMatchMax, gain));
  for (int i = 0; i < n; ++i) out[i] = std::tanh(gain * out[i]);
}
