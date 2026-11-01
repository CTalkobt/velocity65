/* video.c — the display: VIC-IV full-colour character mode (FCM) as a bitmap
 *
 * 40 x 24 cells of 8 x 8 pixels, a byte per pixel, with 16-bit character
 * numbers: each cell shows a 64-byte pixel block. The blocks are numbered
 * down the columns, so a pixel column is a run of bytes 8 apart and a cell
 * column is COLSTRIDE bytes: render.s45 writes rows with the DMA's
 * line-drawing addressing and columns with a destination step of 8.
 * Row 24 is ordinary text: the HUD.
 *
 * Double buffered: two screens (character number arrays, in bank 1 at
 * SCREEN_A / SCREEN_B) show two sets of pixel blocks, in bank 4 and bank 5.
 * The game draws into the hidden one; video_flip shows it, in the vertical
 * blank. The HUD row is kept in bank 0 (hud_row) and copied to both
 * screens by hud_commit. Setting it up and back (vsetup.c) is a code
 * overlay.
 */
#include <dma.h>
#include <stdio.h>
#include "video.h"
#include "vsetup.h"

/* the text row's characters, and another row's built for the DMA: in the
 * BASIC screen's RAM, unused while the game runs (render.s45 LOWSCR) */
#pragma section("lowrows", 0x0B80)
__section("lowrows") unsigned int hud_row[SCR_CW];
static __section("lowrows") unsigned int row_buf[SCR_CW];
static unsigned char shown;                   /* the buffer on display */

void video_show(unsigned char b) {
    unsigned long a = b ? SCREEN_B : SCREEN_A;
    REG(0xD060) = (unsigned char)a;
    REG(0xD061) = (unsigned char)(a >> 8);
    REG(0xD062) = (unsigned char)(a >> 16);
    shown = b;
}

unsigned char video_back(void) {
    return shown ? (unsigned char)(BUF_A >> 16) : (unsigned char)(BUF_B >> 16);
}

/* the raster line (9 bits, VIC-II numbering) */
static unsigned int raster(void) {
    unsigned char lo = REG(0xD012), hi = REG(0xD011);
    return (unsigned int)(hi & 0x80) << 1 | lo;
}

void video_flip(unsigned char vsync, unsigned char same_from) {
    /* while the beam is outside the image, so the picture never tears:
     * below row same_from (the rows from there on are the same in both
     * screens: the bonnet, the text row) or above the first; a late frame
     * waits too. vsync 0: at once */
    unsigned int from = 52 + (unsigned int)same_from;
    if (vsync)
        for (;;) {
            unsigned int r = raster();
            if (r >= from || r < 40) break;
        }
    video_show(shown ^ 1);
}

void hud_clear(void) {
    unsigned char i;
    for (i = 0; i < SCR_CW; i++) hud_row[i] = 0x20;
    hud_commit();
}

void hud_commit(void) {
    dma_copy_far(SCREEN_A + HUD_ROW * SCR_CW * 2, (unsigned long)hud_row, SCR_CW * 2);
    dma_copy_far(SCREEN_B + HUD_ROW * SCR_CW * 2, (unsigned long)hud_row, SCR_CW * 2);
}

static void hud_colour(unsigned char col, unsigned char n, unsigned char c) {
    dma_fill_far_step(COLOR_LIN + (HUD_ROW * SCR_CW + col) * 2 + 1, 2, n, c);
}

void hud_text(unsigned char col, const char *s, unsigned char c) {
    unsigned char c0 = col;
    while (*s && col < SCR_CW) hud_row[col++] = (unsigned char)*s++;
    hud_colour(c0, col - c0, c);
}

void hud_num(unsigned char col, unsigned int v, unsigned char digits, unsigned char c) {
    unsigned char d = digits;
    while (d--) {
        hud_row[col + d] = (unsigned char)(0x30 + v % 10);
        v /= 10;
    }
    hud_colour(col, digits, c);
}

/* a text row over the image (row 0..SCR_CH-1): its cells show characters,
 * in colour c, the image's pixels hidden under them */
void video_text(unsigned char row, const char *s, unsigned char c) {
    unsigned char i;
    for (i = 0; i < SCR_CW; i++) row_buf[i] = *s ? (unsigned char)*s++ : 0x20;
    dma_copy_far(SCREEN_A + row * SCR_CW * 2, (unsigned long)row_buf, SCR_CW * 2);
    dma_copy_far(SCREEN_B + row * SCR_CW * 2, (unsigned long)row_buf, SCR_CW * 2);
    dma_fill_far_step(COLOR_LIN + row * SCR_CW * 2 + 1, 2, SCR_CW, c);
}

/* the row shows the image again */
void video_image_row(unsigned char row) {
    unsigned char b, c;
    for (b = 0; b < 2; b++) {
        unsigned int ch0 = (unsigned int)((b ? BUF_B : BUF_A) >> 6);
        for (c = 0; c < SCR_CW; c++) row_buf[c] = ch0 + c * (COLSTRIDE / 64) + row;
        dma_copy_far((b ? SCREEN_B : SCREEN_A) + row * SCR_CW * 2, (unsigned long)row_buf, SCR_CW * 2);
    }
    dma_fill_far(COLOR_LIN + row * SCR_CW * 2, SCR_CW * 2, 0);
}
