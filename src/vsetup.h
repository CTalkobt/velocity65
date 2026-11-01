/* vsetup.h — what video.c shares with vsetup.c */
#pragma once
#include "video.h"

#define COLOR_LIN 0xFF80000UL
#define ROWS (SCR_CH + 1)
#define SCREEN_A 0x1C000UL                    /* (bank 1, past the road profiles) */
#define SCREEN_B 0x1C800UL
extern unsigned int hud_row[SCR_CW];
void video_show(unsigned char b);
