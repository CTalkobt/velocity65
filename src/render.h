/* render.h — the DMA renderer (render.s45) and the generated tables */
#pragma once
#include "gen_config.h"

/* the frame: the camera, set before rd_frame() */
extern unsigned char rd_bank;              /* the bank of the buffer drawn into */
extern unsigned char rd_rows;              /* rows to draw (the rest is covered) */
extern unsigned int cam_zf;                /* camera z within its segment */
extern signed int cam_x, cam_y;            /* world units */
extern signed long cam_dx0;                /* the first segment's dx (quarter units) */
extern unsigned int pano_far_off, pano_near_off;
extern signed char win_curve[];
extern unsigned char win_ylo[], win_yhi[], win_var[], win_w[];

void rd_frame(void);
extern unsigned char rd_draw;              /* the last edge projected (DRAW; less in fog) */
extern unsigned char scen_max;             /* scenery beyond this edge isn't drawn */
extern unsigned char rd_wet;               /* the cars' reflections (1), their spray (2) */

/* the rain: NDROP streaks, from (x, y) down, len long */
extern unsigned char rn_n, rn_xl[NDROP], rn_xh[NDROP], rn_y[NDROP], rn_len[NDROP];
void rd_rain(void);

/* what rd_frame leaves for the sprites, per edge (0 .. e_n - 1) */
extern unsigned char e_n;
extern unsigned char e_sxl[], e_sxh[], e_syl[], e_syh[];
extern unsigned char e_s0[], e_s1[], e_s2[];
extern unsigned char e_clip[];             /* rows left free + 1 (0: hidden) */

/* a sprite: bottom centre (sp_x, sp_y), sp_w x sp_h pixels, rows below
 * sp_clip hidden */
extern unsigned char sp_id;
extern signed int sp_x, sp_y;
extern unsigned int sp_w, sp_h;
extern unsigned char sp_clip;
void rd_sprite(void);

/* the scenery in the window (track.h) and these cars, farthest first:
 * edge and fraction (pos), sprite, x */
extern unsigned char vc_n;
extern unsigned char vc_pos_l[8], vc_pos_h[8], vc_id[8], vc_xl[8], vc_xh[8];
extern unsigned char vc_yl[8], vc_yh[8];      /* height above the road */
extern unsigned char vc_axle[8];              /* the tyres, drawn first */
void rd_sprites(void);

/* the circuit's panoramas (attic RAM, page-aligned) */
extern unsigned long pano_far_a, pano_near_a;

/* fill fr_n rows from fr_y0 with colour fr_col */
extern unsigned char fr_y0, fr_n, fr_col;
void rd_fill_rows(void);

/* generated tables (tools/gen_assets.py) */
extern unsigned char sky_col[SCR_H];            /* (in low RAM: render.s45) */
extern const unsigned char prof_a0[NVAR * NMIP], prof_a1[NVAR * NMIP];
extern const unsigned char var_one[NVAR];
extern const unsigned char prof_grass[NVAR * NMIP], prof_grass_r[NVAR * NMIP];
extern const unsigned char ceil_col[4 * NMIP], face_col[NMIP];   /* the tunnels' */
extern const unsigned int scen_hw[NSCEN];
extern const signed int scen_hx[NSCEN];
/* each sprite's address in attic RAM (megabyte SPR_A3), row by row, a table
 * per byte; column by column it follows, tw * th on */
extern const unsigned char spr_a0[NSPR], spr_a1[NSPR], spr_a2[NSPR];
extern unsigned char spr_tw[NSPR], spr_th[NSPR];
extern unsigned int spr_ww[NSPR];
