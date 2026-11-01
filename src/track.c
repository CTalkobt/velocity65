/* track.c — the circuit: a window of it around the camera, and lookups
 *
 * tools/gen_assets.py builds each circuit (sections of eased curves and
 * cosine hills, the scenery of each theme, the jumps and the forks) into 9
 * arrays of TRK_MAX bytes and a header, which the game loads into attic RAM
 * at TRK_ADDR + c * CIRC_SIZE:
 * curve, height (low, high), road variant, scenery left and right (sprite +
 * 1) and their x / 32, road width (64 = RW). Every frame the part around
 * the camera is copied to bank 0 by DMA.
 */
#include <dma.h>
#include "track.h"
#include "render.h"

struct circ_hdr circ;
unsigned char circ_now;
unsigned long trk_addr = TRK_ADDR;   /* the circuit's (disk.c: track_select) */

/* the window around seg (wrapping around the lap) */
static unsigned char *const win_at[9] = {
    (unsigned char *)win_curve, win_ylo, win_yhi, win_var, win_sprl, win_sprr,
    (unsigned char *)win_oxl, (unsigned char *)win_oxr, win_w };

static void window_part(unsigned int seg, unsigned char at, unsigned char n) {
    unsigned long a = trk_addr + seg;
    unsigned char i;
    for (i = 0; i < 9; i++, a += TRK_MAX) dma_copy_far((unsigned long)(win_at[i] + at), a, n);
}

void track_window(unsigned int seg) {
    unsigned int left = trk_n - seg;
    if (left >= NWIN) window_part(seg, 0, NWIN);
    else {
        window_part(seg, 0, (unsigned char)left);
        window_part(0, (unsigned char)left, (unsigned char)(NWIN - left));
    }
}

static unsigned char peek(unsigned long a) {
    unsigned char v;
    dma_copy_far((unsigned long)&v, a, 1);
    return v;
}

signed char track_curve(unsigned int seg) {
    return (signed char)peek(trk_addr + seg);
}

signed int track_y(unsigned int seg) {
    return (signed int)((unsigned int)peek(trk_addr + TRK_MAX + seg) |
                        (unsigned int)peek(trk_addr + 2 * TRK_MAX + seg) << 8);
}

unsigned char track_var(unsigned int seg) {
    return peek(trk_addr + 3 * TRK_MAX + seg);
}

unsigned char track_w(unsigned int seg) {
    return peek(trk_addr + 8 * TRK_MAX + seg);
}
