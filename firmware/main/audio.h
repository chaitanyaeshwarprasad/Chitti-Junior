// Part of Chitti Junior by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define AUDIO_RATE 24000  // same as the voice the server sends (24 kHz neural TTS)

// Called from the mic task with 20 ms of mono PCM at AUDIO_RATE while capture is on.
typedef void (*audio_mic_cb_t)(const int16_t *pcm, size_t samples);

void audio_init(audio_mic_cb_t mic_cb);
void audio_capture(bool on);

// Speech playback: queue PCM, mark the end, poll for completion.
void audio_play_begin(void);
void audio_play_push(const uint8_t *pcm, size_t bytes);
void audio_play_end(void);     // no more data for this reply
void audio_play_stop(void);    // drop everything now
bool audio_playing(void);
uint32_t audio_underruns(void);  // times the speaker ran dry mid-reply

float audio_mic_level(void);   // 0..1, recent microphone loudness
float audio_out_level(void);   // 0..1, current speaker loudness (lip sync)
void audio_set_volume(int vol);
int audio_get_volume(void);
void audio_beep(int freq, int ms);

// sound tests
void audio_set_buffer_all(bool on);              // play replies only once fully received
void audio_test_tone(int f0_hz, int f1_hz, int ms);  // steady tone (f0 == f1) or sweep
