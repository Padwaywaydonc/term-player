// See scatter.h. Its own translation unit: the vendored headers' table macros stay apart.
#include "scatter.h"

#include <algorithm>

#include "beads/dsp/processor.h"

const int kScatterBlock = 12;          // the module's own block: 48 kHz / 4 kHz
const float kScatterFeedbackMax = 0.8f;
const float kScatterDrive = 3.0f;      // feed the buffer near full scale, as the cloud modes do
const float kScatterOut = 1.6f;        // measured: wet level in line with the cloud modes

struct Scatter::Impl {
  beads::Processor processor;
  uint8_t banks[3][256 * 1024];        // three 256 KB memory banks, as on the module
  uint16_t reverb_buffer[32768];
};

void Scatter::init() {
  impl = new Impl();
  uint8_t* buffers[3] = {impl->banks[0], impl->banks[1], impl->banks[2]};
  impl->processor.Init(buffers, impl->reverb_buffer);
  impl->processor.Configure(beads::Processor::MEDIA_DIGITAL, false);
}

Scatter::~Scatter() { delete impl; }

static beads::RandomizedParameter knob(float base, float random) {
  beads::RandomizedParameter p;
  p.base = base; p.attenuverter = random; p.cv = 0; p.has_cv = false;
  return p;
}

void Scatter::render(const float* in_l, const float* in_r, float* out_l, float* out_r, int n,
                     float density, float size, float position, float texture, float pitch,
                     float spread, float feedback, bool freeze, float wash) {
  beads::Parameters p;
  p.time = knob(position, 0.5f * spread);
  p.size = knob(std::min(0.95f, size), 0.4f * spread);    // 0.98+: switches to delay mode
  p.shape = knob(texture, 0.0f);
  p.pitch = knob(pitch, 0.25f * spread);
  p.monophonic_mode = false;
  beads::ModulatedParameter d;
  d.base = density < 0 ? 0.48f * (1.0f + density) : (density > 0 ? 0.52f + 0.48f * density : 0.5f);
  d.cv = 0;
  beads::Processor::MixParameters m;
  m.feedback = kScatterFeedbackMax * feedback;
  m.dry_wet = 1.0f;
  m.reverb_amount = wash;
  m.output_gate = false;
  bool freezes[kScatterBlock];
  std::fill(freezes, freezes + kScatterBlock, freeze);
  for (int b = 0; b < n; b += kScatterBlock) {
    beads::FloatFrame in[kScatterBlock], out[kScatterBlock];
    for (int i = 0; i < kScatterBlock; ++i) { in[i].l = kScatterDrive * in_l[b + i]; in[i].r = kScatterDrive * in_r[b + i]; }
    impl->processor.Process(beads::SEED_BUTTON_STATE_LOCKED, nullptr, freezes, d, p, m, in, out, kScatterBlock);
    for (int i = 0; i < kScatterBlock; ++i) { out_l[b + i] += kScatterOut * out[i].l; out_r[b + i] += kScatterOut * out[i].r; }
  }
}
