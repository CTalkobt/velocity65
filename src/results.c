/* results.c — what runs once at a race's start and once in a while at its
 * end: what the race pays (over the picture at the countdown), the
 * finish (the prize, the circuits opened, the records) and the results
 * over the picture, place by place
 *
 * A code overlay (game.h): brought into bank 0 when called.
 */
#pragma cc45 overlay(results)
#pragma cc45 noescape_strings          /* (the literals kept with the code) */

#include <mega65.h>
#include "game.h"
#include "render.h"
#include "track.h"
#include "video.h"
#include "circuits.h"

#define RES_TOP  4                   /* the results' first text row */
#define RES_ROWS 13

unsigned char worth_shown, results_up, new_rec;

static void put_str(char *buf, const char *t) {
    while (*t) *buf++ = *t++;
}

/* v right-aligned in digits */
static void put_num(char *buf, unsigned int v, unsigned char digits) {
    while (digits--) {
        buf[digits] = (char)(0x30 + (unsigned char)(v % 10));
        v /= 10;
        if (!v) break;
    }
}

static void blank(char *line) {
    unsigned char i;
    for (i = 0; i < 40; i++) line[i] = ' ';
    line[40] = 0;
}

/* frames (50 Hz) as m:ss.cc; 0: none */
static void put_time(char *buf, unsigned int t) {
    unsigned int s = t / 50;
    if (!t) {
        put_str(buf, s"-:--.--");
        return;
    }
    buf[0] = (char)('0' + s / 60 % 10);
    buf[1] = ':';
    buf[2] = (char)('0' + s % 60 / 10);
    buf[3] = (char)('0' + s % 10);
    buf[4] = '.';
    buf[5] = (char)('0' + (t % 50) * 2 / 10);
    buf[6] = (char)('0' + (t % 50) * 2 % 10);
}

/* the countdown: what the race pays, over the top of the picture */
void race_worth(void) {
    /* this race pays   1st 1500  2nd 1000
     * 3rd 700  4th 500  5th 350  6th 250
     * 7th 150  8th 100  jump +150 ramp +250 (the places' columns by row) */
    static const unsigned char __noescape col[NCARS] = { 18, 28, 1, 10, 19, 28, 1, 10 };
    char line[41];
    unsigned char r, i;
    for (r = 0; r < 3; r++) {
        blank(line);
        if (r == 0) put_str(line + 1, s"this race pays");
        if (r == 2) put_str(line + 19, s"jump +150 ramp +250");
        for (i = r == 0 ? 0 : r == 1 ? 2 : 6; i < (r == 0 ? 2 : r == 1 ? 6 : 8); i++) {
            put_str(line + col[i], i == 0 ? s"1st" : i == 1 ? s"2nd" : i == 2 ? s"3rd" : s"4th");
            line[col[i]] = (char)('1' + i);
            put_num(line + col[i] + 3, prize_at(i), 5);
        }
        video_text(r, line, r == 0 ? 7 : 1);
    }
    worth_shown = 1;
}

/* the player over the line: the prize (more further along the route),
 * the circuits it opens (the top 3), the race's record */
void race_finished(void) {
#ifndef DEMO
    unsigned int *r = &save.race_rec[circ_now];
#endif
    earned += prize_at(place - 1);
    save.credits += earned;
    opened = 0;
    if (place <= 3 && !((save.cleared >> circ_now) & 1)) {
        save.cleared |= (unsigned char)(1 << circ_now);
        opened = 1;
    }
    new_rec = 0;
#ifndef DEMO
    if (!*r || race_t < *r) {
        new_rec = *r != 0;
        *r = race_t;
    }
#endif
    saved = 0;
    state = S_FINISH;
    state_t = 0;
    banner = SPR_BFINISH + 1;
    banner_t = 150;
    car_lap[0] = LAPS;
    nitro_t = 0;
}

/* the drivers (car 0 is the player) and the cars */
static const char *const __noescape driver[NCARS] = {
    s"you", s"ava kline", s"bo larsen", s"cy duval", s"dex marlo", s"eli sato", s"fay ortiz", s"gus berg" };
static const char *const __noescape model[4] = { s"gt", s"hatch", s"roadster", s"wedge" };

/* the results: the order over the line, the others (still racing) by when
 * they will be over it at their speed; what the player won; the records */
void results_show(void) {
    char line[41];
    unsigned char ord[NCARS], k, i;
    unsigned long when[NCARS];
    unsigned long fin_at = (unsigned long)LAPS * lap_z + ((unsigned long)START_SEG << 16);
    for (k = 0; k < NCARS; k++) {
        if (car_done[k]) when[k] = car_fin[k];          /* (before any still racing) */
        else {
            unsigned long p = progress(k), left = p < fin_at ? fin_at - p : 0;
            unsigned int v = car_v[k] > 0x400 ? car_v[k] : 0x400;
            when[k] = 0x10000UL + race_t + left / v;
        }
        /* in order */
        for (i = k; i && when[ord[i - 1]] > when[k]; i--) ord[i] = ord[i - 1];
        ord[i] = k;
    }
    blank(line);
    put_str(line + 1, s"result:");
    put_str(line + 9, circ_name[circ_now]);
    line[31] = (char)('0' + LAPS);
    put_str(line + 33, LAPS == 1 ? s"lap" : s"laps");
    video_text(RES_TOP, line, 7);
    blank(line);
    put_str(line + 1, s"pos driver      car       time");
    video_text(RES_TOP + 1, line, 15);
    for (i = 0; i < NCARS; i++) {
        k = ord[i];
        blank(line);
        line[1] = (char)('1' + i);
        put_str(line + 4, driver[k]);
        put_str(line + 16, model[car_livery[k] >> 1]);
        if (car_done[k]) put_time(line + 26, car_ft[k]);
        else {
            line[25] = '~';
            put_time(line + 26, (unsigned int)(when[k] - 0x10000UL));
        }
        if (!k) {
            line[34] = '+';
            put_num(line + 35, prize_at(i), 4);
        }
        video_text(RES_TOP + 2 + i, line, k ? 1 : 7);
    }
    blank(line);
    video_text(RES_TOP + 10, line, 1);
    {
        unsigned int prize = prize_at(place - 1);
        put_str(line + 1, s"prize");
        put_num(line + 7, prize, 5);
        put_str(line + 14, s"bonus");
        put_num(line + 20, earned - prize, 5);
        put_str(line + 27, s"total");
        put_num(line + 33, earned, 5);
        video_text(RES_TOP + 11, line, 5);
    }
    blank(line);
    put_str(line + 1, s"record");
    put_time(line + 8, save.race_rec[circ_now]);
    if (new_rec) put_str(line + 16, s"new!");
    put_str(line + 22, s"best lap");
    put_time(line + 31, save.lap_rec[circ_now]);
    video_text(RES_TOP + 12, line, new_rec ? 7 : 13);
    results_up = 1;
}

/* a race begins: the car (less grip in the rain), its nitro */
void race_setup(void) {
    apply_car();
    if (weather == W_RAIN) p_grip -= p_grip >> 2;
    nitro_n = save.nitro;
    nitro_t = boost_t = bog_t = slip = 0;
    lights = 0;
}

/* the HUD row on the title and at the countdown (racex.c: when it changes) */
void title_hud(void) {
    hud_clear();
    if (cheat) hud_text(1, s"cheat: every circuit open  fire: map", 5);
    else hud_text(0, s" fire: map  g: garage  m: music  c: cam", 7);
    hud_mode = S_TITLE;
}

void count_hud(void) {
    hud_clear();
    hud_text(1, s"go when the lights go out - not before!", 7);
    hud_mode = S_COUNT;
}

void results_hide(void) {
    text_rows_off(RES_TOP, RES_TOP + RES_ROWS);
    results_up = 0;
}
