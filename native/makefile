# Builds the tombola audio core from the vendored DSP (MIT, see ../NOTICE).
V        = vendor
CXXFLAGS = -O2 -fPIC -DTEST -include cstdio -msse2 -Wno-unused-variable -Wno-unused-parameter -I$(V)/eurorack -I$(V)
SOURCES  = tombola_core.cc mods.cc warp.cc scatter.cc \
           $(V)/eurorack/rings/dsp/fm_voice.cc $(V)/eurorack/rings/dsp/part.cc \
           $(V)/eurorack/rings/dsp/resonator.cc $(V)/eurorack/rings/dsp/string.cc \
           $(V)/eurorack/rings/dsp/string_synth_part.cc $(V)/eurorack/rings/resources.cc \
           $(V)/eurorack/clouds/dsp/correlator.cc $(V)/eurorack/clouds/dsp/granular_processor.cc \
           $(V)/eurorack/clouds/dsp/mu_law.cc $(V)/eurorack/clouds/dsp/pvoc/frame_transformation.cc \
           $(V)/eurorack/clouds/dsp/pvoc/phase_vocoder.cc $(V)/eurorack/clouds/dsp/pvoc/stft.cc \
           $(V)/eurorack/clouds/resources.cc \
           $(V)/stmlib/dsp/atan.cc $(V)/stmlib/dsp/units.cc $(V)/stmlib/utils/random.cc \
           $(V)/eurorack/plaits/dsp/fm/algorithms.cc \
           $(V)/eurorack/plaits/dsp/engine/additive_engine.cc \
           $(V)/eurorack/plaits/dsp/engine/bass_drum_engine.cc \
           $(V)/eurorack/plaits/dsp/engine2/chiptune_engine.cc \
           $(V)/eurorack/plaits/dsp/chords/chord_bank.cc \
           $(V)/eurorack/plaits/dsp/engine/chord_engine.cc \
           $(V)/eurorack/plaits/dsp/engine/fm_engine.cc \
           $(V)/eurorack/plaits/dsp/engine/grain_engine.cc \
           $(V)/eurorack/plaits/dsp/engine/hi_hat_engine.cc \
           $(V)/eurorack/plaits/dsp/speech/lpc_speech_synth.cc \
           $(V)/eurorack/plaits/dsp/speech/lpc_speech_synth_controller.cc \
           $(V)/eurorack/plaits/dsp/speech/lpc_speech_synth_phonemes.cc \
           $(V)/eurorack/plaits/dsp/speech/lpc_speech_synth_words.cc \
           $(V)/eurorack/plaits/dsp/engine/modal_engine.cc \
           $(V)/eurorack/plaits/dsp/physical_modelling/modal_voice.cc \
           $(V)/eurorack/plaits/dsp/speech/naive_speech_synth.cc \
           $(V)/eurorack/plaits/dsp/engine/noise_engine.cc \
           $(V)/eurorack/plaits/dsp/engine/particle_engine.cc \
           $(V)/eurorack/plaits/dsp/engine2/phase_distortion_engine.cc \
           $(V)/eurorack/plaits/dsp/physical_modelling/resonator.cc \
           $(V)/eurorack/plaits/resources.cc \
           $(V)/eurorack/plaits/dsp/speech/sam_speech_synth.cc \
           $(V)/eurorack/plaits/dsp/engine2/six_op_engine.cc \
           $(V)/eurorack/plaits/dsp/engine/snare_drum_engine.cc \
           $(V)/eurorack/plaits/dsp/engine/speech_engine.cc \
           $(V)/eurorack/plaits/dsp/physical_modelling/string.cc \
           $(V)/eurorack/plaits/dsp/engine/string_engine.cc \
           $(V)/eurorack/plaits/dsp/engine2/string_machine_engine.cc \
           $(V)/eurorack/plaits/dsp/physical_modelling/string_voice.cc \
           $(V)/eurorack/plaits/dsp/engine/swarm_engine.cc \
           $(V)/eurorack/plaits/dsp/engine/virtual_analog_engine.cc \
           $(V)/eurorack/plaits/dsp/engine2/virtual_analog_vcf_engine.cc \
           $(V)/eurorack/plaits/dsp/voice.cc \
           $(V)/eurorack/plaits/dsp/engine/waveshaping_engine.cc \
           $(V)/eurorack/plaits/dsp/engine/wavetable_engine.cc \
           $(V)/eurorack/plaits/dsp/engine2/wave_terrain_engine.cc \
           $(V)/eurorack/elements/dsp/exciter.cc \
           $(V)/eurorack/elements/dsp/multistage_envelope.cc \
           $(V)/eurorack/elements/dsp/ominous_voice.cc \
           $(V)/eurorack/elements/dsp/part.cc \
           $(V)/eurorack/elements/dsp/resonator.cc \
           $(V)/eurorack/elements/dsp/tube.cc \
           $(V)/eurorack/elements/dsp/voice.cc \
           $(V)/eurorack/plaits/dsp/fm/dx_units.cc \
           $(V)/eurorack/elements/dsp/string.cc \
           $(V)/eurorack/elements/resources.cc \
           $(V)/eurorack/marbles/resources.cc \
           $(V)/eurorack/tides2/poly_slope_generator.cc $(V)/eurorack/tides2/resources.cc \
           $(V)/eurorack/warps/dsp/filter_bank.cc $(V)/eurorack/warps/dsp/modulator.cc \
           $(V)/eurorack/warps/dsp/oscillator.cc $(V)/eurorack/warps/dsp/vocoder.cc \
           $(V)/eurorack/warps/resources.cc \
           $(V)/eurorack/beads/dsp/delay_clock.cc $(V)/eurorack/beads/dsp/delay_controller.cc \
           $(V)/eurorack/beads/dsp/mu_law.cc $(V)/eurorack/beads/dsp/processor.cc \
           $(V)/eurorack/beads/dsp/trigger_generator.cc $(V)/eurorack/beads/resources.cc
HEADERS  = synth_voice.h mods.h drums.h warp.h scatter.h
TARGET   = ../tombola/libtombola_core.so

$(TARGET): $(SOURCES) $(HEADERS) makefile
	$(CXX) -shared $(CXXFLAGS) $(SOURCES) -o $@

clean:
	rm -f $(TARGET)

.PHONY: clean
