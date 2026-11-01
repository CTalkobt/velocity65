/* track.h — the circuits (built by tools/gen_assets.py, loaded into attic
 * RAM at TRK_ADDR + c * CIRC_SIZE), the one raced on, with a window of it
 * around the camera in bank 0 for the renderer and the game */
#pragma once
#include "gen_config.h"

#define NWIN       (DRAW + 2)

/* a circuit's header (after its 9 arrays, at CIRC_HDR), copied to circ
 * when it is chosen: its length, its jumps and forks (where the car can fly,
 * from .. to; the fork's ramp, on the right carriageway at RAMP_X), its
 * look (palette) and panorama */
struct circ_hdr {
    unsigned int n;                  /* segments in a lap */
    unsigned char nfeat, look, pano, weather;
    unsigned char kind[MAX_FEAT];
    unsigned int from[MAX_FEAT], to[MAX_FEAT], ramp[MAX_FEAT];
};
extern struct circ_hdr circ;
extern unsigned char circ_now;       /* the circuit chosen */
extern unsigned long trk_addr;       /* its arrays in attic RAM */
#define trk_n      circ.n
#define NFEAT      circ.nfeat
#define feat_kind  circ.kind
#define feat_from  circ.from
#define feat_to    circ.to
#define feat_ramp  circ.ramp

/* the circuit c to race on: its header, panoramas and palette (disk.c) */
void track_select(unsigned char c);

/* the window: segments cam_seg .. cam_seg + NWIN - 1 (render.s45 owns
 * the arrays) */
extern signed char win_curve[NWIN];
extern unsigned char win_ylo[NWIN], win_yhi[NWIN], win_var[NWIN];
extern unsigned char win_sprl[NWIN], win_sprr[NWIN];   /* sprite + 1, 0: none */
extern signed char win_oxl[NWIN], win_oxr[NWIN];        /* its x / 32 */
extern unsigned char win_w[NWIN];                       /* road width, 64 = RW */


void track_window(unsigned int seg);
/* one segment's values, read from attic RAM */
signed char track_curve(unsigned int seg);
signed int track_y(unsigned int seg);
unsigned char track_var(unsigned int seg);
unsigned char track_w(unsigned int seg);
