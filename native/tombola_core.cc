// tombola audio core: the 48 kHz stereo mix for play --tombola.
//
// Ball strikes go through the resonator and the grain layer through the
// granular processor from Emilie Gillet's open-source eurorack code (MIT,
// vendored under native/vendor, see NOTICE). This file is the glue: a C API
// for Python (ctypes), the ring-chord voices, played-back note buffers, the
// 48k <-> 32k resampling around the granular processor and the master reverb.
//
// Threading: Python calls tc_set_params / tc_strike / tc_play / tc_set_rings
// from its main thread and tc_render from its audio thread. Those calls only
// touch a mutex-guarded staging area; tc_render applies it at block start.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <mutex>
#include <vector>
#include <pmmintrin.h>
#include <xmmintrin.h>

#include "clouds/dsp/granular_processor.h"   // declares the frame types reverb.h needs
#include "clouds/dsp/fx/reverb.h"
#include "rings/dsp/part.h"

namespace {

const int kRate = 48000;
const int kChunk = 48;                 // 48 frames @48k = 2 resonator blocks = 32 frames @32k
const int kResonatorBlock = 24;
const int kCloudsBlock = 32;
const int kTable = 2048;
const int kMaxVoices = 24;
const float kResonatorGain = 4.0f;     // the resonator's output sits well below full scale
const float kCloudsDrive = 3.0f;       // like the module's input gain: feed the buffer properly
const float kFeedbackMax = 0.42f;      // measured: from ~0.49 dense clouds sustain forever

struct Params {                        // mirrors TcParams in tombola/native.py
  float volume, reverb, ring_level;
  int ring_timbre;
  int res_model, res_poly;
  float res_structure, res_brightness, res_damping, res_position;
  float grain_level, grain_density, grain_size, grain_position, grain_pitch;
  float grain_spread, grain_texture, grain_feedback;
  int grain_freeze, grain_source, grain_mode;
};

// Sustained ring-chord timbres: (ratio, amplitude) harmonic sets.
const float kTimbres[5][8][2] = {
  {{1, 1.0f}, {2, 0.45f}, {3, 0.2f}},                                             // WARM
  {{1, 1.0f}, {2, 0.2f}},                                                          // GLASS
  {{1, 1.0f}},                                                                     // SINE
  {{1, 1.0f}, {2, 0.6f}, {3, 0.4f}, {4, 0.25f}, {6, 0.15f}},                       // ORGAN
  {{1, 1.0f}, {2, 0.5f}, {3, 0.333f}, {4, 0.25f}, {5, 0.2f}, {6, 0.167f},
   {7, 0.143f}, {8, 0.125f}},                                                      // SAW
};

struct Table { std::vector<float> data; };

// One cycle of a timbre with only the harmonics below max_ratio (no aliasing).
const float* timbre_table(int timbre, int max_ratio) {
  static Table tables[5][9];
  max_ratio = std::max(1, std::min(8, max_ratio));
  Table& t = tables[timbre][max_ratio];
  if (t.data.empty()) {
    t.data.assign(kTable, 0.0f);
    float peak = 0.0f;
    for (int i = 0; i < kTable; ++i) {
      float x = 0.0f;
      for (int h = 0; h < 8; ++h) {
        float r = kTimbres[timbre][h][0], a = kTimbres[timbre][h][1];
        if (r <= 0 || r > max_ratio) continue;
        x += a * std::sin(2.0f * float(M_PI) * r * i / kTable);
      }
      t.data[i] = x;
      peak = std::max(peak, std::fabs(x));
    }
    for (float& x : t.data) x /= peak;
  }
  return t.data.data();
}

struct RingVoice {                     // one chord note: two detuned table oscillators
  float freq = 0, target = 0, p1 = 0, p2 = 0, gain = 0, goal = 0;
  void render(float* l, float* r, int n, int timbre, float scale, float pan) {
    freq += (target - freq) * 0.15f;                              // ~glide on chord changes
    const float* t = timbre_table(timbre, int(0.45f * kRate / std::max(freq, 1.0f)));
    float s1 = kTable * freq * 0.997f / kRate, s2 = kTable * freq * 1.003f / kRate;
    float g1 = gain + std::max(-0.02f, std::min(0.02f, goal - gain));   // ramped: no zipper noise
    if (gain > 0 || g1 > 0) {
      for (int i = 0; i < n; ++i) {
        float g = scale * (gain + (g1 - gain) * i / n);
        float a = t[int(p1) & (kTable - 1)], b = t[int(p2) & (kTable - 1)];
        p1 += s1; p2 += s2;
        l[i] += g * (a * (1 - pan) + b * pan);
        r[i] += g * (a * pan + b * (1 - pan));
      }
    } else {
      p1 += s1 * n; p2 += s2 * n;
    }
    p1 = std::fmod(p1, float(kTable)); p2 = std::fmod(p2, float(kTable));
    gain = g1;
  }
};

struct Sample {                        // a played-back note buffer (other ball instruments)
  std::vector<float> data;
  size_t pos = 0;
  float gain = 1;
  int group = -1;
  int poly = 0;
  int fade = -1;                       // >= 0: choked, samples left of the fade-out
};

struct Strike { float note, strength; };

float midi_to_freq(float m) { return 440.0f * std::pow(2.0f, (m - 69.0f) / 12.0f); }

struct Engine {
  std::mutex lock;
  // --- staging (written under lock by the Python main thread) ---
  Params staged{};
  std::deque<Strike> strikes;
  std::deque<Sample> incoming;
  float ring_midis[4]{}, ring_gains[4]{};
  int ring_count = 0;
  // --- audio state (audio thread only) ---
  Params p{};
  rings::Part part;
  uint16_t part_reverb[65536];
  float impulse = 0;
  RingVoice ring[4];
  int rings_live = 0;
  std::vector<Sample> samples;
  clouds::GranularProcessor clouds;
  uint8_t clouds_large[118784];
  uint8_t clouds_small[65536 - 128];
  clouds::Reverb reverb;
  uint16_t reverb_buffer[16384];
  float down_pos = 0, up_pos = 0;      // resampler phases
  float down_prev_l = 0, down_prev_r = 0, lp_l = 0, lp_r = 0;
  float up_prev_l = 0, up_prev_r = 0;
  std::vector<float> c_in_l, c_in_r, c_out_l, c_out_r;
  long grains = 0;                     // grain estimate for the visual sparks
  float grain_clock = 0;

  void init() {
    part.Init(part_reverb);
    clouds.Init(clouds_large, sizeof(clouds_large), clouds_small, sizeof(clouds_small));
    clouds.set_num_channels(2);
    clouds.set_low_fidelity(false);
    clouds.set_playback_mode(clouds::PLAYBACK_MODE_GRANULAR);
    clouds.Prepare();
    reverb.Init(reverb_buffer);
    reverb.set_diffusion(0.7f);
    reverb.set_lp(0.7f);
    reverb.set_time(0.75f);
    reverb.set_input_gain(0.2f);
  }

  void apply_staging() {
    std::lock_guard<std::mutex> g(lock);
    p = staged;
    for (int k = 0; k < 4; ++k) {
      if (k < ring_count) {
        if (ring[k].freq == 0) ring[k].freq = midi_to_freq(ring_midis[k]);
        ring[k].target = midi_to_freq(ring_midis[k]);
        ring[k].goal = ring_gains[k];
      } else {
        ring[k].goal = 0;
      }
    }
    rings_live = 4;
    while (!incoming.empty()) {
      Sample s = std::move(incoming.front());
      incoming.pop_front();
      if (s.group >= 0 && s.poly > 0) {                        // choke the oldest beyond poly
        int ringing = 0;
        for (auto& o : samples) ringing += (o.group == s.group && o.fade < 0);
        for (auto& o : samples) {
          if (ringing < s.poly) break;
          if (o.group == s.group && o.fade < 0) { o.fade = 256; --ringing; }
        }
      }
      if (int(samples.size()) >= kMaxVoices) samples.erase(samples.begin());
      samples.push_back(std::move(s));
    }
  }

  // Resonator: one strike per 24-sample block at most (0.5 ms granularity).
  void render_resonator(float* l, float* r) {
    part.set_model(static_cast<rings::ResonatorModel>(
        std::max(0, std::min(int(rings::RESONATOR_MODEL_LAST) - 1, p.res_model))));
    int poly = std::max(1, std::min(4, p.res_poly));
    if (poly != part.polyphony()) part.set_polyphony(poly);   // it reconfigures the voices: only on change
    rings::Patch patch;
    patch.structure = p.res_structure;
    patch.brightness = p.res_brightness;
    patch.damping = p.res_damping;
    patch.position = p.res_position;
    for (int b = 0; b < kChunk; b += kResonatorBlock) {
      rings::PerformanceState ps;
      std::memset(&ps, 0, sizeof ps);
      ps.internal_exciter = false;
      {
        std::lock_guard<std::mutex> g(lock);
        if (!strikes.empty()) {
          Strike s = strikes.front();
          strikes.pop_front();
          ps.strum = true;
          ps.note = s.note;
          impulse = s.strength;
          last_note = s.note;
        } else {
          ps.note = last_note;
        }
      }
      float in[kResonatorBlock], out[kResonatorBlock], aux[kResonatorBlock];
      for (int j = 0; j < kResonatorBlock; ++j) { in[j] = impulse; impulse *= 0.99f; }
      part.Process(ps, patch, in, out, aux, kResonatorBlock);
      // With polyphony, odd and even voices come out on separate outputs: blend them so a
      // single strike sounds on both sides while the voices still spread across the field.
      for (int j = 0; j < kResonatorBlock; ++j) {
        l[b + j] += kResonatorGain * (0.7f * out[j] + 0.3f * aux[j]);
        r[b + j] += kResonatorGain * (0.3f * out[j] + 0.7f * aux[j]);
      }
    }
  }
  float last_note = 60;

  void render_samples(float* l, float* r) {
    for (auto& s : samples) {
      for (int i = 0; i < kChunk && s.pos < s.data.size(); ++i, ++s.pos) {
        float g = s.gain;
        if (s.fade >= 0) { g *= std::max(0.0f, s.fade / 256.0f); s.fade = std::max(0, s.fade - 1); }
        float x = s.data[s.pos] * g;
        l[i] += x; r[i] += x;
      }
    }
    samples.erase(std::remove_if(samples.begin(), samples.end(), [](const Sample& s) {
      return s.pos >= s.data.size() || s.fade == 0;
    }), samples.end());
  }

  // 48k stereo source -> granular processor at 32 kHz -> back to 48k.
  void render_clouds(const float* sl, const float* sr, float* l, float* r) {
    auto* cp = clouds.mutable_parameters();
    cp->position = p.grain_position;
    cp->size = p.grain_size;
    cp->pitch = p.grain_pitch;
    cp->density = 0.5f + 0.5f * p.grain_density;          // <0.5 steady, 0.5 none, >0.5 random
    cp->texture = p.grain_texture;
    cp->dry_wet = 1.0f;                                    // wet only; dry stays in our mix
    cp->stereo_spread = p.grain_spread;
    cp->feedback = kFeedbackMax * p.grain_feedback;
    cp->reverb = 0.0f;                                     // the master reverb handles space
    cp->freeze = p.grain_freeze != 0;
    cp->trigger = false;
    cp->gate = false;
    clouds.set_playback_mode(static_cast<clouds::PlaybackMode>(
        std::max(0, std::min(int(clouds::PLAYBACK_MODE_LAST) - 1, p.grain_mode))));

    // Downsample 48 -> 32 kHz (one-pole anti-alias, linear interpolation).
    clouds::ShortFrame in[kCloudsBlock], out[kCloudsBlock];
    int o = 0;
    for (int i = 0; i < kChunk && o < kCloudsBlock; ++i) {
      lp_l += 0.6f * (sl[i] - lp_l); lp_r += 0.6f * (sr[i] - lp_r);
      while (down_pos < 1.0f && o < kCloudsBlock) {
        float a = down_pos;
        float xl = down_prev_l + (lp_l - down_prev_l) * a, xr = down_prev_r + (lp_r - down_prev_r) * a;
        in[o].l = int16_t(std::max(-32767.0f, std::min(32767.0f, kCloudsDrive * xl * 32767.0f)));
        in[o].r = int16_t(std::max(-32767.0f, std::min(32767.0f, kCloudsDrive * xr * 32767.0f)));
        ++o;
        down_pos += 1.5f;
      }
      down_pos -= 1.0f;
      down_prev_l = lp_l; down_prev_r = lp_r;
    }
    for (; o < kCloudsBlock; ++o) in[o].l = in[o].r = 0;
    clouds.Process(in, out, kCloudsBlock);
    clouds.Prepare();

    // Upsample 32 -> 48 kHz (linear).
    int k = 0;
    for (int i = 0; i < kChunk; ++i) {
      float cl = out[std::min(k, kCloudsBlock - 1)].l / 32768.0f;
      float cr = out[std::min(k, kCloudsBlock - 1)].r / 32768.0f;
      l[i] += p.grain_level * (1.6f / kCloudsDrive) * (up_prev_l + (cl - up_prev_l) * up_pos);
      r[i] += p.grain_level * (1.6f / kCloudsDrive) * (up_prev_r + (cr - up_prev_r) * up_pos);
      up_pos += 2.0f / 3.0f;
      if (up_pos >= 1.0f) { up_pos -= 1.0f; up_prev_l = cl; up_prev_r = cr; ++k; }
    }
    float rate = std::fabs(p.grain_density) * 40.0f;          // visual sparks only
    if (p.grain_level > 0) { grain_clock += rate * kChunk / kRate; grains += long(grain_clock); grain_clock -= long(grain_clock); }
  }

  void render_chunk(int16_t* out) {
    apply_staging();
    float balls_l[kChunk]{}, balls_r[kChunk]{}, rings_l[kChunk]{}, rings_r[kChunk]{};
    render_resonator(balls_l, balls_r);
    render_samples(balls_l, balls_r);
    for (int k = 0; k < rings_live; ++k)
      ring[k].render(rings_l, rings_r, kChunk, std::max(0, std::min(4, p.ring_timbre)),
                     0.3f * p.ring_level, 0.2f + 0.2f * k);
    float src_l[kChunk], src_r[kChunk];
    for (int i = 0; i < kChunk; ++i) {
      src_l[i] = (p.grain_source != 2 ? balls_l[i] : 0) + (p.grain_source != 1 ? rings_l[i] : 0);
      src_r[i] = (p.grain_source != 2 ? balls_r[i] : 0) + (p.grain_source != 1 ? rings_r[i] : 0);
    }
    float mix_l[kChunk], mix_r[kChunk];
    for (int i = 0; i < kChunk; ++i) { mix_l[i] = balls_l[i] + rings_l[i]; mix_r[i] = balls_r[i] + rings_r[i]; }
    render_clouds(src_l, src_r, mix_l, mix_r);

    clouds::FloatFrame frames[kChunk];
    for (int i = 0; i < kChunk; ++i) { frames[i].l = mix_l[i]; frames[i].r = mix_r[i]; }
    reverb.set_amount(0.6f * p.reverb);
    reverb.Process(frames, kChunk);
    for (int i = 0; i < kChunk; ++i) {
      out[2 * i] = int16_t(32000.0f * std::tanh(p.volume * frames[i].l));
      out[2 * i + 1] = int16_t(32000.0f * std::tanh(p.volume * frames[i].r));
    }
  }
};

}  // namespace

extern "C" {

void* tc_create() {
  Engine* e = new Engine();
  e->init();
  return e;
}

void tc_destroy(void* h) { delete static_cast<Engine*>(h); }

void tc_set_params(void* h, const Params* params) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> g(e->lock);
  e->staged = *params;
}

void tc_strike(void* h, float note, float strength) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> g(e->lock);
  if (e->strikes.size() < 16) e->strikes.push_back({note, strength});
}

// Play a mono float buffer (48 kHz). group >= 0 with poly > 0: at most poly
// voices of that group ring; the oldest is choked with a short fade.
void tc_play(void* h, const float* data, int n, float gain, int group, int poly) {
  Engine* e = static_cast<Engine*>(h);
  Sample s;
  s.data.assign(data, data + n);
  s.gain = gain;
  s.group = group;
  s.poly = poly;
  std::lock_guard<std::mutex> g(e->lock);
  e->incoming.push_back(std::move(s));
}

void tc_set_rings(void* h, int count, const float* midis, const float* gains) {
  Engine* e = static_cast<Engine*>(h);
  std::lock_guard<std::mutex> g(e->lock);
  e->ring_count = std::max(0, std::min(4, count));
  for (int k = 0; k < e->ring_count; ++k) { e->ring_midis[k] = midis[k]; e->ring_gains[k] = gains[k]; }
}

// Render frames (multiple of 48) of interleaved stereo s16. Releases nothing
// itself; ctypes.CDLL drops the Python interpreter lock around this call.
int tc_render(void* h, int16_t* out, int frames) {
  // Flush denormals to zero, as the module's ARM chip does: decaying filters otherwise
  // produce tiny numbers that x86 handles very slowly. Per call: the setting is per thread.
  _MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON);
  _MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON);
  Engine* e = static_cast<Engine*>(h);
  int done = 0;
  for (; done + kChunk <= frames; done += kChunk) e->render_chunk(out + 2 * done);
  return done;
}

long tc_grains(void* h) { return static_cast<Engine*>(h)->grains; }

int tc_voices(void* h) { return int(static_cast<Engine*>(h)->samples.size()); }

}  // extern "C"
