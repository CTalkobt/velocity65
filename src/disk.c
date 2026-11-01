/* disk.c — loading the art, the music and the track at the start, and
 * the save file (VELSAVE): through the KERNAL (kernal.s45's kcall); and
 * (once a race, so out of bank 0 too) the grid for a race
 *
 * A code overlay (game.h): its code is brought into bank 0 when called.
 */
#pragma cc45 overlay(disk)
#pragma cc45 noescape_strings          /* (the literals kept with the code) */

#include <dma.h>
#include <string.h>
#include <unpack.h>
#include "game.h"
#include "render.h"
#include "track.h"
#include "video.h"

/* a file's name into name_buf (its length) */
static unsigned char kname(const char *name) {
    unsigned char n = 0;
    while (name[n]) {
        name_buf[n] = name[n];
        n++;
    }
    name_buf[n] = 0;
    return n;
}

static unsigned char disk_dev(void) {
    unsigned char dev = REG(0xBA);
    return dev < 8 ? 8 : dev;
}

static unsigned char kload(const char *name, unsigned long at);

void load_game(void) {
    if (kload("velsave", SAVE_AT) || save.magic != SAVE_MAGIC
        || !(save.models & (1 << (save.livery >> 1))) || !(save.tyres & (1 << save.tyre))
        || save.wing >= NWING || !((save.wings | 1) & (1 << save.wing))
        || save.circuit >= NCIRC) {
        memset(&save, 0, sizeof save);
        save.magic = SAVE_MAGIC;
        save.models = 1;             /* the GT, */
        save.tyres = 1;              /* on standard tyres, */
        save.wings = 1;              /* no wing */
#ifdef GARAGE
        save.credits = 20000;        /* (tests: to spend) */
#endif
#ifdef MAP
        save.cleared = 0x05;         /* (tests: Mega Circuit and Harbour Bay won) */
        save.credits = 12345;
#endif
    }
    /* (a save from before the nitro and the records: without them) */
    if (save.rec_magic != REC_MAGIC || save.nitro >= NNITRO || !((save.nitros | 1) & (1 << save.nitro))) {
        memset(&save.nitros, 0, sizeof save - ((unsigned int)&save.nitros - (unsigned int)&save));
        save.nitros = 1;
        save.level = 1;              /* (normal) */
        save.rec_magic = REC_MAGIC;
    }
    if (save.level > 2) save.level = 1;
#ifdef DEMO
    save.nitros = 15;                /* (the demo shows the nitro off) */
    save.nitro = 3;
#endif
}

void save_game(void) {
    unsigned char n = kname("@0:velsave");
    unsigned int end = SAVE_AT + sizeof(struct save_rec);
    kjsr(K_SETBNK, 0, 0, 0);
    kjsr(K_SETNAM, n, (unsigned char)(unsigned int)name_buf, (unsigned char)((unsigned int)name_buf >> 8));
    kjsr(K_SETLFS, 1, disk_dev(), 0);
    kc_ptr = SAVE_AT;
    kjsr(K_SAVE, 0xFB, (unsigned char)end, (unsigned char)(end >> 8));
    kc_ptr = 0;
    saved = 1;
}

/* text on the (BASIC) screen, before the game's own */
void say(const char *t) {
    while (*t) kjsr(K_CHROUT, (unsigned char)*t++, 0, 0);
}

/* LOAD a file to a chip RAM address: 0, or an error */
static unsigned char kload(const char *name, unsigned long at) {
    unsigned char n = kname(name), err;
    kjsr(K_SETBNK, (unsigned char)(at >> 16), 0, 0);
    kjsr(K_SETNAM, n, (unsigned char)(unsigned int)name_buf, (unsigned char)((unsigned int)name_buf >> 8));
    kjsr(K_SETLFS, 0, disk_dev(), 0);
    kjsr(K_LOAD, 0, (unsigned char)at, (unsigned char)(at >> 8));
    err = kc_c;
    kjsr(K_SETBNK, 0, 0, 0);
    return err;
}

unsigned char load_art(void) {
    /* the packed art goes to bank 4 (the first screen, not shown yet) and
     * unpacks from there to bank 1 and to attic RAM */
    if (kload("velchip", 0x40000UL)) return 0;
    unpack(0x40000UL);
    if (kload("velattic", 0x40000UL)) return 0;
    unpack(0x40000UL);
    if (kload("velatt2", 0x40000UL)) return 0;
    unpack(0x40000UL);
    if (kload("velatt3", 0x40000UL)) return 0;
    unpack(0x40000UL);
    if (kload("velatt4", 0x40000UL)) return 0;
    unpack(0x40000UL);
    if (kload("velatt5", 0x40000UL)) return 0;
    unpack(0x40000UL);
    {
        /* the circuits, VELC0-VELC7 */
        static char name[] = "velc0";
        unsigned char c;
        for (c = 0; c < NCIRC; c++) {
            name[4] = (char)('0' + c);
            if (kload(name, 0x40000UL)) return 0;
            unpack(0x40000UL);
        }
    }
    if (kload("velmap", 0x40000UL)) return 0;
    unpack(0x40000UL);
    if (kload("velmusic", 0x40000UL)) return 0;
    unpack(0x40000UL);
    dma_copy_far(LOWTAB, LOWTAB_ATTIC, LOWTAB_SIZE);   /* (render.s45's) */
    return 1;
}

/* the circuit c to race on: its header, panoramas and palette */
void track_select(unsigned char c) {
    circ_now = c;
    trk_addr = TRK_ADDR + (unsigned long)c * CIRC_SIZE;
    dma_copy_far((unsigned long)&circ, trk_addr + CIRC_HDR, sizeof circ);
    pano_far_a = circ.pano ? PANO_FAR_1 : PANO_FAR_0;
    pano_near_a = circ.pano ? PANO_NEAR_1 : PANO_NEAR_0;
    video_look(circ.look);
    /* the weather: in fog the road and the scenery are drawn less far */
    weather = circ.weather;
    rd_draw = weather == W_FOG ? FOG_DRAW : DRAW;
    scen_max = weather == W_FOG ? FOG_SCEN : SCEN_FAR;
}

/* the player's car from what is chosen: its sprites and its numbers */
void apply_car(void) {
    unsigned char m = save.livery >> 1;
    player_livery = save.livery;
    player_tyre = save.tyre;
    player_wing = save.wing;
    p_vmax = (unsigned int)(model_top[m] - wing_drag[save.wing]) << 8;
    p_pull = model_pull[m];
    p_grip = (unsigned char)(((unsigned long)model_grip[m] * tyre_grip[save.tyre] * wing_grip[save.wing]) >> 14);
}

/* a race from the start: the cars on the grid, the clocks at 0 */
void reset_race(void) {
    earned = 0;
    unsigned char k;

    lap_z = (unsigned long)trk_n << 16;
    fin_z = (unsigned long)(LAPS + 2) * lap_z;
    for (k = 0; k < NCARS; k++) {
        /* the grid: two by two behind the line, the player at the back */
        unsigned char slot = (unsigned char)(NCARS - 1 - k);
        if (k == 0) slot = NCARS - 1;
        else slot = (unsigned char)(k - 1);
        car_z[k] = ((unsigned long)START_SEG << 16) - 0x8000UL - (unsigned long)(slot / 2) * (560UL << 8) -
                   (unsigned long)(slot & 1) * (180UL << 8);
        car_x[k] = (slot & 1) ? LANE / 2 + 60 : -(LANE / 2 + 60);
        car_tx[k] = car_x[k];
        car_v[k] = 0;
        car_steer[k] = 0;
        car_lap[k] = 0;
        car_base[k] = 0;
        car_air[k] = 0;
        car_zone[k] = 0;
        car_done[k] = 0;
        car_ft[k] = 0;
        car_livery[k] = k ? k : player_livery;
        car_tyre[k] = k ? (unsigned char)(k >= 6) : player_tyre;
        /* the opponents' pace: the front of the grid is quicker */
        car_vmax[k] = (unsigned int)(VMAX - 3000U - (unsigned int)slot * 900U - (unsigned int)rnd() * 6U
                                     + (unsigned int)CIRC_LAYER(circ_now) * 700U
                                     + (unsigned int)save.level * 1500U - 1500U);
    }
#ifdef START_AT
    car_z[0] = (unsigned long)START_AT << 16;  /* (tests: the player starts here) */
    car_x[0] = 0;
#endif
    fin_n = 0;
    gear = 1;
    shake = 0;
    steer_v = 0;
    race_t = lap_t = 0;
    best_t = 0;
    last_lap_t = 0;
    bg_far = bg_near = 0;
    cam_x_s = car_x[0];
    nitro_t = slip = boost_t = bog_t = 0;
    lights = 0;
}
