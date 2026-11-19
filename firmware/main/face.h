// Part of Chitti Junior by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
#pragma once
#include <stdbool.h>

typedef enum {
    MOOD_NEUTRAL,
    MOOD_HAPPY,
    MOOD_EXCITED,
    MOOD_CALM,
    MOOD_THINKING,
    MOOD_SURPRISED,
    MOOD_SAD,
    MOOD_SLEEPY,
    MOOD_PLAYFUL,
    MOOD_ANGRY,
    MOOD_LAUGHING,
    MOOD_DANCING,
    MOOD_DRIVING,
    MOOD_COUNT
} mood_t;

typedef enum { ACT_IDLE, ACT_LISTENING, ACT_THINKING, ACT_SPEAKING } activity_t;

void face_init(void);                 // starts the render task (core 1)
void face_set_mood(mood_t m);
mood_t face_mood_from_name(const char *name);
void face_set_activity(activity_t a);
void face_set_text(const char *line1, const char *line2);  // NULL, NULL clears
void face_look_at(float x, float y);  // -1..1, e.g. from a touch
void face_poke(void);                 // quick squash-and-stretch reaction
void face_set_dim(bool dim);           // asleep: drives away, then the screen goes dark
void face_set_rest(bool rest);         // idle a while: dimmer, body holds still (less heat)
void face_set_night(bool night);       // late night: everything dimmer
void face_set_battery(int percent, bool charging);  // -1 = no battery
void face_set_subtitle(const char *text);  // small English subtitle under Chitti; NULL clears
void face_set_touch_seen(void);            // Junior: the screen has touch, so say "tap my face"
void face_stress_screen(int ms);            // sound test: full-screen redraws for ms
