/* sound.c — engine, tyres and effects on the SIDs
 *
 * The same notes go to the left and the right SID ($D400 and $D460: SIDs 1
 * and 4; the music has SIDs 2 and 3).
 * Voice 1 is the engine: a pulse wave whose pitch follows the rpm, a
 * little wider under load, with a slow wobble. Voice 2 is noise: tyres
 * skidding, the gravel off the road, crashes. Voice 3 beeps (countdown).
 */
#include "sound.h"
#include "video.h"

#define SID_L 0xD400
#define SID_R 0xD460

static unsigned char crash_t, bump_t, beep_t, wob;
unsigned int snd_f;

static void sid(unsigned char reg, unsigned char v) {
    REG(SID_L + reg) = v;
    REG(SID_R + reg) = v;
}

void sound_on(void) {
    unsigned char r;
    for (r = 0; r < 25; r++) sid(r, 0);
    sid(0x05, 0x08);          /* engine: fast attack, sustain full */
    sid(0x06, 0xF4);
    sid(0x0C, 0x05);          /* noise */
    sid(0x0D, 0xA6);
    sid(0x13, 0x00);          /* beeps */
    sid(0x14, 0xA0);
    sid(0x15, 0x00);
    sid(0x16, 0x30);          /* filter: low pass on the noise, softer */
    sid(0x17, 0x02);
    sid(0x18, 0x1F);          /* low pass, full volume */
}

void sound_off(void) {
    unsigned char r;
    for (r = 0; r < 25; r++) sid(r, 0);
}

void sound_engine(unsigned char rpm, unsigned char load, unsigned char skid) {
    unsigned int f;
    unsigned char pw;
    if (!rpm) {
        sid(0x04, 0x40);
    } else {
        wob += 23;
        snd_f = f = 900 + (unsigned int)rpm * 13 + (wob & 0x1F);
        sid(0x00, (unsigned char)f);
        sid(0x01, (unsigned char)(f >> 8));
        pw = (unsigned char)(0x40 + (load >> 2));
        sid(0x02, 0x00);
        sid(0x03, pw >> 4);
        sid(0x04, 0x41);
    }
    /* the noise: a crash, a bump, a skid, or quiet */
    if (crash_t) {
        crash_t--;
        sid(0x07, 0x00);
        sid(0x08, (unsigned char)(8 + crash_t));
        sid(0x0B, 0x81);
        sid(0x16, (unsigned char)(0x20 + crash_t * 4));
    } else if (bump_t) {
        bump_t--;
        sid(0x07, 0x00);
        sid(0x08, 0x06);
        sid(0x0B, 0x81);
        sid(0x16, 0x40);
    } else if (skid) {
        sid(0x07, 0x00);
        sid(0x08, (unsigned char)(0x30 + (skid >> 3)));
        sid(0x0B, 0x81);
        sid(0x16, (unsigned char)(0x10 + (skid >> 2)));
    } else {
        sid(0x0B, 0x80);
    }
    if (beep_t && --beep_t == 0) sid(0x12, 0x10);
}

void sound_crash(void) { crash_t = 24; }
void sound_bump(void) { if (!crash_t) bump_t = 6; }

void sound_beep(unsigned char high) {
    unsigned int f = high ? 0x3A00 : 0x1D00;
    sid(0x0E, (unsigned char)f);
    sid(0x0F, (unsigned char)(f >> 8));
    sid(0x12, 0x11);
    beep_t = high ? 30 : 12;
}
