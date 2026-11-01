/* vsetup.c — the display set up for the game, and back for BASIC; its
 * palettes (a look each)
 *
 * In the code overlay "disk" with disk.c (game.h): run at the start, the
 * end and when the look changes, not during a race.
 */
#pragma cc45 overlay(disk)

#include <dma.h>
#include "vsetup.h"

#define PAL_IO 0xFFD3100UL                     /* $D100: the palette's red, green, blue */

static unsigned char saved[14];

void video_blank(unsigned char col) {
    dma_fill_far(BUF_A, SCR_CW * COLSTRIDE, col);
    dma_fill_far(BUF_B, SCR_CW * COLSTRIDE, col);
}

void video_on(void) {
    unsigned char b, c, r;
    saved[0] = REG(0xD031); saved[1] = REG(0xD054); saved[2] = REG(0xD058); saved[3] = REG(0xD059);
    saved[4] = REG(0xD05D); saved[5] = REG(0xD05E); saved[6] = REG(0xD060); saved[7] = REG(0xD061);
    saved[8] = REG(0xD062); saved[9] = REG(0xD063); saved[10] = REG(0xD064); saved[11] = REG(0xD065);
    saved[12] = REG(0xD020); saved[13] = REG(0xD021);
    for (b = 0; b < 2; b++) {
        /* a row at a time, built in hud_row */
        unsigned int ch0 = (unsigned int)((b ? BUF_B : BUF_A) >> 6);
        for (r = 0; r < SCR_CH; r++) {
            for (c = 0; c < SCR_CW; c++) hud_row[c] = ch0 + c * (COLSTRIDE / 64) + r;
            dma_copy_far((b ? SCREEN_B : SCREEN_A) + r * SCR_CW * 2, (unsigned long)hud_row, SCR_CW * 2);
        }
    }
    video_blank(0);
    hud_clear();
    dma_fill_far(COLOR_LIN, SCR_CW * ROWS * 2, 0);

    REG(0xD020) = 0;
    REG(0xD021) = 0;
    REG(0xD031) = REG(0xD031) & 0x7F;                 /* 40 columns */
    REG(0xD05D) = REG(0xD05D) & 0x7F;                 /* hot registers off */
    REG(0xD054) = REG(0xD054) | 0x05;                 /* CHR16 + FCLRHI: characters > $FF are FCM */
    REG(0xD058) = SCR_CW * 2;                         /* bytes per row */
    REG(0xD059) = 0;
    REG(0xD05E) = SCR_CW;
    REG(0xD063) = REG(0xD063) & 0xF0;
    video_show(0);
    REG(0xD064) = 0;                                  /* colour RAM from its start */
    REG(0xD065) = 0;
    video_look(0);
}

/* colours 16-255 for a look (0 day, 1 dusk, 2 overcast, 3 night), from
 * attic RAM straight into the palette */
void video_look(unsigned char look) {
    unsigned long a = PAL_ADDR + (unsigned long)look * PAL_SIZE;
    dma_copy_far(PAL_IO + 16, a, 240);
    dma_copy_far(PAL_IO + 0x100 + 16, a + 240, 240);
    dma_copy_far(PAL_IO + 0x200 + 16, a + 480, 240);
}

void video_off(void) {
    /* (colours 16-255, which the game sets, are not BASIC's) */
    REG(0xD054) = saved[1];
    REG(0xD058) = saved[2]; REG(0xD059) = saved[3];
    REG(0xD05E) = saved[5];
    REG(0xD060) = saved[6]; REG(0xD061) = saved[7]; REG(0xD062) = saved[8]; REG(0xD063) = saved[9];
    REG(0xD064) = saved[10]; REG(0xD065) = saved[11];
    REG(0xD05D) = saved[4];
    REG(0xD031) = saved[0];
    REG(0xD020) = saved[12]; REG(0xD021) = saved[13];
    dma_fill_far(COLOR_LIN, 80 * 25, 1);              /* (game.c clears the screen) */
}
