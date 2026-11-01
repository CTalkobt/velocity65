/* map.c — the route map: the circuits on a map of the country, the roads
 * between them, and the next circuit to race on chosen
 *
 * The circuits come in pairs along the route (gen_assets.py MAP_ROUTES):
 * Mega Circuit, then a choice of two, two more, two more, the Grand Tour.
 * A race finished in the top 3 wins its circuit, and opens the pair after
 * it (game.c: save.cleared); a circuit won or open can be raced again.
 *
 * A code overlay (game.h): its code is brought into bank 0 when called.
 */
#pragma cc45 overlay(routes)
#pragma cc45 noescape_strings          /* (the literals kept with the code) */

#include <dma.h>
#include <mega65.h>
#include "game.h"
#include "render.h"
#include "sound.h"
#include "track.h"
#include "video.h"
#include "circuits.h"

#define MAP_TOP    20                /* the text rows under the map */

static unsigned char m_sel;          /* the circuit under the cursor */
static unsigned char m_place, m_opened;   /* the last race's place, and what it opened */
static unsigned char m_said;         /* the message line is up to date */

static unsigned char won(unsigned char c) {
    return (save.cleared >> c) & 1;
}

/* open: the first, one won, or one of the pair after a circuit won */
static unsigned char is_open(unsigned char c) {
    unsigned char l = CIRC_LAYER(c), k;
    if (!l || won(c) || cheat) return 1;
    for (k = 0; k < NCIRC; k++)
        if (CIRC_LAYER(k) == l - 1 && won(k)) return 1;
    return 0;
}

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

static void blank(char *line) {
    unsigned char i;
    for (i = 0; i < 40; i++) line[i] = ' ';
    line[40] = 0;
}

/* the text rows: the credits above; the circuit under the cursor below */
static void map_show(void) {
    char line[41];
    unsigned char c = m_sel;
    blank(line);
    put_str(line + 1, save.cleared & (1 << (NCIRC - 1)) ? s"route map - champion!" : s"route map");
    put_str(line + 23, s"credits");
    put_num(line + 31, (unsigned int)(save.credits > 65535UL ? 65535UL : save.credits), 7);
    video_text(0, line, 7);

    blank(line);
    line[1] = (char)('1' + c);
    put_str(line + 3, circ_name[c]);
    put_str(line + 17, look_name[circ_look[c]]);
    put_str(line + 28, won(c) ? s"won" : is_open(c) ? s"open" : s"locked");
    video_text(MAP_TOP, line, won(c) ? 5 : is_open(c) ? 7 : 12);

    blank(line);
    line[1] = (char)('0' + circ_km10[c] / 10);
    line[2] = '.';
    line[3] = (char)('0' + circ_km10[c] % 10);
    /*  2.9 km   1 jump   2 forks   win  3000 */
    put_str(line + 5, s"km");
    line[10] = (char)('0' + circ_jumps[c]);
    put_str(line + 12, circ_jumps[c] == 1 ? s"jump" : s"jumps");
    line[19] = (char)('0' + circ_forks[c]);
    put_str(line + 21, circ_forks[c] == 1 ? s"fork" : s"forks");
    put_str(line + 28, s"win");
    put_num(line + 32, (unsigned int)((unsigned long)prize[0] * (2 + CIRC_LAYER(c)) / 2), 5);
    video_text(MAP_TOP + 1, line, 15);

    /*  record 2:41.08   best lap 0:51.22 */
    blank(line);
    put_str(line + 1, s"record");
    put_time(line + 8, save.race_rec[c]);
    put_str(line + 18, s"best lap");
    put_time(line + 27, save.lap_rec[c]);
    video_text(MAP_TOP + 2, line, 13);

    blank(line);
    put_str(line + 1, s"<> fire: race  g: garage  d:");
    put_str(line + 30, save.level == 0 ? s"easy" : save.level == 1 ? s"normal" : s"hard");
    video_text(MAP_TOP + 3, line, 12);
    m_said = 0;
}

/* the next difficulty: easy, normal, hard */
void map_level(void) {
    save.level = (unsigned char)((save.level + 1) % 3);
    saved = 0;
    map_show();
}

/* to the map; place (1-8, 0: none) and opened: the race just run */
void map_enter(unsigned char place, unsigned char opened) {
    unsigned char c;
    to_state(S_MAP);
    cockpit = 0;
    video_look(0);                   /* (the map by day) */
    m_place = place;
    m_opened = opened;
    /* the cursor: on the next circuit to win (the first open one not won),
     * else the one raced last */
    m_sel = circ_now;
    for (c = 0; c < NCIRC; c++)
        if (is_open(c) && !won(c)) {
            m_sel = c;
            break;
        }
    map_show();
    hud_clear();
    if (!saved) {
        hud_text(0, s"saving...", 7);
        hud_commit();
        save_game();
        hud_clear();
    }
    hud_mode = 255;
}

void map_leave(void) {
    text_rows_off(0, 1);
    text_rows_off(MAP_TOP, SCR_CH);
    video_look(circ.look);
}

/* the next (dir 1) or previous open circuit; up / down: the other of a pair */
static void move(signed char dir) {
    unsigned char c = m_sel;
    do {
        c = (unsigned char)((c + NCIRC + dir) % NCIRC);
    } while (!is_open(c));
    m_sel = c;
}

unsigned char map_keys(unsigned char press) {
    unsigned char was = m_sel;
    if (press & C_RIGHT) move(1);
    else if (press & C_LEFT) move(-1);
    else if (press & (C_GAS | C_BRAKE)) {
        /* the other of the pair (1-2, 3-4, 5-6) */
        unsigned char o = m_sel & 1 ? m_sel + 1 : m_sel - 1;
        if (m_sel && m_sel < NCIRC - 1 && is_open(o)) m_sel = o;
    } else if (press & C_FIRE) {
        if (!is_open(m_sel)) {
            sound_beep(0);
            return 0;
        }
        if (m_sel != circ_now || save.circuit != m_sel) {
            save.circuit = m_sel;
            saved = 0;
        }
        track_select(m_sel);
        return M_RACE;
    }
    if (m_sel != was) {
        m_place = 0;
        map_show();
    }
    return 0;
}

/* the message line: the last race's result, else what fire does */
void map_hud(void) {
    if (hud_mode != S_MAP) {
        hud_mode = S_MAP;
        m_said = 0;
    }
    if (m_said) return;
    m_said = 1;
    hud_clear();
    if (m_place) {
        unsigned char c = m_place <= 3 ? 5 : 2;
        hud_text(1, s"you came", 7);
        hud_num(10, m_place, 1, c);
        hud_text(11, m_place == 1 ? s"st" : m_place == 2 ? s"nd" : m_place == 3 ? s"rd" : s"th", c);
        hud_text(14, m_opened ? s"- new routes are open!" : m_place <= 3 ? s"- well driven" : s"- top 3 to win it", 7);
    } else if (!is_open(m_sel)) {
        hud_text(1, s"locked: win (top 3) a race before it", 2);
    } else if (won(m_sel)) {
        hud_text(1, s"fire: race it again", 7);
    } else {
        hud_text(1, s"fire: race - top 3 wins it", 7);
    }
}

/* the map, its markers (won, open, locked) and the cursor */
void map_scene(void) {
    unsigned char c;
    rd_bank = video_back();
    dma_copy_far((unsigned long)rd_bank << 16, MAP_ADDR, SCR_W / 8 * COLSTRIDE);
    for (c = 0; c < NCIRC; c++) {
        sp_id = won(c) ? SPR_MK_DONE : is_open(c) ? SPR_MK_OPEN : SPR_MK_LOCK;
        sp_w = spr_tw[sp_id];
        sp_h = spr_th[sp_id];
        sp_x = (signed int)node_x[c];
        sp_y = node_y[c] + 6;
        sp_clip = SCR_H - 1;
        rd_sprite();
    }
    if ((FRAMECOUNT & 24) != 24) {          /* (blinking: on 3/4 of the time) */
        sp_id = SPR_MK_SEL;
        sp_w = spr_tw[sp_id];
        sp_h = spr_th[sp_id];
        sp_x = (signed int)node_x[m_sel];
        sp_y = node_y[m_sel] + 10;
        sp_clip = SCR_H - 1;
        rd_sprite();
    }
}
