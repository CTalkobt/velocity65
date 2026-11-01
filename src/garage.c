/* garage.c — the garage: a showroom with the car in it as chosen, a menu
 * below to choose and buy the car, the tyres, the wing, the nitro, the
 * colour, and to race or leave
 *
 * A code overlay (game.h): its code is brought into bank 0 when called.
 */
#pragma cc45 overlay(garage)
#pragma cc45 noescape_strings          /* (the literals kept with the code) */

#include <mega65.h>
#include "game.h"
#include "render.h"
#include "sound.h"
#include "track.h"
#include "video.h"

/* the prices and names of what is for sale */
static const unsigned int __noescape model_cost[NMODEL]  = { 0, 2500, 4500, 8000 };
static const char *const __noescape model_name[NMODEL]   = { s"gt     ", s"hatch  ", s"roadstr", s"wedge  " };
static const unsigned int __noescape tyre_cost[NTYRE]    = { 0, 2000 };
static const char *const __noescape tyre_name[NTYRE]     = { s"standard", s"fat     " };
static const unsigned int __noescape wing_cost[NWING]   = { 0, 1000, 2500, 4000 };
static const char *const __noescape wing_name[NWING]    = { s"none    ", s"lip     ", s"high    ", s"big     " };
static const unsigned int __noescape nitro_cost[NNITRO] = { 0, 1500, 3500, 6000 };
static const char *const __noescape nitro_name[NNITRO]  = { s"none    ", s"1 shot  ", s"2 shots ", s"3 shots " };
static const char *const __noescape colour_name[NMODEL * 2] = {
    s"red     ", s"blue    ", s"yellow  ", s"green   ", s"white   ", s"orange  ", s"purple  ", s"black   " };

/* number v into buf (screen codes), digits wide, right-aligned */
static void put_num(char *buf, unsigned long v, unsigned char digits) {
    while (digits--) {
        buf[digits] = (char)(0x30 + (unsigned char)(v % 10));
        v /= 10;
        if (!v) break;
    }
}

static void put_str(char *buf, const char *t) {
    while (*t) *buf++ = *t++;
}

/* the garage: a showroom, the car in it as chosen, and a menu below.
 * Its rows: 0 the car, 1 the tyres, 2 the wing, 3 the nitro (each to
 * choose, and buy), 4 the colour, 5 race (on the circuit chosen on the
 * map), 6 exit (to the map, nothing bought) */
#define G_COLOUR 4
#define GR_RACE  5
#define GR_EXIT  6
#define G_ROWS   7
#define G_TOP    16                  /* the menu's first text row */
static unsigned char g_model, g_colour, g_tyre, g_wing, g_nitro, g_row;

/* the other colour (also the C key) */
void garage_colour(void) {
    g_colour ^= 1;
    garage_show();
}

static unsigned char *g_sel(unsigned char r) {
    return r == 0 ? &g_model : r == 1 ? &g_tyre : r == 2 ? &g_wing : &g_nitro;
}
static unsigned char g_count(unsigned char r) {
    return r == 0 ? NMODEL : r == 1 ? NTYRE : r == 2 ? NWING : NNITRO;
}
static unsigned char g_owned(unsigned char r) {
    unsigned char bits = r == 0 ? save.models : r == 1 ? save.tyres : r == 2 ? (unsigned char)(save.wings | 1)
                       : (unsigned char)(save.nitros | 1);
    return (bits >> *g_sel(r)) & 1;
}
static unsigned int g_cost(unsigned char r) {
    return r == 0 ? model_cost[g_model] : r == 1 ? tyre_cost[g_tyre] : r == 2 ? wing_cost[g_wing] : nitro_cost[g_nitro];
}
static unsigned char g_all_owned(void) {
    return g_owned(0) && g_owned(1) && g_owned(2) && g_owned(3);
}

/* the showroom, in bands (C64 colours 0-15: the art has the rest): a dark
 * ceiling with a strip light, a roller door, the floor with its line */
static const unsigned char gar_y[] = { 8, 13, 15, 22, 24, 28, 29, 33, 34, 38, 39, 43, 44, 48, 49, 53, 54, 58, 59, 63,
                                       64, 68, 69, 72, 75, 78, 79, 83, 84, 89, 90, 97, 99, 105, 106, 112 };
static const unsigned char gar_c[] = { 11, 1, 11, 9, 12, 11, 12, 11, 12, 11, 12, 11, 12, 11, 12, 11, 12, 11, 12, 11,
                                       12, 11, 15, 11, 15, 12, 15, 12, 15, 12, 15, 7, 15, 12, 15 };

void garage_scene(void) {
    unsigned char i;
    rd_bank = video_back();
    for (i = 0; i < sizeof gar_c; i++) {
        fr_y0 = gar_y[i];
        fr_n = (unsigned char)(gar_y[i + 1] - gar_y[i]);
        fr_col = gar_c[i];
        rd_fill_rows();
    }
    /* the car, large, turning from side to side (tick's steering) */
    draw_player(168, SCR_W / 2, 100, 111);
}

static void blank(char *line) {
    unsigned char i;
    for (i = 0; i < 40; i++) line[i] = ' ';
    line[40] = 0;
}

static const char *const __noescape g_label[G_ROWS] = { s"car", s"tyres", s"wing", s"nitro", s"colour", s"race", s"exit" };

void garage_show(void) {
    char line[41];
    unsigned char r, c;
    const char *t;
    /* the car in the showroom, as chosen (owned or not) */
    player_livery = car_livery[0] = (unsigned char)(g_model * 2 + g_colour);
    player_tyre = car_tyre[0] = g_tyre;
    player_wing = g_wing;

    blank(line);
    put_str(line + 1, s"velocity garage      credits");
    put_num(line + 31, save.credits, 7);
    video_text(0, line, 7);

    /* what the car does, as chosen */
    blank(line);
    put_str(line + 1, s"top speed     kmh    grip");
    put_num(line + 11, (unsigned int)(model_top[g_model] - wing_drag[g_wing]) * 75 / 58, 3);
    {
        /* (as apply_car's, 100 for the GT on standard tyres) */
        unsigned int g = (unsigned int)model_grip[g_model] * tyre_grip[g_tyre] >> 7;
        put_num(line + 27, (g * wing_grip[g_wing] >> 7) * 25 / 32, 3);
    }
    video_text(G_TOP - 2, line, 13);
    blank(line);
    if (g_nitro) put_str(line + 1, s"nitro: n, or fire with the stick back");
    video_text(G_TOP - 1, line, 12);

    for (r = 0; r < G_ROWS; r++) {
        c = 15;
        blank(line);
        if (g_row == r) line[1] = '>';
        put_str(line + 3, g_label[r]);
        t = r == 0 ? model_name[g_model] : r == 1 ? tyre_name[g_tyre] : r == 2 ? wing_name[g_wing]
          : r == 3 ? nitro_name[g_nitro] : r == G_COLOUR ? colour_name[g_model * 2 + g_colour]
          : r == GR_RACE ? s"win       credits" : s"to the route map";
        put_str(line + 13, t);
        if (r == GR_RACE) put_num(line + 17, prize_at(0), 5);
        if (r <= G_COLOUR) {
            line[11] = '<';
            line[22] = '>';
            if (r == G_COLOUR || g_owned(r)) put_str(line + 26, r == G_COLOUR ? s"free" : s"owned");
            else {
                put_str(line + 26, s"cr");
                put_num(line + 29, g_cost(r), 5);
                c = save.credits >= g_cost(r) ? 7 : 2;
            }
        }
        if (r == GR_RACE) c = g_all_owned() ? 5 : 11;
        video_text((unsigned char)(G_TOP + r), line, g_row == r ? 1 : c);
    }
    blank(line);
    put_str(line + 1, s"up/down: choose   left/right: change");
    video_text(G_TOP + G_ROWS, line, 12);
}

void garage_enter(void) {
    reset_race();
    to_state(S_GARAGE);
    video_look(0);                   /* (the showroom by day) */
    cockpit = 0;
    g_model = save.livery >> 1;
    g_colour = save.livery & 1;
    g_tyre = save.tyre;
    g_wing = save.wing;
    g_nitro = save.nitro;
    g_row = GR_RACE;
    garage_show();
    hud_clear();
    hud_text(0, s"saving...", 7);
    hud_commit();
    if (!saved) save_game();
    hud_clear();
    music_for_state();
}

/* leaving: the car as chosen, if it is all owned (else as it was) */
void garage_leave(void) {
    video_look(circ.look);
    text_rows_off(0, 1);
    text_rows_off(G_TOP - 2, SCR_CH);
    if (g_all_owned()) {
        if (save.livery != g_model * 2 + g_colour || save.tyre != g_tyre || save.wing != g_wing
            || save.nitro != g_nitro) saved = 0;
        save.livery = (unsigned char)(g_model * 2 + g_colour);
        save.tyre = g_tyre;
        save.wing = g_wing;
        save.nitro = g_nitro;
    }
    if (!saved) save_game();         /* what was bought, what is chosen */
    apply_car();
}

/* a key or the joystick in the garage: G_RACE to start a race, G_EXIT to
 * leave for the route map, else 0 */
unsigned char garage_keys(unsigned char press) {
    unsigned char changed = 1;
    if (press & (C_LEFT | C_RIGHT)) {
        if (g_row < G_COLOUR) {
            unsigned char *sel = g_sel(g_row), n = g_count(g_row);
            *sel = (press & C_LEFT) ? (*sel ? *sel - 1 : n - 1) : (unsigned char)((*sel + 1) % n);
        } else if (g_row == G_COLOUR) g_colour ^= 1;
        else changed = 0;
    } else if (press & C_GAS) {
        g_row = g_row ? g_row - 1 : G_ROWS - 1;
    } else if (press & C_BRAKE) {
        g_row = (unsigned char)((g_row + 1) % G_ROWS);
    } else if (press & C_FIRE) {
        if (g_row < G_COLOUR) {
            /* buy what is shown; (owned) on to the race */
            if (!g_owned(g_row)) {
                unsigned int cost = g_cost(g_row);
                if (save.credits >= cost) {
                    unsigned char bit = (unsigned char)(1 << *g_sel(g_row));
                    save.credits -= cost;
                    if (g_row == 0) save.models |= bit;
                    else if (g_row == 1) save.tyres |= bit;
                    else if (g_row == 2) save.wings |= bit;
                    else save.nitros |= bit;
                    saved = 0;
                    sound_beep(1);
                } else sound_beep(0);
            } else g_row = GR_RACE;
        } else if (g_row == G_COLOUR) g_colour ^= 1;
        else if (g_row == GR_EXIT) return G_EXIT;
        else if (g_all_owned()) return G_RACE;
        else sound_beep(0);          /* (something chosen isn't owned) */
    } else changed = 0;
    if (changed) garage_show();
    return 0;
}

static const char *g_msg_t;   /* (the message shown, and the credits) */
static unsigned long g_msg_cr;

/* the message line: what fire does */
void garage_hud(void) {
    unsigned char c = 7;
    const char *t;
    if (hud_mode != S_GARAGE) {
        hud_mode = S_GARAGE;
        g_msg_t = 0;
    }
    if (g_row < G_COLOUR) {
        if (g_owned(g_row)) t = s"fire: owned - on to the race";
        else if (save.credits >= g_cost(g_row)) t = s"fire: buy it for";
        else {
            t = s"not enough credits: need";
            c = 2;
        }
    } else if (g_row == G_COLOUR) t = s"fire or left/right: the other colour";
    else if (g_row == GR_EXIT) t = s"fire: back to the map, nothing bought";
    else if (g_all_owned()) t = s"fire: race!  3 laps, 8 cars";
    else {
        t = s"buy what is shown, or choose one owned";
        c = 2;
    }
    if (t == g_msg_t && save.credits == g_msg_cr) return;
    g_msg_t = t;
    g_msg_cr = save.credits;
    hud_clear();
    hud_text(1, t, c);
    if (g_row < G_COLOUR && !g_owned(g_row)) {
        unsigned int cost = g_cost(g_row);
        unsigned char n = 1;
        while (t[n]) n++;
        unsigned int v = save.credits >= cost ? cost : cost - (unsigned int)save.credits;
        hud_num(n + 2, v, v >= 10000 ? 5 : v >= 1000 ? 4 : 3, c);
    }
}
