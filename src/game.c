/* race — VELOCITY 65, a road racer for the MEGA65 (cc45)
 *
 * Eight cars, three laps of a circuit of hills and curves. The view is the
 * driver's (the bonnet in front) or a chase camera behind the car.
 *
 * Keys: cursor keys / W A S D, joystick in either port. Up, W, SPACE or
 *       fire: accelerate; down or S: brake; left / right: steer.
 *       C camera, P pause, M sound on / off, RUN/STOP quit.
 *
 * The world moves in fixed steps of one video frame (50 Hz); the picture
 * is drawn as often as it can be (render.s45 does the road and the sky,
 * this file places the sprites).
 *
 * Build with -DDEMO to drive by itself (screenshots, soak tests).
 *
 * cc45's plain char/int/long are unsigned: signed values say so.
 */
#include <dma.h>
#include <stdio.h>
#include <string.h>
#include <cbm.h>
#include <input.h>
#include <mega65.h>
#include <unpack.h>
#include <audio_sid.h>
#include <audio_tune.h>
#include "music.h"
#include "gen_config.h"
#include "render.h"
#include "sound.h"
#include "track.h"
#include "video.h"
#include "game.h"

#define MAX_DT     4                 /* longest step simulated at once */

/* speeds: world units per frame, 8.8 (VMAX: game.h) */
#define V_OFFROAD  (84U << 8)
#define BRAKE      300
#define DRAG       40
#define STEER      26                /* world units per frame at speed */
#define BONNET_COVERS 170             /* the bonnet hides every row from here */


/* the cars */
unsigned long car_z[NCARS];   /* along the lap, 1/256 world units */
signed int car_x[NCARS];      /* across the road, world units */
unsigned int car_v[NCARS];    /* 8.8 world units per frame */
unsigned int car_vmax[NCARS];
signed int car_tx[NCARS];     /* AI: the lane it heads for */
signed char car_steer[NCARS]; /* -1 / 0 / 1, for the sprite */
unsigned char car_lap[NCARS]; /* laps begun (1 .. LAPS, then done) */
unsigned char car_livery[NCARS]; /* model and colour (gen_assets.py LIVERIES) */
unsigned char car_tyre[NCARS];   /* tyre type (gen_assets.py TYRES) */
unsigned char car_done[NCARS];
unsigned char car_fin[NCARS];  /* finished: in this place, */
unsigned int car_ft[NCARS];    /*   at this race_t */
unsigned char fin_n;          /* cars finished */
unsigned long car_base[NCARS]; /* car_lap * lap_z, for the race order */
unsigned long fin_z;          /* (LAPS + 2) * lap_z: ahead of every racing car */
unsigned long lap_z;          /* a lap, 1/256 units */
/* in the air (near the jumps and forks only: elsewhere the cars are on the
 * road) */
signed int car_alt[NCARS];    /* the wheels' height (world units) */
static signed int car_vy[NCARS];     /* up, world units per frame */
unsigned char car_air[NCARS];
unsigned char car_zone[NCARS]; /* the feature it is at, + 1 */

/* the player */
unsigned char gear, shake;
unsigned char crash_cool, place;
signed int steer_v;           /* the steering, 1/8 world units per frame */
unsigned int race_t, lap_t, best_t, last_lap_t;
unsigned char cockpit = 1;
unsigned char sound_ok = 1;
unsigned char player_livery;  /* the player's car and tyres (to be won as */
unsigned char player_tyre;    /*   rewards) */

/* ------------------------------------------------------------------------ */
/* the cars to win: four models (each in two colours: livery = model * 2 +
 * colour) and two kinds of tyre, bought with credits won in races. The
 * player's top speed (world units a frame), pull (128: the GT's) and grip
 * (128: the GT on standard tyres; the curves' pull is divided by it), and
 * off the road the top speed a little higher on fat tyres */
const unsigned char model_top[NMODEL]  = { 232, 222, 228, 246 };
const unsigned char model_pull[NMODEL] = { 128, 158, 144, 150 };
const unsigned char model_grip[NMODEL] = { 128, 142, 160, 134 };
const unsigned char tyre_grip[NTYRE]   = { 128, 154 };
static const unsigned char tyre_off[NTYRE]    = { 0, 28 };
/* the rear wings (the player's: a layer over the body): more grip (128 =
 * none), a little top speed lost */
const unsigned char wing_grip[NWING]  = { 128, 134, 142, 150 };
const unsigned char wing_drag[NWING]  = { 0, 1, 3, 5 };
unsigned char player_wing;

/* prize money by place (game.h: prize_at; for a jump landed, for the
 * fork's ramp flown: JUMP_BONUS, RAMP_BONUS) */
const unsigned int prize[NCARS] = { 1500, 1000, 700, 500, 350, 250, 150, 100 };

/* the save record and a file name, below the program (game.h; variables
 * of their own there: a cast address's members are written wrongly by cc45) */
#pragma section("lowsave", 0x1E40)
__section("lowsave") struct save_rec save;
__section("lowsave") char name_buf[24];
unsigned int earned;          /* this race's winnings so far */
unsigned char saved = 1;      /* the credits on the disk are up to date */
unsigned int p_vmax;          /* the player's car: top speed (8.8), */
unsigned char p_pull, p_grip; /*   pull and grip (128 = 1) */
static unsigned char bonnet_done;    /* bit per buffer: its covered rows are drawn */
signed long bg_far, bg_near;  /* panorama scroll, 8.8 pixels */
signed int cam_x_s;           /* the chase camera's x (lags the car) */
static signed int cam_h_s;           /*   and its height */

/* the state of the game */
unsigned char state;
unsigned int state_t;
unsigned char banner, banner_t;   /* a sprite id + 1 shown over the road */
static unsigned char vsync = 1;
unsigned char paused;

/* the start, the slipstream, the nitro, the weather (racex.c) */
unsigned char slip, nitro_n, nitro_t, nitro_req, boost_t, bog_t, lights, weather;

/* controls (C_*: game.h) */
unsigned char ctl, ctl_last;
static unsigned char key_last;

/* ------------------------------------------------------------------------ */
/* ------------------------------------------------------------------------ */

static unsigned int rnd_state = 0xACE1;
unsigned char rnd(void) {
    rnd_state = rnd_state * 25173U + 13849U;
    return (unsigned char)(rnd_state >> 8);
}

unsigned int seg_of(unsigned long z) {
    return (unsigned int)(z >> 16);
}

/* signed distance from a to b along the lap (b ahead: positive) */
signed long ahead(unsigned long a, unsigned long b) {
    signed long d = (signed long)(b - a);
    if (d > (signed long)(lap_z / 2)) d -= (signed long)lap_z;
    if (d < -(signed long)(lap_z / 2)) d += (signed long)lap_z;
    return d;
}

unsigned int cam_seg;          /* the window's first segment */

/* a segment's curve: from the window around the camera when it is there
 * (most are), else from attic RAM */
static signed char curve_at(unsigned int seg) {
    unsigned int idx = seg >= cam_seg ? seg - cam_seg : seg + trk_n - cam_seg;
    if (idx < NWIN) return win_curve[idx];
    return track_curve(seg);
}

/* the curve n segments ahead of z */
signed char curve_ahead(unsigned long z, unsigned char n) {
    unsigned int seg = seg_of(z) + n;
    if (seg >= trk_n) seg -= trk_n;
    return curve_at(seg);
}

/* the fastest speed (world units per frame) at which a curve's pull
 * (player_tick: 3 * curve * v^2 / 65536) takes 80% of the steering */
static const unsigned char curve_vmax[25] = {
    232, 232, 232, 232, 232, 232, 232, 232, 232, 224, 213, 203, 194,
    186, 180, 174, 168, 163, 158, 154, 150, 147, 143, 140, 137 };

unsigned char curve_limit(signed char c) {
    unsigned char a = (unsigned char)(c < 0 ? -c : c), v = curve_vmax[a > 24 ? 24 : a];
    /* (in the rain the grip is 3/4: sqrt of it as fast) */
    return weather == W_RAIN && a > 6 ? v - (v >> 3) : v;
}

/* a segment's height, road variant, width: from the window when it is
 * there, else from attic RAM */
static unsigned int win_idx(unsigned int seg) {
    return seg >= cam_seg ? seg - cam_seg : seg + trk_n - cam_seg;
}

static signed int y_at(unsigned int seg) {
    unsigned int i = win_idx(seg);
    if (i < NWIN) return (signed int)((unsigned int)win_yhi[i] << 8 | win_ylo[i]);
    return track_y(seg);
}

unsigned char var_at(unsigned int seg) {
    unsigned int i = win_idx(seg);
    return i < NWIN ? win_var[i] : track_var(seg);
}

static unsigned char w_at(unsigned int seg) {
    unsigned int i = win_idx(seg);
    return i < NWIN ? win_w[i] : track_w(seg);
}

unsigned int next_seg(unsigned int seg, unsigned char n) {
    seg += n;
    return seg >= trk_n ? seg - trk_n : seg;
}

static signed int lerp16(signed int a, signed int b, unsigned char f) {
    return a + (signed int)(((signed long)(b - a) * (signed long)f) >> 8);
}

/* the road's height under z */
static signed int ground(unsigned long z) {
    unsigned int seg = seg_of(z);
    return lerp16(y_at(seg), y_at(next_seg(seg, 1)), (unsigned char)(z >> 8));
}

/* the jump or fork (+ 1) segment seg is in, 0: none */
unsigned char feature_of(unsigned int seg) {
    unsigned char i;
    for (i = 0; i < NFEAT; i++)
        if (seg >= feat_from[i] && seg <= feat_to[i]) return (unsigned char)(i + 1);
    return 0;
}

/* what is under x on segment seg */
#define ON_ROAD  0
#define ON_GRASS 1
#define ON_WATER 2                   /* (or the far bank's face) */
static unsigned char surface(unsigned int seg, signed int x) {
    unsigned char v = var_at(seg);
    signed long hw = ((signed long)RW * (signed long)w_at(seg)) >> 6;
    signed int u = (signed int)((signed long)x * 256L / hw);   /* 256: the edge */
    signed int au = u < 0 ? -u : u;
    if (v == V_WATER || v == V_BANK) return ON_WATER;
    if (v == V_BRIDGE || v == V_BRIDGE + 1) return u >= DECK_LO && u < DECK_HI ? ON_ROAD : ON_WATER;
    if ((v == V_SPLIT || v == V_SPLIT + 1) && au < ISLAND_U) return ON_GRASS;
    return au < 256 ? ON_ROAD : ON_GRASS;
}

#define GRAVITY   4                  /* world units per frame per frame */
#define RAMP_OK   100                /* off the ramp's middle by this: still a jump */
#define RAMP_HIT  (235 + 150)        /*   by this: the car clips its edge */
#define DECK_X    ((signed int)((((long)DECK_LO + DECK_HI) / 2) * SPLIT_W * RW / (256L * 64)))

/* into the water (or the bank): back on the road past it, slowly */
static void splash(unsigned char k) {
    unsigned int seg = seg_of(car_z[k]);
    unsigned char n;
    for (n = 0; n < 40 && surface(seg, car_x[k]) != ON_ROAD; n++) seg = next_seg(seg, 1);
    car_z[k] = ((unsigned long)seg << 16) + 0x4000;
    car_alt[k] = ground(car_z[k]);
    car_vy[k] = 0;
    car_air[k] = 0;
    car_v[k] = 50U << 8;
    if (k == 0) {
        shake = 30;
        crash_cool = 30;
        sound_crash();
    }
}

/* the fork's ramp: square on it, a jump; on its edge, a spin */
static void ramp(unsigned char k) {
    signed int dx = car_x[k] - RAMP_X;
    if (dx < 0) dx = -dx;
    if (dx < RAMP_OK) {
        car_air[k] = 1;
        car_vy[k] = (signed int)(car_v[k] >> 10) + 6;
        if (k == 0 && state == S_RACE) earned += RAMP_BONUS;
    } else if (dx < RAMP_HIT) {
        car_v[k] /= 3;
        if (k == 0) {
            shake = 20;
            sound_crash();
        }
    }
}

/* near a jump or fork: the car's height - on the road, or in the air when
 * the road falls away faster than it would fall (the end of a ramp) */
static void fly(unsigned char k) {
    unsigned int seg = seg_of(car_z[k]);
    unsigned char zone = feature_of(seg);
    signed int g;
    if (!zone && !car_air[k]) {
        car_zone[k] = 0;
        return;
    }
    g = ground(car_z[k]);
    if (!car_zone[k] && !car_air[k]) {   /* just arrived */
        car_alt[k] = g;
        car_vy[k] = 0;
    }
    car_zone[k] = zone;
    if (car_air[k]) {
        car_vy[k] -= GRAVITY;
        car_alt[k] += car_vy[k];
        if (car_alt[k] <= g) {
            /* down: on the road, or in the water, or into the bank */
            if (surface(seg, car_x[k]) == ON_WATER || g - car_alt[k] > 120) splash(k);
            else {
                car_alt[k] = g;
                car_vy[k] = 0;
                car_air[k] = 0;
                if (k == 0) {
                    if (state == S_RACE) earned += JUMP_BONUS;
                    shake = 8;
                    sound_bump();
                }
            }
        }
        return;
    }
    if (g - car_alt[k] < car_vy[k] - GRAVITY) {
        car_air[k] = 1;
        car_vy[k] -= GRAVITY;
        car_alt[k] += car_vy[k];
        return;
    }
    car_vy[k] = g - car_alt[k];
    car_alt[k] = g;
    if (surface(seg, car_x[k]) == ON_WATER) splash(k);
    else if (zone && feat_kind[zone - 1] == FEAT_FORK && seg == feat_ramp[zone - 1]) ramp(k);
}

/* the race order: laps begun, then the distance */
unsigned long progress(unsigned char k) {
    if (car_done[k]) return fin_z - car_fin[k];
    return car_base[k] + car_z[k];
}


/* ------------------------------------------------------------------------ */
/* controls: W A S D, the cursor keys, SPACE, a joystick in either port.
 * The MEGA65's cursor left and up keys show in the matrix as right SHIFT +
 * cursor right / down; $D60F tells them apart (bit 0 left, bit 1 up). */
#define CRSR_LEFTUP REG(0xD60F)

static unsigned char controls(void) {
    unsigned char c = 0, joy = input_joy(1) | input_joy(2), lu = CRSR_LEFTUP & 3;
    if (input_pressed(KEY_W) || (lu & 2) || (joy & JOY_UP) || input_pressed(KEY_SPACE)) c |= C_GAS;
    if (joy & JOY_FIRE) c |= C_GAS | C_FIRE;
    if (input_pressed(KEY_SPACE) || input_pressed(KEY_RETURN)) c |= C_FIRE;
    if (input_pressed(KEY_S) || (input_pressed(KEY_DOWN) && !(lu & 2)) || (joy & JOY_DOWN)) c |= C_BRAKE;
    if (input_pressed(KEY_A) || (lu & 1) || (joy & JOY_LEFT)) c |= C_LEFT;
    if (input_pressed(KEY_D) || (input_pressed(KEY_RIGHT) && !(lu & 1)) || (joy & JOY_RIGHT)) c |= C_RIGHT;
    return c;
}

unsigned char skid;

/* the gears: speeds where each ends, for the rpm and the pull */
static const unsigned char gear_top[6] = { 0, 62, 108, 150, 192, 233 };

static unsigned char rpm_of(unsigned int v) {
    unsigned char vi = (unsigned char)(v >> 8), lo, hi;
    while (gear < 5 && vi > gear_top[gear]) gear++;
    while (gear > 1 && vi + 10 < gear_top[gear - 1]) gear--;
    lo = gear > 1 ? gear_top[gear - 1] - 20 : 0;
    hi = gear_top[gear];
    if (vi < lo) vi = lo;
    return (unsigned char)(60 + (unsigned int)(vi - lo) * 195U / (unsigned int)(hi - lo));
}

/* one 50 Hz step of the player's car */
static void player_tick(unsigned char c) {
    unsigned int seg = seg_of(car_z[0]);
    signed char curve = curve_at(seg);
    unsigned int v = car_v[0];
    unsigned char vi = (unsigned char)(v >> 8), off;
    signed int x = car_x[0], pull, sc;

    if (state == S_COUNT || car_done[0] == 2) c = 0;
    if (car_air[0]) {
        /* in the air: no grip, a little steering */
        if (c & C_LEFT) x -= STEER / 4;
        if (c & C_RIGHT) x += STEER / 4;
        car_x[0] = x;
        skid = 0;
        return;
    }
    off = surface(seg, x) == ON_GRASS;
    if (c & C_GAS) {
        /* stronger in the low gears, nothing past the top; more in a
         * slipstream, with nitro or a great start, little after a jump start */
        unsigned int a = (unsigned int)(((150 - gear * 18) * (unsigned int)p_pull) >> 7), top = p_vmax;
        if (slip) top += SLIP_V;
        if (nitro_t) {
            top += NITRO_V;
            a <<= 1;
        }
        if (top < p_vmax) top = 0xFF00;        /* (past 255 units a frame: no further) */
        if (boost_t) a <<= 1;
        if (bog_t) a >>= 3;
        if (v < top) v += a;
        if (v > top) v = top;
    } else if (c & C_BRAKE) {
        v = v > BRAKE ? v - BRAKE : 0;
    } else {
        v = v > DRAG ? v - DRAG : 0;
    }
    if (off && v > V_OFFROAD + ((unsigned int)tyre_off[player_tyre] << 8)) {
        v -= 160;
        if ((FRAMECOUNT & 3) == 0) sound_bump();
        shake = 2;
    }
    /* steering: the wheel turns in over a few frames and back when let go
     * (steer_v, 1/8 world units per frame); less at a crawl */
    if (c & C_LEFT) {
        if (steer_v > -STEER * 8) steer_v -= steer_v > 0 ? 32 : 18;
    } else if (c & C_RIGHT) {
        if (steer_v < STEER * 8) steer_v += steer_v < 0 ? 32 : 18;
    } else if (steer_v > 24) steer_v -= 24;
    else if (steer_v < -24) steer_v += 24;
    else steer_v = 0;
    if (steer_v > STEER * 8) steer_v = STEER * 8;
    if (steer_v < -STEER * 8) steer_v = -STEER * 8;
    sc = steer_v / 8;
    /* (both operands signed long: cc45 divides signed long * unsigned char
     * as unsigned) */
    if (vi < 48) sc = (signed int)((signed long)sc * (signed long)vi / 48);
    x += sc;
    car_steer[0] = steer_v < -STEER * 3 ? -1 : steer_v > STEER * 3 ? 1 : 0;
    /* the curve pushes outwards */
    pull = (signed int)(((signed long)curve * (signed long)((unsigned int)vi * vi) * 3) >> 16);
    pull = (signed int)((signed long)pull * 128 / (signed long)p_grip);
    x -= pull;
    /* the tyres squeal near the limit: steering into a pull of most of
     * what the steering can give */
    skid = 0;
    {
        signed int p = pull < 0 ? -pull : pull;
        if (p > STEER / 2 && ((pull < 0 && (c & C_LEFT)) || (pull > 0 && (c & C_RIGHT))))
            skid = (unsigned char)(p > STEER + 8 ? 255 : (p - STEER / 2) * 10);
    }
    if (x < -(RW * 3)) x = -(RW * 3);
    if (x > RW * 3) x = RW * 3;

    car_v[0] = v;
    car_x[0] = x;
}

/* ------------------------------------------------------------------------ */

/* a place's prize on this circuit: half as much again per pair of
 * circuits along the route */
unsigned int prize_at(unsigned char i) {
    return (unsigned int)((unsigned long)prize[i] * (2 + CIRC_LAYER(circ_now)) / 2);
}

unsigned char opened;         /* the last race opened new circuits */

static void tick(void) {
    unsigned char k, c = ctl;
    state_t++;
    if (state == S_GARAGE) {
        car_steer[0] = (signed char)((state_t >> 6) % 3) - 1;
        return;
    }
    if (state == S_MAP) return;
    if (state == S_TITLE || car_done[0]) c = autopilot();
#ifdef DEMO
    c = autopilot();
#endif
    race_tick(c);
    if (state != S_COUNT) {
        player_tick(c);
        for (k = 1; k < NCARS; k++) ai_tick(k);
        for (k = 0; k < NCARS; k++) {
            car_z[k] += car_v[k];
            if (car_z[k] >= lap_z) car_z[k] -= lap_z;
            fly(k);
            /* over the line: a lap begins */
            if (seg_of(car_z[k]) == START_SEG && seg_of(car_z[k] - car_v[k]) == START_SEG - 1) {
                if (car_lap[k] < LAPS) {
                    car_lap[k]++;
                    car_base[k] += lap_z;
                }
                else if (!car_done[k]) {
                    car_done[k] = 1;
                    car_fin[k] = ++fin_n;
                    car_ft[k] = race_t;
                    if (k == 0) place = fin_n;
                }
                if (k == 0 && state == S_RACE) {
                    if (car_lap[0] > 1) {
                        last_lap_t = lap_t;
                        if (!best_t || lap_t < best_t) best_t = lap_t;
                        lap_done();
                    }
                    lap_t = 0;
                    if (car_done[0]) race_finished();
                    else if (car_lap[0] == LAPS) {
                        banner = SPR_BFINAL + 1;
                        banner_t = 120;
                    }
                }
            }
        }
        collisions();
    } else {
        player_tick(0);
    }
    if (state == S_RACE || state == S_FINISH) race_t++;   /* (the others finishing) */
    if (state == S_RACE) lap_t++;
    if (state == S_COUNT)
#ifdef DEMO
        count_tick(0);
#else
        count_tick(ctl);
#endif
    if (banner_t && --banner_t == 0) banner = 0;
    if (shake) shake--;
    /* the scenery scrolls with the curves */
    {
        signed char curve = curve_at(seg_of(car_z[0]));
        signed long d = (signed long)curve * (signed long)(car_v[0] >> 8);
        bg_far += d >> 3;
        bg_near += d >> 2;
    }
}

/* ------------------------------------------------------------------------ */
/* the picture */

/* a car's sprites: the body by its livery and the view (its steering, and
 * where it is relative to the camera: off to the right, its left flank
 * shows); the axle by its tyres, the view and the tread - stepping with the
 * distance, every frame at speed */
static unsigned char car_axle;
static unsigned char car_wing;       /* the player's wing layer (0: none) */

static unsigned char car_sprite(unsigned char k, signed int rel) {
    signed char v = car_steer[k];
    unsigned char wf;
    if (rel > 450) v++;
    if (rel < -450) v--;
    if (v > 1) v = 1;
    if (v < -1) v = -1;
    wf = car_v[k] < (60U << 8) ? (unsigned char)(car_z[k] >> 12) & 1 : (unsigned char)(FRAMECOUNT + k) & 1;
    car_axle = (unsigned char)(SPR_AXLE_0_L0 + car_tyre[k] * 6 + (v + 1) * 2 + wf);
    car_wing = player_wing ? (unsigned char)(SPR_WING_1_L + (player_wing - 1) * 3 + (v + 1)) : 0;
    if (k) return (unsigned char)(SPR_CARW_1_L + (car_livery[k] - 1) * 3 + (v + 1));
    return (unsigned char)(SPR_CAR_0_L + car_livery[k] * 3 + (v + 1));
}

/* the cars in view, farthest first, for rd_sprites */
static void find_cars(unsigned long cam_z) {
    unsigned char k, a;
    unsigned int lim = (unsigned int)(e_n - 2) << 8, pos;
    unsigned int vis_pos[NCARS];
    vc_n = 0;
    for (k = 1; k < NCARS; k++) {           /* (the chase view draws car 0 apart) */
        unsigned long d = car_z[k] >= cam_z ? car_z[k] - cam_z : car_z[k] + lap_z - cam_z;
        if (d >= ((unsigned long)lim << 8)) continue;
        pos = (unsigned int)(d >> 8) + cam_zf;
        if (pos < 2 * SEG || pos >= lim) continue;
        /* insert, farthest first */
        a = vc_n++;
        while (a && vis_pos[a - 1] < pos) {
            vis_pos[a] = vis_pos[a - 1];
            vc_id[a] = vc_id[a - 1];
            vc_axle[a] = vc_axle[a - 1];
            vc_xl[a] = vc_xl[a - 1];
            vc_xh[a] = vc_xh[a - 1];
            vc_yl[a] = vc_yl[a - 1];
            vc_yh[a] = vc_yh[a - 1];
            a--;
        }
        vis_pos[a] = pos;
        vc_id[a] = car_sprite(k, car_x[k] - cam_x);
        vc_axle[a] = car_axle;
        vc_xl[a] = (unsigned char)car_x[k];
        vc_xh[a] = (unsigned char)((unsigned int)car_x[k] >> 8);
        {
            /* above the road: in the air */
            signed int h = car_zone[k] || car_air[k] ? car_alt[k] - ground(car_z[k]) : 0;
            if (h < 0) h = 0;
            vc_yl[a] = (unsigned char)h;
            vc_yh[a] = (unsigned char)((unsigned int)h >> 8);
        }
    }
    for (a = 0; a < vc_n; a++) {
        vc_pos_l[a] = (unsigned char)vis_pos[a];
        vc_pos_h[a] = (unsigned char)(vis_pos[a] >> 8);
    }
}

/* sp_w, then the height from the texture's shape */
void size_sprite(unsigned char id, unsigned int w) {
    sp_id = id;
    sp_w = w;
    sp_h = (unsigned int)((unsigned long)w * spr_th[id] / spr_tw[id]);
}

#ifdef PROFILE
/* phase times: CIA 2 timer A underflows every 100 us, timer B counts them
 * down; averages over 16 frames in 0.1 ms; frames per second x 10 */
static unsigned int prof_t0;
unsigned long prof_sum_road, prof_sum_spr, prof_sum_tick;   /* (racex.c shows them) */
static unsigned int prof_now(void) {
    unsigned char lo = REG(0xDD06), hi = REG(0xDD07);
    return (unsigned int)hi << 8 | lo;
}
static void prof_start(void) {
    REG(0xDD04) = 99;                 /* timer A: 100 phi2 cycles */
    REG(0xDD05) = 0;
    REG(0xDD06) = 0xFF;               /* timer B: counts timer A underflows */
    REG(0xDD07) = 0xFF;
    REG(0xDD0F) = 0x51;
    REG(0xDD0E) = 0x11;
}
#define PROF(x) x
#else
#define PROF(x)
#endif

/* the player's car, bw wide, its wheels' bottom at x, by: the axle, then
 * the body over it (the same size and place), and the wing on its top */
void draw_player(unsigned int bw, signed int x, signed int by, unsigned char clip) {
    unsigned char body = car_sprite(0, 0), d, wet = state == S_GARAGE ? 0 : rd_wet;
    unsigned int bh = 0;
    sp_clip = clip;
    if (wet) {
        /* in the rain: its reflection on the road, under it */
        size_sprite(SPR_REFL, bw);
        sp_x = x;
        sp_y = by + (signed int)sp_h;
        rd_sprite();
    }
    for (d = 0; d < 3; d++) {
        if (d == 2 && !car_wing) break;
        size_sprite(d == 0 ? car_axle : d == 1 ? body : car_wing, bw);
        sp_x = x;
        sp_y = by;
        if (d == 1) bh = sp_h;
        if (d == 2) sp_y = by - (signed int)((unsigned long)bh * (CAR_TH - wing_row[player_livery >> 1]) / CAR_TH);
        sp_clip = clip;
        rd_sprite();
    }
    if (wet & 2) {
        /* and the spray it throws up */
        size_sprite(SPR_SPRAY, bw + (bw >> 2));
        sp_x = x;
        sp_y = by;
        rd_sprite();
    }
    if (nitro_t) {
        /* the nitro's flames from both exhausts */
        size_sprite(SPR_FLAME_0 + (FRAMECOUNT & 1), bw >> 2);
        sp_y = by - (signed int)(bh >> 3);
        for (d = 0; d < 2; d++) {
            sp_x = x + (d ? (signed int)(bw >> 2) : -(signed int)(bw >> 2));
            rd_sprite();
        }
    }
}

/* the picture in front of the camera */
static unsigned long cam_zabs;

static void render(void) {
    unsigned long cz;
    unsigned int cseg;
    signed int y0, y1, by;
    signed char curve;
    unsigned char bump = 0;
    if (shake) bump = (unsigned char)(FRAMECOUNT & 1) + (shake > 8 ? 1 : 0);
    if (state == S_GARAGE) {
        garage_scene();
        return;
    }
    if (state == S_MAP) {
        map_scene();
        return;
    }

    /* the camera */
    cz = car_z[0];
    if (!cockpit) cz = cz >= ((unsigned long)CHASE_BACK << 8) ? cz - ((unsigned long)CHASE_BACK << 8)
                                                            : cz + lap_z - ((unsigned long)CHASE_BACK << 8);
    cam_zabs = cz;
    cseg = seg_of(cz);
    cam_seg = cseg;
    cam_zf = (unsigned int)(cz >> 8) & 0xFF;
    track_window(cseg);
    curve = win_curve[0];
    cam_dx0 = -(((signed long)curve * (signed long)cam_zf) >> 8);
    y0 = (signed int)((unsigned int)win_yhi[0] << 8 | win_ylo[0]);
    y1 = (signed int)((unsigned int)win_yhi[1] << 8 | win_ylo[1]);
    by = lerp16(y0, y1, (unsigned char)cam_zf);
    if (cockpit) {
        cam_x = car_x[0];
        cam_y = (car_zone[0] || car_air[0] ? car_alt[0] : by) + CAMH_COCKPIT + bump * 4;
    } else {
        cam_x_s += (car_x[0] - cam_x_s) >> 2;
        cam_x = cam_x_s;
        /* the chase camera's height follows the car's with a lag: the car
         * leaps up the screen off a ramp, and comes down again */
        {
            signed int want = (car_zone[0] || car_air[0] ? car_alt[0] : by) + CAMH_CHASE;
            cam_h_s += (want - cam_h_s) / 6;
            if (want - cam_h_s > 600 || cam_h_s - want > 600) cam_h_s = want;
            cam_y = cam_h_s + bump * 4;
        }
    }
    pano_far_off = (unsigned int)(bg_far >> 8) & (PANO_W - 1);
    pano_near_off = (unsigned int)(bg_near >> 8) & (PANO_W - 1);

    rd_bank = video_back();
    rd_rows = cockpit ? BONNET_COVERS : SCR_H;
    PROF(prof_t0 = prof_now());
    rd_frame();
    PROF(prof_sum_road += prof_t0 - prof_now());
    PROF(prof_t0 = prof_now());
    find_cars(cz);
    rd_sprites();
    PROF(prof_sum_spr += prof_t0 - prof_now());

    if (cockpit) {
        /* the bonnet, doubled to the screen's width. The rows it covers
         * (BONNET_COVERS on) are the same every frame: drawn once into
         * each buffer, then only its front edge over the road */
        unsigned char b = rd_bank & 1;
        sp_id = SPR_BONNET;
        sp_x = SCR_W / 2;
        sp_y = SCR_H + 2 - (bonnet_done & (1 << b) ? bump : 0);
        sp_w = SCR_W;
        sp_h = 44;
        sp_clip = bonnet_done & (1 << b) ? BONNET_COVERS - 1 : SCR_H - 1;
        rd_sprite();
        bonnet_done |= (unsigned char)(1 << b);
    } else {
        /* the player's car, CHASE_BACK ahead of the camera */
        unsigned long s = (unsigned long)FOCAL * 65536UL / CHASE_BACK;
        unsigned int pseg = seg_of(car_z[0]);
        signed int py = car_zone[0] || car_air[0] ? car_alt[0] : y_at(pseg);
        draw_player((unsigned int)((unsigned long)spr_ww[car_sprite(0, 0)] * s >> 16),
                    SCR_W / 2 + (signed int)(((signed long)(car_x[0] - cam_x) * (signed long)s) >> 16),
                    HZ + (signed int)(((signed long)(cam_y - py) * (signed long)s) >> 16) + bump, SCR_H - 1);
    }

    race_extras();
}

/* ------------------------------------------------------------------------ */
/* the HUD row */

unsigned char hud_mode = 255;
void hud(void) {
    if (state == S_GARAGE) {
        garage_hud();
        return;
    }
    if (state == S_MAP) {
        map_hud();
        return;
    }
    race_hud();
}


/* ------------------------------------------------------------------------ */
/* the player's car, the save file, the garage, the music */

/* the KERNAL's ROM at $C000 in (interrupts on), as at the start and the
 * end; out (interrupts off) while the game runs: the program has the RAM
 * there. kernal.s45's kcall maps it in for each KERNAL routine it calls */
void kjsr(unsigned int at, unsigned char a, unsigned char x, unsigned char y) {
    kc_target = at;
    kc_a = a;
    kc_x = x;
    kc_y = y;
    kcall();
}

static void rom_in(void) {
    REG(0xD030) = REG(0xD030) | 0x20;
    __asm__("cli");
}

static void rom_out(void) {
    __asm__("sei");
    REG(0xD030) = REG(0xD030) & 0xDF;
}

/* the music (tools/gen_music.py): skyline on the title, afterglow in the
 * garage and on the route map, each circuit's own in its races
 * (music_mode 0: music and sounds, 1: sounds, 2: silent) */
#define MUSIC_ADDR 0x8200000UL
static const unsigned int tune_at[NTUNES] = {
    TUNE_SKYLINE_AT, TUNE_MACHINE_AT, TUNE_AFTERGLOW_AT, TUNE_AUTOROUTE_AT, TUNE_VICETOWN_AT, TUNE_OVERDRIVE_AT };
static const unsigned char circ_tune[NCIRC] = {
    TUNE_OVERDRIVE, TUNE_SKYLINE, TUNE_VICETOWN, TUNE_MACHINE,      /* Mega Circuit .. Pine Ridge */
    TUNE_VICETOWN, TUNE_OVERDRIVE, TUNE_MACHINE, TUNE_AUTOROUTE };  /* Sunset Coast .. Grand Tour */
static unsigned char music_mode, music_now = 255;

static void music(unsigned char t) {
    if (music_mode || t == music_now) return;
    music_now = t;
    tune_play_far(MUSIC_ADDR + tune_at[t]);
}

void music_for_state(void) {
    if (state == S_TITLE) music(TUNE_SKYLINE);
    else if (state == S_GARAGE || state == S_MAP) music(TUNE_AFTERGLOW);
    else music(circ_tune[circ_now]);
}

/* ------------------------------------------------------------------------ */

static unsigned char last_frame;

static unsigned char timing(void) {
    unsigned char now = FRAMECOUNT, dt = (unsigned char)(now - last_frame);
    last_frame = now;
    if (dt == 0) dt = 1;
    if (dt > MAX_DT) dt = MAX_DT;
    return dt;
}

void to_state(unsigned char s) {
    state = s;
    state_t = 0;
    banner = 0;
    banner_t = 0;
}

/* a key pressed now that was not before */
static unsigned char key_edge(unsigned char key, unsigned char bit) {
    unsigned char down = input_pressed(key);
    unsigned char was = key_last & bit;
    if (down) key_last |= bit;
    else key_last &= (unsigned char)~bit;
    return down && !was;
}

/* the garage's text rows show the picture again */
void text_rows_off(unsigned char from, unsigned char to) {
    while (from < to) video_image_row(from++);
}

/* the countdown begins, with what the race pays shown */
static void start_race(void) {
    to_state(S_COUNT);
#ifndef CHASE
    cockpit = 1;
#endif
    bonnet_done = 0;
    race_setup();
    race_worth();
}

int main(void) {
#ifdef STACK_TEST
    { unsigned char *p = (unsigned char *)0xCC00; while (p < (unsigned char *)0xCF00) *p++ = 0x5A; }
#endif
    rom_out();                        /* (the RAM at $C000 is the program's) */
    kc_ptr = 0;                       /* (kcall's state is in low RAM) */
    say("\x93velocity 65\n\nloading...\n");
    if (!load_art()) {
        say("can't load the art (velchip, velattic, velatt2-5, velc0-7, velmap)\n");
        rom_in();
        return 1;
    }
    load_game();
    apply_car();
    video_on();
#ifdef CIRC
    save.circuit = CIRC;              /* (tests: the demo on this circuit) */
#endif
    track_select(save.circuit);
    sid_init();
    tune_voices(3);                   /* SIDs 2 and 3: the game's sounds have 1 and 4 */
    sound_on();
    input_flush();
    reset_race();
    to_state(S_TITLE);
    music_for_state();
    cockpit = 0;
#ifdef FREEZE
    cockpit = 1;
#endif
    PROF(prof_start());
    for (;;) {
        unsigned char dt, t;
        dt = timing();
        ctl = controls();
#ifdef GARAGE
        /* (tests: a script of presses in the garage, half a second apart:
         * the hatch bought, fat tyres bought, the high wing bought, a nitro
         * shot bought, the other colour, a race) */
        ctl = 0;
        if (state == S_GARAGE && (state_t % 25) < 3 && state_t >= 25) {
            static const unsigned char script[] = {
#ifdef GARAGE_EXIT
                C_BRAKE, C_FIRE };
#else
                C_GAS, C_GAS, C_GAS, C_GAS, C_GAS, C_RIGHT, C_FIRE, C_BRAKE, C_RIGHT, C_FIRE, C_BRAKE, C_RIGHT,
                C_RIGHT, C_FIRE, C_BRAKE, C_RIGHT, C_FIRE, C_BRAKE, C_RIGHT, 0, 0, 0, 0, 0, 0, 0, 0, C_BRAKE, 0, 0, 0,
                C_FIRE };
#endif
            unsigned char step = (unsigned char)(state_t / 25 - 1);
            if (step < sizeof script) ctl = script[step];
        }
#endif
        if (state == S_GARAGE) {
            /* (C is the colour here; RUN/STOP the exit) */
            unsigned char press = ctl & (unsigned char)~ctl_last, go;
            if (key_edge(KEY_C, 1)) garage_colour();
            if (input_pressed(KEY_RETURN) || input_pressed(KEY_SPACE)) press &= (unsigned char)~C_GAS;
            go = garage_keys(press);
            if (key_edge(KEY_STOP, 32)) go = G_EXIT;
            if (go) {
                garage_leave();
                if (go == G_EXIT) map_enter(0, 0);
                else {
                    reset_race();
                    start_race();
                    music_for_state();
                }
                ctl_last |= C_FIRE;           /* (that press does nothing more) */
            }
        } else if (state == S_MAP) {
            /* the route map: the next circuit to race on */
            unsigned char go = map_keys(ctl & (unsigned char)~ctl_last);
            if (key_edge(KEY_G, 16)) go = M_GARAGE;
            if (key_edge(KEY_D, 128)) map_level();
            if (key_edge(KEY_STOP, 32)) go = M_TITLE;
            if (go) {
                map_leave();
                reset_race();
                if (go == M_RACE) start_race();
                else if (go == M_GARAGE) garage_enter();
                else to_state(S_TITLE);
                music_for_state();
                ctl_last |= C_FIRE;
            }
        } else {
            if (key_edge(KEY_STOP, 32)) break;
            if (key_edge(KEY_C, 1)) {
                cockpit ^= 1;
                bonnet_done = 0;
            }
            /* nitro: N, or fire with the joystick pulled back */
            if (key_edge(KEY_N, 64) || ((ctl & C_FIRE) && (ctl & C_BRAKE) && !(ctl_last & C_FIRE))) nitro_req = 1;
        }
        if (key_edge(KEY_P, 2) && state == S_RACE) paused ^= 1;
        if (key_edge(KEY_M, 4)) {
            /* music and sounds, sounds, silence */
            music_mode = (unsigned char)((music_mode + 1) % 3);
            sound_ok = music_mode < 2;
            if (music_mode == 1) tune_stop();
            if (music_mode == 2) sound_off();
            if (music_mode == 0) {
                sound_on();
                music_now = 255;
                music_for_state();
            }
        }
        if (key_edge(KEY_V, 8)) vsync ^= 1;
#ifdef DEMO
        /* the demo starts a race after a few seconds on the title */
        if (state == S_TITLE && state_t >= 150) ctl |= C_FIRE;
        if (state == S_FINISH && state_t >= 250) ctl |= C_FIRE;   /* (then the map) */
        if (state == S_RACE && (race_t & 511) == 200) nitro_req = 1;
#endif
        if (state == S_TITLE && (ctl & C_FIRE) && !(ctl_last & C_FIRE)) {
#ifdef DEMO
            reset_race();
            start_race();
            music_for_state();
#else
            map_enter(0, 0);
#endif
        } else if (state == S_TITLE && (key_edge(KEY_G, 16)
#ifdef GARAGE
                                        || state_t > 20
#endif
                                        )) {
            garage_enter();
        } else if (state == S_FINISH && results_up && (ctl & C_FIRE) && !(ctl_last & C_FIRE)) {
            results_hide();
            map_enter(place, opened);
            ctl_last |= C_FIRE;
        }
#ifdef MAP
        /* (tests: from the title to the map, then a script of presses) */
        if (state == S_TITLE && state_t == 30) map_enter(0, 0);
        if (state == S_MAP && (state_t % 25) == 0 && state_t) {
            static const unsigned char mscript[] = { C_RIGHT, C_RIGHT, C_RIGHT, C_LEFT, C_GAS, C_BRAKE, 0, 0, C_FIRE };
            unsigned char step = (unsigned char)(state_t / 25 - 1);
            if (step < sizeof mscript) {
                unsigned char go = map_keys(mscript[step]);
                if (go == M_RACE) {
                    map_leave();
                    reset_race();
                    start_race();
                    music_for_state();
                }
            }
        }
#endif
        ctl_last = ctl;
        if (worth_shown && state != S_COUNT) {
            text_rows_off(0, 3);
            worth_shown = 0;
        }
        PROF(prof_t0 = prof_now());
        if (!paused)
#ifndef FREEZE
            for (t = 0; t < dt; t++) tick();
#endif
        PROF(prof_sum_tick += prof_t0 - prof_now());
        if (!music_mode)
            for (t = 0; t < dt; t++) tune_update();
        render();
        hud();
        hud_commit();
        if (sound_ok) {
            unsigned char rpm = rpm_of(car_v[0]);
            if (state == S_COUNT && (ctl & C_GAS)) rpm = 220;      /* (revving) */
            if (nitro_t && rpm < 240) rpm += 15;
            if (paused) sound_engine(0, 0, 0);
            else sound_engine(rpm, (ctl & C_GAS) ? 255 : 60, skid);
        }
        video_flip(vsync, cockpit && bonnet_done == 3 ? BONNET_COVERS : SCR_H);
    }
    sound_off();
    tune_stop();
    video_off();
    kjsr(K_CHROUT, 0x93, 0, 0);       /* clear the (80-column) screen */
    rom_in();                         /* (the KERNAL's again, interrupts on) */
    return 0;
}
