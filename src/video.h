/* video.h — the VIC-IV set up as a 320 x 192 FCM bitmap plus a text row */
#pragma once
#include "gen_config.h"

#define REG(a) (*(volatile unsigned char *)(a))

#define HUD_ROW SCR_CH              /* the text row below the image */

void video_on(void);
void video_off(void);
unsigned char video_back(void);            /* the hidden buffer's bank: draw there */
/* show it; vsync: when the beam is below row same_from of the image (the
 * rows from there are the same in both screens) or above it */
void video_flip(unsigned char vsync, unsigned char same_from);
void video_blank(unsigned char col);       /* fill both buffers with a colour */

/* the HUD row (screen codes) in colour c (0-15) */
void hud_clear(void);
void hud_commit(void);                     /* the row's text to both screens */
void hud_text(unsigned char col, const char *s, unsigned char c);
void hud_num(unsigned char col, unsigned int v, unsigned char digits, unsigned char c);

/* a row of the image (0 .. SCR_CH - 1) as text in colour c, or as the image */
void video_text(unsigned char row, const char *s, unsigned char c);
void video_image_row(unsigned char row);
void video_look(unsigned char look);          /* the palette: 0 day .. 3 night */
