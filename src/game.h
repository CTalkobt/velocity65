/* game.h — what game.c shares with its overlays (garage.c, disk.c)
 *
 * The garage and the disk's loading and saving are code overlays (ln45):
 * kept in attic RAM, copied into one region of bank 0 when they are called,
 * so bank 0 holds only the largest of them. A call into one goes through a
 * stub; their variables and constants stay resident.
 */
#ifndef GAME_H
#define GAME_H

#include "gen_config.h"

#define NCARS      8                 /* the player is car 0 */
#ifndef LAPS
#define LAPS       3                 /* (tests: -DLAPS=1) */
#endif
#define LANE       (RW * 2 / 3)      /* from one lane's middle to the next */
#define VMAX       (232U << 8)       /* world units per frame, 8.8 */
#define CAR_HIT_X  300               /* closer than this side by side: contact */
#define CAR_HIT_Z  (300UL << 8)      /*   and this far along the road */
#define KMH(v)     ((unsigned int)(((unsigned long)(v) * 300) / VMAX))
#define FRAMECOUNT REG(0xD7FA)

/* the race (game.c; reset_race, disk.c, puts the cars on the grid) */
extern unsigned long car_z[NCARS], car_base[NCARS];
extern signed int car_x[NCARS], car_tx[NCARS];
extern unsigned int car_v[NCARS], car_vmax[NCARS];
extern signed char car_steer[NCARS];
extern unsigned char car_lap[NCARS], car_done[NCARS], car_air[NCARS], car_zone[NCARS];
extern unsigned long lap_z, fin_z;
extern unsigned char fin_n, gear, shake;
extern signed int steer_v, cam_x_s;
extern unsigned int race_t, lap_t, best_t, last_lap_t, earned;
extern signed long bg_far, bg_near;
unsigned char rnd(void);

/* more of the race, for the race's overlays (racex.c, results.c) */
extern unsigned char car_fin[NCARS];         /* finished: in this place */
extern unsigned int car_ft[NCARS];           /*   at this race_t */
extern signed int car_alt[NCARS];
extern unsigned char crash_cool, place, skid, opened, paused, at_fork;
extern unsigned int state_t, cam_seg;
extern unsigned char banner, banner_t;       /* a sprite id + 1 over the road */
extern unsigned char ctl, ctl_last;
extern unsigned int p_vmax;                  /* the player's car: top speed, */
extern unsigned char p_pull, p_grip;         /*   pull and grip (128 = 1) */
unsigned int seg_of(unsigned long z);
signed long ahead(unsigned long a, unsigned long b);
signed char curve_ahead(unsigned long z, unsigned char n);
unsigned char curve_limit(signed char c);
unsigned int next_seg(unsigned int seg, unsigned char n);
unsigned char feature_of(unsigned int seg);
unsigned long progress(unsigned char k);
unsigned char var_at(unsigned int seg);
void size_sprite(unsigned char id, unsigned int w);

/* the start, the slipstream, the nitro, the weather (racex.c) */
#define SLIP_V     (12U << 8)        /* the slipstream: top speed + 15 km/h */
#define NITRO_V    (24U << 8)        /* nitro: + 31 km/h, twice the pull, */
#define NITRO_T    150               /*   for 3 s */
#define NNITRO     4                 /* nitro: none, 1-3 charges a race */
extern unsigned char slip;           /* in a car's slipstream */
extern unsigned char nitro_n, nitro_t, nitro_req;   /* charges left, frames left, asked for */
extern unsigned char boost_t, bog_t; /* a great start's boost, a jump start's bog-down */
extern unsigned char lights;         /* the start lights lit: 0-5, 6 out */
extern unsigned char weather;        /* the circuit's (gen_assets.py W_*) */
extern unsigned char cheat;          /* every circuit open (racex.c) */
extern unsigned char sound_ok;       /* the game's sounds on */

/* the states of the game */
enum { S_TITLE, S_COUNT, S_RACE, S_FINISH, S_GARAGE, S_MAP };
extern unsigned char state;
extern unsigned char cockpit;
extern unsigned char hud_mode;

/* controls */
#define C_GAS   0x01
#define C_BRAKE 0x02
#define C_LEFT  0x04
#define C_RIGHT 0x08
#define C_FIRE  0x10

/* the cars to win: four models in two colours (livery = model * 2 +
 * colour), two kinds of tyre, four wings */
#define NMODEL 4
extern const unsigned char model_top[NMODEL], model_grip[NMODEL], model_pull[NMODEL];
extern const unsigned char tyre_grip[NTYRE];
extern const unsigned char wing_grip[NWING], wing_drag[NWING];
extern const unsigned char wing_row[NMODEL];
extern unsigned char player_livery, player_tyre, player_wing;
extern unsigned char car_livery[], car_tyre[];

/* what is kept on the disk (VELSAVE): credits, what is owned, the car.
 * It lives below the program (with render.s45's arrays, $1600-$1FFF), where
 * the KERNAL can read it to save it: the RAM at $C000 the program reaches
 * into is under the ROM while the KERNAL runs */
#define SAVE_MAGIC 0x65
struct save_rec {
    unsigned char magic;
    unsigned char models;            /* bit per model owned */
    unsigned char tyres;             /* bit per tyre owned */
    unsigned char livery, tyre;
    unsigned char wings;             /* bit per wing owned (bit 0: none) */
    unsigned char wing;
    unsigned char cleared;           /* bit per circuit won (top 3) */
    unsigned long credits;
    unsigned char circuit;           /* the circuit chosen */
    /* (added later: valid when rec_magic is) */
    unsigned char nitros;            /* bit per nitro owned (bit 0: none) */
    unsigned char nitro;             /* the nitro chosen: charges a race */
    unsigned char rec_magic;
    unsigned int lap_rec[NCIRC];     /* each circuit's best lap, */
    unsigned int race_rec[NCIRC];    /*   best race (frames; 0: none) */
    unsigned char level;             /* the difficulty: 0 easy, 1 normal, 2 hard */
};
#define REC_MAGIC 0x5A
#define SAVE_AT  0x1E40
extern struct save_rec save;
extern char name_buf[24];            /* a file name for the KERNAL */
extern unsigned char saved;          /* the credits on the disk are up to date */

/* prize money by place; prize_at: on the circuit chosen (half as much
 * again per pair of circuits along the route: CIRC_LAYER 0-4) */
#define CIRC_LAYER(c) (((c) + 1) >> 1)
extern const unsigned int prize[NCARS];
unsigned int prize_at(unsigned char i);
#define JUMP_BONUS 150
#define RAMP_BONUS 250

/* game.c */
void to_state(unsigned char s);
void hud(void);
void apply_car(void);
void music_for_state(void);
void draw_player(unsigned int bw, signed int x, signed int by, unsigned char clip);
void text_rows_off(unsigned char from, unsigned char to);

/* a KERNAL routine (kernal.s45's kcall: the ROM at $C000 mapped in for it) */
extern unsigned char kc_a, kc_x, kc_y, kc_c;
extern unsigned int kc_target, kc_ptr;
void kcall(void);
void kjsr(unsigned int at, unsigned char a, unsigned char x, unsigned char y);
#define K_SETBNK 0xFF6B
#define K_SETNAM 0xFFBD
#define K_SETLFS 0xFFBA
#define K_LOAD   0xFFD5
#define K_SAVE   0xFFD8
#define K_CHROUT 0xFFD2

/* racex.c (overlay "racex"): the race's code that runs every frame (of a
 * race and of the title's): the only overlay called then, so it stays in */
void ai_tick(unsigned char k);
void collisions(void);
unsigned char autopilot(void);
void count_tick(unsigned char c);    /* the start lights */
void race_tick(unsigned char c);     /* the slipstream, the nitro, the start */
void lap_done(void);                 /* the player's lap: a record? */
void race_extras(void);              /* the rain, the mirror, the lights, banners */
void race_hud(void);
void race_setup(void);               /* a race begins: the car, its nitro */
void hud_msg(const char *t, unsigned char c, unsigned char frames);

/* results.c (overlay "results"): what runs once at a race's start and end */
void race_worth(void);               /* what the race pays, at the countdown */
extern unsigned char worth_shown;
void race_finished(void);            /* the player is over the line */
void title_hud(void);
void count_hud(void);
void results_show(void);
void results_hide(void);
extern unsigned char results_up, new_rec;

/* garage.c (overlay "garage") */
#define G_RACE   4                   /* garage_keys: race, */
#define G_EXIT   5                   /*   leave for the title */
void garage_enter(void);
void garage_leave(void);
void garage_show(void);
void garage_colour(void);
unsigned char garage_keys(unsigned char press);
void garage_hud(void);
void garage_scene(void);

/* map.c (overlay "routes"): the route map */
#define M_RACE   1                   /* map_keys: race on the circuit chosen, */
#define M_GARAGE 2                   /*   the garage, */
#define M_TITLE  3                   /*   the title */
void map_enter(unsigned char place, unsigned char opened);
void map_leave(void);
unsigned char map_keys(unsigned char press);
void map_hud(void);
void map_scene(void);
void map_level(void);                /* the next difficulty (D) */

#ifdef PROFILE
extern unsigned long prof_sum_road, prof_sum_spr, prof_sum_tick;
#endif

/* disk.c (overlay "disk") */
void reset_race(void);
void say(const char *t);
unsigned char load_art(void);
void load_game(void);
void save_game(void);

#endif
