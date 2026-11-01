/* racex.c — the race's code that runs every frame: the other cars' driving,
 * the collisions, the start lights and the start, the slipstream, the
 * nitro, the rain, the rear-view mirror, the banners and the HUD row
 *
 * A code overlay (game.h): brought into bank 0 when called. It is the
 * only overlay the title, the countdown, the race and its finish call
 * every frame (results.c only once in a while), so it stays in the region
 * while they run.
 */
#pragma cc45 overlay(racex)
#pragma cc45 noescape_strings          /* (the literals kept with the code) */

#include <mega65.h>
#include <input.h>
#include "game.h"
#include "render.h"
#include "sound.h"
#include "track.h"
#include "video.h"

unsigned char at_fork;               /* feature_drive chose a branch's line */
unsigned char cheat;                 /* every circuit open (typed on the title) */

/* the AI (and the autopilot) at a feature: at a fork odd cars take the
 * ramp, even ones the bridge; fast enough for any jump. Returns the speed
 * wanted (0: no matter) */
#define DECK_X    ((signed int)((((long)DECK_LO + DECK_HI) / 2) * SPLIT_W * RW / (256L * 64)))
static unsigned int feature_drive(unsigned char k) {
    unsigned int seg = seg_of(car_z[k]);
    unsigned char z = feature_of(next_seg(seg, 30)), here = feature_of(seg);
    unsigned char jumper = (k & 1) || k == 0;
    /* a fork: the lane on the branch's side first (clear of the island's
     * nose), then the ramp's line or the bridge's */
    at_fork = 1;
    if (here && feat_kind[here - 1] == FEAT_FORK && seg >= feat_from[here - 1] + 6)
        car_tx[k] = jumper ? RAMP_X : DECK_X;
    else if (z && feat_kind[z - 1] == FEAT_FORK)
        car_tx[k] = jumper ? LANE : -LANE;
    else at_fork = 0;
    if (!z) z = here;
    if (!z) return 0;
    if (feat_kind[z - 1] == FEAT_FORK && !jumper) return 0;
    return 205U << 8;
}

/* the computer's driving, for the opponents and the attract mode: keep to
 * the lane, change lanes round a slower car ahead */
static void ai_lane(unsigned char k) {
    unsigned char j;
    for (j = 0; j < NCARS; j++) {
        signed long d;
        signed int dx;
        if (j == k) continue;
        d = ahead(car_z[k], car_z[j]);
        if (d <= 0 || d > (signed long)(4UL * SEG << 8)) continue;
        dx = car_tx[k] - car_x[j];
        if (dx > -CAR_HIT_X && dx < CAR_HIT_X && car_v[j] < car_v[k]) {
            /* blocked: the other lane, or slow down */
            signed int to = car_x[j] > 0 ? car_x[j] - LANE : car_x[j] + LANE;
            if (to < -LANE) to = -LANE;
            if (to > LANE) to = LANE;
            car_tx[k] = to;
            if (d < (signed long)(2UL * SEG << 8) && car_v[k] > car_v[j]) car_v[k] = car_v[j];
        }
    }
    if (rnd() == 7) car_tx[k] = (signed int)(rnd() % 3) * LANE - LANE;
}

/* the rubber band (segments of lead over the player): ahead of the player
 * by more than RB_SLACK a car eases off, more the further ahead, and from
 * RB_HOLD on it goes no faster than the player (a crashed player isn't left
 * behind; from RB_STOP, out of sight, at 3/4 of the player's average speed,
 * which falls while the player is stopped); behind by more than RB_SLACK it pushes harder, more the further
 * back: faster on the straights, nearer the limit in the curves, quicker
 * to pick up speed */
#define RB_SLACK  12
#define RB_HOLD   90
#define RB_STOP   150                /*   and from here 3/4 of its average speed */
#define RB_EASE   48                 /* top speed lost per segment ahead (1/256 units) */
#define RB_PUSH   22                 /*   gained per segment behind, per level + 1 */
#define RB_PUSH_MAX(l) ((14U + 8U * (l)) << 8)   /*   at most (normal: +28 km/h) */

static unsigned long avg_acc;        /* the player's speed, averaged (* 256) */

/* the drivers' ways: how far below the limit each takes the curves (some
 * have less of a lead foot), and how often each gets a hard curve wrong -
 * runs wide and lifts for a second (in 8192ths a step in one). Easier
 * levels make them more careful and more often wrong, harder ones less */
static const unsigned char __noescape ai_care[NCARS] = { 0, 0, 4, 1, 7, 2, 9, 5 };
static const unsigned char __noescape ai_slip[NCARS] = { 0, 2, 4, 3, 6, 3, 8, 5 };
#define ERR_T     55                 /* steps a mistake lasts */
static unsigned char car_err[NCARS];

void ai_tick(unsigned char k) {
    unsigned int target = car_vmax[k];
    unsigned char lv = save.level, care = lv == 0 ? ai_care[k] + 4 : lv == 1 ? ai_care[k] : ai_care[k] >> 1;
    unsigned char margin = 6 + care, accel = 90;
    signed int seg_lead;
    signed char c4 = curve_ahead(car_z[k], 4), c0 = curve_ahead(car_z[k], 0);
    {
        /* (until the player is over the line the laps begun don't compare:
         * the grid is behind it, the cars in front over it first) */
        signed long lead = (car_lap[0] ? (signed long)(progress(k) - progress(0)) : ahead(car_z[0], car_z[k])) >> 16;
        seg_lead = lead > 30000 ? 30000 : lead < -30000 ? -30000 : (signed int)lead;
    }
    if (state == S_RACE) {
        if (seg_lead > RB_STOP) {
            /* out of sight ahead: 3/4 of the player's average speed */
            unsigned int avg = (unsigned int)(avg_acc >> 8);
            target = (avg >> 1) + (avg >> 2);
        }
        else if (seg_lead > RB_HOLD) {
            /* far ahead: hold station on the player (at a crawl at least) */
            target = car_v[0] > (40U << 8) ? car_v[0] - (4U << 8) : 36U << 8;
        } else if (seg_lead > RB_SLACK) {
            unsigned int ease = (unsigned int)(seg_lead - RB_SLACK) * RB_EASE;
            target = target > ease + (60U << 8) ? target - ease : 60U << 8;
        } else if (seg_lead < -RB_SLACK) {
            unsigned int behind = (unsigned int)(-seg_lead - RB_SLACK), step = RB_PUSH * (lv + 1);
            unsigned int push = behind > RB_PUSH_MAX(lv) / step ? RB_PUSH_MAX(lv) : behind * step;
            target += push;
            if (target < push || target > 0xFE00) target = 0xFE00;
            margin = (behind > 30 ? 0 : 3) + (care >> 1);
            accel = 130;
        }
        /* off the line: each its own reaction to the lights */
        if (race_t < (unsigned int)((k * 5) % 23 + 4)) target = 0;
        /* now and then a hard curve gone wrong */
        if (!car_err[k] && (c0 > 8 || c0 < -8) && rnd() < (ai_slip[k] << (2 - lv)) && rnd() < 16)
            car_err[k] = ERR_T;
    }
    /* lift for the sharper curves ahead, a little below the player's limit
     * (at it, chasing a player well ahead) */
    {
        unsigned int lim = (unsigned int)(curve_limit(c4) - margin) << 8;
        if (target > lim) target = lim;
    }
    if (car_err[k]) {
        /* running wide (to the outside of the curve), lifting */
        car_err[k]--;
        target = car_v[k] - (car_v[k] >> 3);
        car_tx[k] = c0 > 0 ? -(RW - 150) : RW - 150;
    }
    if (car_done[k]) target = 120U << 8;
    {
        unsigned int fv = feature_drive(k);
        if (fv && target && target < fv) target = fv;
        if (car_air[k]) target = car_v[k];          /* (no grip up there) */
        if (car_v[k] < target) car_v[k] += accel;
        else if (car_v[k] > target + 200) car_v[k] -= 120;
        if (!car_zone[k] && !at_fork && !car_err[k]) {
            ai_lane(k);
            if (car_tx[k] > LANE) car_tx[k] = LANE;  /* (after a fork) */
            if (car_tx[k] < -LANE) car_tx[k] = -LANE;
        }
    }
    if (car_x[k] < car_tx[k] - 8) { car_x[k] += 8; car_steer[k] = 1; }
    else if (car_x[k] > car_tx[k] + 8) { car_x[k] -= 8; car_steer[k] = -1; }
    else car_steer[k] = 0;
}

/* the scenery, the tunnels' walls and the other cars the player runs into */
#define TUN_X     ((signed int)((long)RW * TUN_U256 / 256))

void collisions(void) {
    unsigned char k, side;
    unsigned int seg = seg_of(car_z[0]);
    unsigned int idx = seg >= cam_seg ? seg - cam_seg : seg + trk_n - cam_seg;
    signed int x = car_x[0];
    if (crash_cool) crash_cool--;
    else if (idx < NWIN && car_v[0] > (20U << 8))
        for (side = 0; side < 2; side++) {
            unsigned char id = side ? win_sprr[idx] : win_sprl[idx];
            signed int ox, dx, hw;
            if (!id) continue;
            id--;
            if (id == SPR_RAMP) continue;           /* (fly() deals with it) */
            if (id == SPR_GANTRY) {
                /* its posts */
                if (x < -(RW + 200) || x > RW + 200) goto crash;
                continue;
            }
            ox = (signed int)(side ? win_oxr[idx] : win_oxl[idx]) * 32 + scen_hx[id];
            dx = x - ox;
            hw = (signed int)scen_hw[id] + 140;
            if (dx > -hw && dx < hw) {
                if (id == SPR_BUSH) {
                    car_v[0] = car_v[0] * 3 / 4;
                    sound_bump();
                    crash_cool = 8;
                    continue;
                }
crash:
                car_v[0] = car_v[0] / 5;
                car_x[0] = x + (x > 0 ? -120 : 120);
                shake = 16;
                crash_cool = 30;
                sound_crash();
                return;
            }
        }
    /* a tunnel's walls: scraped along, slower */
    if (var_at(seg) >= V_TUNNEL && (x > TUN_X - 160 || x < -(TUN_X - 160))) {
        car_x[0] = x > 0 ? TUN_X - 190 : -(TUN_X - 190);
        car_v[0] -= car_v[0] >> 4;
        steer_v = 0;
        shake = 4;
        sound_bump();
    }
    for (k = 1; k < NCARS; k++) {
        signed long d = ahead(car_z[0], car_z[k]);
        signed int dx = car_x[k] - x;
        if (d > -(signed long)CAR_HIT_Z && d < (signed long)CAR_HIT_Z && dx > -CAR_HIT_X && dx < CAR_HIT_X) {
            if (d > 0) {
                /* into its back */
                if (car_v[0] > car_v[k]) {
                    car_v[0] = car_v[k] - (car_v[k] >> 3);
                    car_z[0] -= 0x2000;
                    sound_bump();
                    shake = 6;
                }
            } else if (car_v[k] > car_v[0]) {
                car_v[k] = car_v[0] - (car_v[0] >> 3);
                car_z[k] -= 0x2000;
            }
            /* and apart: the other car moves over (its lane away from the
             * player), the player is nudged the other way */
            if (dx > 0) {
                car_x[0] -= 10;
                car_x[k] += 10;
                car_tx[k] = car_x[k] < 0 ? 0 : LANE;
            } else {
                car_x[0] += 10;
                car_x[k] -= 10;
                car_tx[k] = car_x[k] > 0 ? 0 : -LANE;
            }
        }
    }
}

/* the attract mode and the finished car: the computer drives */
unsigned char autopilot(void) {
    unsigned char c = C_GAS, lim;
    signed char curve = curve_ahead(car_z[0], 6);
    signed int want;
    unsigned int fv = feature_drive(0);
    if (!car_zone[0] && !at_fork) ai_lane(0);
    want = car_tx[0] + (car_zone[0] ? 0 : (signed int)curve * 24);
    if (car_x[0] < want - 30) c |= C_RIGHT;
    if (car_x[0] > want + 30) c |= C_LEFT;
    /* lift (or brake) for the curves ahead: the braking takes a while */
    lim = curve_limit(curve_ahead(car_z[0], 4));
    {
        unsigned char l2 = curve_limit(curve_ahead(car_z[0], 14)), l3 = curve_limit(curve_ahead(car_z[0], 28));
        if (l2 < lim) lim = l2;
        if (l3 < lim) lim = l3;
    }
    if (fv && lim < (unsigned char)(fv >> 8)) lim = (unsigned char)(fv >> 8);
    if ((unsigned char)(car_v[0] >> 8) > lim) c = (unsigned char)((c & ~C_GAS) | ((car_v[0] >> 8) > lim + 12 ? C_BRAKE : 0));
    return c;
}

/* ------------------------------------------------------------------------ */
/* messages in the HUD row, for a while */

static const char *msg;
static unsigned char msg_c, msg_end, msg_new;

void hud_msg(const char *t, unsigned char c, unsigned char frames) {
    msg = t;
    msg_c = c;
    msg_end = (unsigned char)(FRAMECOUNT + frames);
    msg_new = 1;
}

/* ------------------------------------------------------------------------ */
/* the start: five red lights come on, a second apart, then after a moment
 * (not always the same) they go out: go. The gas pressed while all five
 * are lit is a jump start, and the car bogs down; pressed just as they go
 * out, a great start, with a boost */
#define LIGHT_T0   60
#define LIGHT_DT   45
#define GREAT_T    12                /* frames after the lights go out */
#define GOOD_T     25

static unsigned int out_t;
static unsigned char jumped, go_wait;

static unsigned char rain_ok;

void count_tick(unsigned char c) {
    if (state_t == 1) {
        unsigned char k;
        lights = 0;
        jumped = go_wait = 0;
        msg = 0;
        rain_ok = 0;
        avg_acc = 0;
        for (k = 0; k < NCARS; k++) car_err[k] = 0;
        out_t = LIGHT_T0 + 4 * LIGHT_DT + 20 + (rnd() & 63);
    }
    if (state_t >= LIGHT_T0 && lights < 5 && (state_t - LIGHT_T0) % LIGHT_DT == 0) {
        lights++;
        sound_beep(0);
    }
    if (lights == 5 && (c & C_GAS)) jumped = 1;
    if (state_t >= out_t) {
        lights = 6;
        sound_beep(1);
        banner = SPR_BGO + 1;
        banner_t = 50;
        state = S_RACE;
        state_t = 0;
        car_lap[0] = 0;
        go_wait = !jumped;
        if (jumped) {
            bog_t = 90;
            hud_msg(s"jump start! wait for the lights to go out", 2, 120);
        }
    }
}

/* every step of the race: the start, the nitro, the slipstream */
void race_tick(unsigned char c) {
    unsigned char k;
    if (state == S_RACE) {
        if (go_wait && (c & C_GAS)) {
            go_wait = 0;
            if (state_t <= GREAT_T) {
                boost_t = 60;
                hud_msg(s"great start!", 7, 100);
            } else if (state_t <= GOOD_T) hud_msg(s"good start", 5, 80);
        }
        if (state_t > GOOD_T) go_wait = 0;
        if (nitro_req && nitro_n && !nitro_t && !car_done[0]) {
            nitro_n--;
            nitro_t = NITRO_T;
            sound_crash();
        }
        /* a car close ahead in the same lane, at speed */
        slip = 0;
        if (car_v[0] > (130U << 8) && !nitro_t)
            for (k = 1; k < NCARS; k++) {
                signed long d = ahead(car_z[0], car_z[k]);
                signed int dx = car_x[k] - car_x[0];
                if (d > (signed long)CAR_HIT_Z && d < (signed long)(10UL * SEG << 8) && dx > -240 && dx < 240) {
                    slip = 1;
                    break;
                }
            }
        avg_acc += car_v[0];
        avg_acc -= avg_acc >> 8;
    } else slip = 0;
    /* in the rain: the cars' reflections on the road, their spray once
     * they are racing */
    rd_wet = weather == W_RAIN ? (state == S_RACE || state == S_FINISH ? 3 : 1) : 0;
    nitro_req = 0;
    if (nitro_t) nitro_t--;
    if (boost_t) boost_t--;
    if (bog_t) bog_t--;
}


/* the player's lap (not the first): a record lap on this circuit? */
void lap_done(void) {
#ifndef DEMO
    unsigned int *r = &save.lap_rec[circ_now];
    if (!*r || lap_t < *r) {
        if (*r) hud_msg(s"new lap record!", 7, 120);
        *r = lap_t;
        saved = 0;
    }
#endif
}

/* ------------------------------------------------------------------------ */
/* the rain: streaks falling down the picture, blown sideways by the curves
 * and the steering (render.s45's rd_rain draws them) */

static void rain(void) {
    unsigned char k, bottom = cockpit ? 168 : SCR_H - 1;
    signed int wind = (signed int)(car_v[0] >> 11) * curve_ahead(car_z[0], 0) / 4 - steer_v / 48;
    if (!rain_ok) {
        for (k = 0; k < NDROP; k++) {
            rn_y[k] = (unsigned char)(rnd() % (unsigned char)(bottom - 16));
            rn_xl[k] = rnd();
            rn_xh[k] = rnd() & 1;
        }
        rain_ok = 1;
    }
    for (k = 0; k < NDROP; k++) {
        signed int x = (signed int)((unsigned int)rn_xh[k] << 8 | rn_xl[k]) + wind;
        unsigned char y = rn_y[k] + 9 + (k & 3) + (unsigned char)(car_v[0] >> 13);
        rn_len[k] = 6 + (k & 7);
        if (y + rn_len[k] > bottom || x < 0 || x >= SCR_W) {
            y = rnd() & 31;
            x = (signed int)rnd() + (rnd() & 63);
        }
        rn_y[k] = y;
        rn_xl[k] = (unsigned char)x;
        rn_xh[k] = (unsigned char)((unsigned int)x >> 8);
    }
    rn_n = NDROP;
    rd_rain();
}

/* ------------------------------------------------------------------------ */
/* the rear-view mirror (the cockpit view): the cars close behind, seen in
 * it from the front, smaller the further back */
#define MIR_X     (SCR_W / 2)
#define MIR_Y     22                 /* its bottom row */
#define MIR_HZ    9                  /* the horizon in it */

static void mirror(void) {
    unsigned char k;
    size_sprite(SPR_MIRROR, spr_tw[SPR_MIRROR]);
    sp_x = MIR_X;
    sp_y = MIR_Y;
    sp_clip = SCR_H - 1;
    rd_sprite();
    /* the cars within 14 segments behind (too small to need an order) */
    for (k = 1; k < NCARS; k++) {
        signed long d = ahead(car_z[k], car_z[0]);
        unsigned int u, w;
        signed int x, half;
        if (d < (signed long)(150UL << 8) || d > (signed long)(14UL * SEG << 8)) continue;
        u = (unsigned int)(d >> 8);
        w = 3600U / u + 2;
        if (w > 22) w = 22;
        half = 44 - (signed int)(w >> 1);
        x = (car_x[k] - car_x[0]) / (signed int)(u >> 3);
        if (x > half) x = half;
        if (x < -half) x = -half;
        size_sprite(SPR_FRONT_0 + car_livery[k], w);
        sp_x = MIR_X + x;
        sp_y = MIR_HZ + (signed int)(2400U / u);
        sp_clip = MIR_Y - 2;
        rd_sprite();
    }
}

/* ------------------------------------------------------------------------ */

/* the start lights over the road */
static void start_lights(void) {
    unsigned char i;
    size_sprite(SPR_LIGHTS, spr_tw[SPR_LIGHTS]);
    sp_x = SCR_W / 2;
    sp_y = 62;
    sp_clip = SCR_H - 1;
    rd_sprite();
    for (i = 0; i < 5; i++) {
        unsigned char id = lights > i && lights < 6 ? SPR_LAMP_ON : SPR_LAMP_OFF;
        size_sprite(id, spr_tw[id]);
        sp_x = SCR_W / 2 - 44 + i * 22;
        sp_y = 55;
        rd_sprite();
    }
}

/* a tunnel's echo: voice 3 (the countdown's beeps, which never sound in
 * one) a few frames behind the engine, a little flat and softer */
static unsigned int echo_f[8];
static unsigned char echo_i, echo_on;

static void echo_reg(unsigned char r, unsigned char v) {
    REG(0xD400 + r) = v;
    REG(0xD460 + r) = v;
}

static void echo(void) {
    if (sound_ok && !paused && var_at(seg_of(car_z[0])) >= V_TUNNEL) {
        unsigned int f;
        echo_f[echo_i++ & 7] = snd_f;
        f = echo_f[(unsigned char)(echo_i - 7) & 7] - 9;
        if (!echo_on) {
            echo_reg(0x13, 0x09);
            echo_reg(0x14, 0x74);
            echo_on = 1;
        }
        echo_reg(0x0E, (unsigned char)f);
        echo_reg(0x0F, (unsigned char)(f >> 8));
        echo_reg(0x10, 0);
        echo_reg(0x11, 0x06);
        echo_reg(0x12, 0x41);
    } else if (echo_on) {
        echo_reg(0x12, 0x40);
        echo_reg(0x13, 0x00);
        echo_reg(0x14, 0xA0);
        echo_on = 0;
    }
}

/* over the picture: the rain, the mirror, the lights, a banner, the title,
 * the pause; and the tunnels' echo */
void race_extras(void) {
    echo();
    if (weather == W_RAIN && var_at(seg_of(car_z[0])) < V_TUNNEL) rain();   /* (not in a tunnel) */
    if (cockpit && state != S_TITLE) mirror();
    if (state == S_COUNT) start_lights();
    sp_clip = SCR_H - 1;
    if (banner) {
        unsigned char id = banner - 1;
        size_sprite(id, spr_tw[id]);
        sp_x = SCR_W / 2;
        sp_y = 64 + (signed int)(sp_h / 2);
        rd_sprite();
    }
    if (state == S_TITLE) {
        size_sprite(SPR_BTITLE, spr_tw[SPR_BTITLE]);
        sp_x = SCR_W / 2;
        sp_y = 62;
        rd_sprite();
    }
    if (paused) {
        size_sprite(SPR_BPAUSE, spr_tw[SPR_BPAUSE]);
        sp_x = SCR_W / 2;
        sp_y = 100;
        rd_sprite();
    }
}

/* ------------------------------------------------------------------------ */
/* the HUD row */

static void hud_time(unsigned char col, unsigned int t, unsigned char c) {
    /* frames (50 Hz) as m:ss.cc */
    unsigned int s = t / 50;
    hud_num(col, s / 60 % 10, 1, c);
    hud_text(col + 1, s":", c);
    hud_num(col + 2, s % 60, 2, c);
    hud_text(col + 4, s".", c);
    hud_num(col + 5, (t % 50) * 2, 2, c);
}

static unsigned char rank(void) {
    unsigned char k, r = 1;
    unsigned long p = progress(0);
    for (k = 1; k < NCARS; k++)
        if (progress(k) > p) r++;
    return r;
}

#define HUD_MSG  250                 /* hud_mode: a message is up */
static unsigned int res_next;        /* when the results are shown again */

void race_hud(void) {
    unsigned char k;
#ifdef PROFILE
    {
        /* fps, the road, the sprites and the game logic in 0.1 ms (averages
         * over 16 frames); the car's x and its lane (+ 2000, / 10) */
        static unsigned int prof_road, prof_spr, prof_tick, prof_fps10;
        static unsigned char prof_n, prof_frames, prof_last;
        unsigned char now = FRAMECOUNT;
        prof_frames += (unsigned char)(now - prof_last);
        prof_last = now;
        if (++prof_n == 16) {
            prof_road = (unsigned int)(prof_sum_road / 16);
            prof_spr = (unsigned int)(prof_sum_spr / 16);
            prof_tick = (unsigned int)(prof_sum_tick / 16);
            prof_sum_tick = 0;
            prof_fps10 = prof_frames ? (unsigned int)(16U * 500U / prof_frames) : 0;
            prof_sum_road = prof_sum_spr = 0;
            prof_n = prof_frames = 0;
        }
        hud_text(0, s"f    r   s   t    x   l               ", 15);
        hud_num(1, prof_fps10, 3, 1);
        hud_num(6, prof_road, 3, 1);
        hud_num(10, prof_spr, 3, 1);
        hud_num(14, prof_tick, 3, 1);
        hud_num(19, (unsigned int)(car_x[0] + 2000) / 10, 3, 1);
        hud_num(23, (unsigned int)(car_tx[0] + 2000) / 10, 3, 1);
        hud_num(27, KMH(car_v[0]), 3, 7);
        return;
    }
#endif
    if (state == S_TITLE) {
        /* "cheat" typed: every circuit open on the map */
        static const unsigned char __noescape cheat_key[5] = { KEY_C, KEY_H, KEY_E, KEY_A, KEY_T };
        static unsigned char cheat_i, cheat_t;
        if (!cheat) {
            if (input_pressed(cheat_key[cheat_i])) {
                cheat_t = FRAMECOUNT;
                if (++cheat_i == 5) {
                    cheat = 1;
                    sound_beep(1);
                    hud_mode = 255;
                }
            } else if (cheat_i && (unsigned char)(FRAMECOUNT - cheat_t) > 150) cheat_i = 0;
        }
        if (hud_mode != S_TITLE) title_hud();
        return;
    }
    if (state == S_COUNT) {
        if (hud_mode != S_COUNT) count_hud();
        return;
    }
    if (state == S_FINISH && state_t > 150) {
        /* the results over the picture (again as the others finish), and
         * the way on */
        if (!results_up) res_next = 0;
        if (state_t >= res_next) {
            results_show();
            res_next = state_t + 50;
        }
        if (hud_mode != S_FINISH) {
            hud_clear();
            hud_text(0, s"finished", 7);
            hud_text(10, s"pos", 15);
            hud_num(14, place, 1, place == 1 ? 7 : 1);
            hud_text(17, s"+", 7);
            hud_num(18, earned, 5, 7);
            hud_text(23, s"cr", 15);
            hud_mode = S_FINISH;
        }
        hud_text(27, s"fire: the map", (FRAMECOUNT & 32) ? 7 : 15);
        return;
    }
    if (msg) {
        if ((signed char)(msg_end - FRAMECOUNT) <= 0) {
            msg = 0;
            hud_mode = 255;
        } else {
            if (msg_new || hud_mode != HUD_MSG) {
                hud_clear();
                hud_text(1, msg, msg_c);
                hud_mode = HUD_MSG;
                msg_new = 0;
            }
            return;
        }
    }
    /* lap1/3 p6/8 0:07.58 b0:27.45 n*** 222kmh */
    if (hud_mode != S_RACE) {
        hud_clear();
        hud_text(0, s"lap", 15);
        hud_text(4, s"/3", 15);
        hud_text(7, s"p", 15);
        hud_text(9, s"/8", 15);
        hud_text(20, s"b", 15);
        if (save.nitro) hud_text(29, s"n", 15);
        hud_text(37, s"kmh", 15);
        hud_mode = S_RACE;
    }
    hud_num(3, car_lap[0] ? car_lap[0] : 1, 1, 1);
    if (state != S_FINISH) place = rank();
    hud_num(8, place, 1, place == 1 ? 7 : 1);
    hud_time(12, state == S_FINISH ? car_ft[0] : lap_t, 1);
    if (best_t) hud_time(21, best_t, 13);
    else hud_text(21, s"-:--.--", 12);
    /* the nitro: a star per charge left, the one burning blinks */
    for (k = 0; k < save.nitro; k++) {
        unsigned char lit = k < nitro_n || (k == nitro_n && nitro_t && (FRAMECOUNT & 4));
        hud_text(30 + k, lit ? s"*" : s"-", lit ? 2 : 11);
    }
    /* the speed: cyan in a slipstream, red with nitro */
    hud_num(34, KMH(car_v[0]), 3, nitro_t ? 2 : slip ? 3 : 7);
}
