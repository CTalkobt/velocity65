#!/usr/bin/env python3
"""The game's music: six original tunes, written out by hand, as tune45 text.

Usage: gen_music.py <out.txt>

Each tune is in the manner of a style of driving music (the melodies and
riffs are this file's own):

  skyline    the title: dreamy indie dance, a steady pulse, bright
             sequenced arpeggios under an airy lead (D major, 118 bpm)
  machine    cold new wave: a doubled synth riff over a mechanical beat and
             a pumping bass (C minor, 128 bpm)
  afterglow  a wistful ballad for the garage and the route map: soft pads,
             an electric-piano arpeggio, a tender lead (Eb major, 90 bpm)
  autoroute  motorik: a steady sequencer, a bleeping melody, a vocoder-ish
             pad and a car horn (F major, 130 bpm): the Grand Tour's
  vicetown   80s TV-cop: gated snares and toms, brass stabs, a heroic
             sawtooth lead (A minor, 140 bpm)
  overdrive  synthwave: a 16th-note octave bass, big snares, a soaring lead
             with glides (F# minor, 150 bpm)

Six voices a tune: v1-v3 on SID 2 (v3 its filter's: the chords, pads and
sequencers), v4-v6 on SID 3 (v4 its filter's: the bass; v5 drums, v6
hi-hats and claps); the game's sounds have SIDs 1 and 4. A tick is a 16th
note, a bar 16 ticks. Each tune has an intro, then loops from its first
section (`|:`).

The parts are written as events: melodies as tune45 notes bar by bar
("f#5:6 e5:2 d5:4 a4:4": every bar must make 16), the rest built from a
bar's chord by small helpers (a bass pattern, an arpeggio, a drum
pattern), so a section is a list of chords and a line or two of notes.
"""
import sys

NAMES = "c c# d d# e f f# g g# a a# b".split()
FLATS = {"db": "c#", "eb": "d#", "gb": "f#", "ab": "g#", "bb": "a#"}


def note_name(n):
    return "%s%d" % (NAMES[n % 12], n // 12 - 1)


def midi(name):
    """'f#5' -> 78 (c4 = 60)."""
    i = 2 if len(name) > 1 and name[1] in "#b" else 1
    p, o = name[:i].lower(), int(name[i:])
    p = FLATS.get(p, p)
    return NAMES.index(p) + 12 * (o + 1)


INSTRUMENTS = """\
wavetable kick   pulse pulse:-5 pulse:-12
wavetable snare  noise pulse:-12 pulse:-19 noise noise
wavetable gsn    noise pulse:-10 noise pulse:-14 noise loop 5
wavetable clap   noise noise:5 noise noise:3 noise
; drums and hats
instrument kick    pulse 08 00 0800 0 0 slide=-60 hr=1 table=kick
instrument snare   pulse 00 f0 0800 0 0 hr=1 table=snare
instrument gsnare  pulse 00 f0 0800 0 0 hr=1 table=gsn
instrument clap    noise 00 f0 0800 0 0 hr=1 table=clap
instrument tom     pulse 08 00 0800 0 0 slide=-22 hr=1
instrument hat     noise 01 00 0800 0 0
instrument ohat    noise 05 00 0800 0 0
instrument shaker  noise 02 00 0800 0 0
instrument crash   noise 0b 00 0800 0 0
; basses (SID 3's filter)
instrument bround  pulse 05 80 0500 0 0 hr=1 filter=lp res=8 cutoff=260 fsweep=-4
instrument bmach   saw   02 a4 0800 0 0 hr=1 filter=lp res=10 cutoff=320 fsweep=-8
instrument bacid   saw   02 a8 0800 0 0 hr=2 filter=lp res=13 cutoff=380 fsweep=-14
instrument bsoft   tri   0a c6 0800 0 0 hr=1
instrument bvice   saw   02 98 0800 0 0 hr=1 filter=lp res=6 cutoff=2a0 fsweep=-6
instrument bmoto   pulse 03 90 0300 0 0 hr=1 filter=lp res=9 cutoff=240 fsweep=-5
; leads and counter-lines
instrument lair    pulse 4a a8 0800 2 0 pwm=16 vdelay=20 hr=1
instrument lead    pulse 0a 98 0400 3 0 pwm=24 vdelay=12 hr=1
instrument ltri    tri   3a b8 0800 2 0 vdelay=16 hr=1
instrument lsaw    saw   0a a8 0800 3 0 vdelay=10 hr=1
instrument riff    pulse 08 c6 0300 0 0 pwm=-30 hr=1
instrument riff2   saw   08 c6 0800 0 0 hr=1
instrument boop    pulse 06 00 0200 0 0 hr=1
instrument horn    pulse 04 a6 0800 0 1 hr=1
instrument echo    pulse 2a 66 0600 2 0 pwm=-20 vdelay=12 hr=1
instrument harm    pulse 2a 88 0600 2 0 pwm=20 vdelay=12 hr=1
instrument epiano  pulse 09 00 0900 0 0 hr=1
instrument blip    pulse 03 00 0180 0 0 hr=1
; chords, pads, sequencers (SID 2's filter)
instrument seq     pulse 01 50 0600 0 0 hr=1 filter=lp res=11 cutoff=300 cutrun
instrument pad     saw   8a c8 0800 0 0 filter=lp res=6 cutoff=200 cutrun
instrument stab    saw   05 60 0800 0 0 hr=1 filter=lp res=8 cutoff=500 fsweep=-12
instrument voco    pulse 6a c8 0800 1 0 pwm=30 filter=bp res=12 cutoff=200 cutrun
"""

# --- chords ------------------------------------------------------------------

QUALITY = {"": (4, 7), "m": (3, 7), "7": (4, 10), "sus": (5, 7), "5": (7, 12)}


def chord(name):
    """'F#m' -> (pitch class, (third, fifth)): the chord's root and the two
    intervals above it."""
    i = 2 if len(name) > 1 and name[1] in "#b" else 1
    root = name[:i].lower()
    root = FLATS.get(root, root)
    return NAMES.index(root), QUALITY[name[i:]]


def in_range(pc, lo):
    """The pitch class pc at or just above MIDI note lo."""
    return lo + (pc - lo) % 12


# --- a voice: events (token head, ticks); 0 ticks: a command ------------------

class Voice:
    def __init__(self):
        self.ev = []

    def ticks(self):
        return sum(d for _, d in self.ev)

    def cmd(self, s):
        self.ev.append((s, 0))

    def loop(self):
        assert self.ticks() % 16 == 0
        self.ev.append(("|:", 0))

    def rest(self, d):
        if d:
            self.ev.append(("r", d))

    def note(self, n, d):
        self.ev.append((note_name(n) if isinstance(n, int) else n, d))

    def line(self, text, transpose=0):
        """Bars of notes: "f#5:6 e5:2 | ...", each bar 16 ticks."""
        for bar in text.split("|"):
            toks = bar.split()
            if not toks:
                continue
            total = 0
            for t in toks:
                if ":" in t and not t.startswith(("@", "arp", "cut", "pw")):
                    head, d = t.rsplit(":", 1)
                    d = int(d)
                    total += d
                    if head in ("r", "~"):
                        self.ev.append((head, d))
                    else:
                        glide = ""
                        if "^" in head:
                            head, glide = head.split("^")
                            glide = "^" + glide
                        self.ev.append((note_name(midi(head) + transpose) + glide, d))
                else:
                    self.cmd(t)
            assert total == 16, "a bar of %d ticks: %s" % (total, bar)

    def text(self):
        """tune45 tokens with a | every bar (a note across one tied)."""
        out, t = [], 0
        for head, d in self.ev:
            if head == "|:":
                if out and out[-1] == "|":
                    out[-1] = "|:"
                else:
                    out.append("|:")
                continue
            if d == 0:
                out.append(head)
                continue
            first = True
            while d:
                take = min(d, 16 - t % 16)
                tok = head if first else ("r" if head == "r" else "~")
                out.append("%s:%d" % (tok, take))
                first = False
                d -= take
                t += take
                if t % 16 == 0:
                    out.append("|")
        return " ".join(out)


def notes_of(v):
    """The notes of a voice's events, in time: (start, midi or None, ticks)."""
    out, t = [], 0
    for head, d in v.ev:
        if d == 0:
            continue
        if head == "~" and out:
            s, n, dd = out[-1]
            out[-1] = (s, n, dd + d)
        elif head in ("r", "~"):
            out.append((t, None, d))
        else:
            out.append((t, midi(head.split("^")[0]), d))
        t += d
    return out


# --- part builders: a bar from a chord ----------------------------------------

def bass_bar(v, name, pat, lo=36):
    """A bar of bass from a pattern of 16 steps: R the root, O its octave, F
    the fifth, f the fifth below, . a rest, - the note before held."""
    pc, (_, fifth) = chord(name)
    r = in_range(pc, lo)
    ev = []
    for ch in pat.replace(" ", ""):
        if ch == "-":
            h, d = ev[-1]
            ev[-1] = (h, d + 1)
        elif ch == ".":
            ev.append(("r", 1))
        else:
            n = {"R": r, "O": r + 12, "F": r + fifth, "f": r + fifth - 12}[ch]
            ev.append((note_name(n), 1))
    assert sum(d for _, d in ev) == 16, pat
    v.ev += ev


def arp_bar(v, name, pat, lo=60):
    """A bar of 16ths through the chord: digits index root, third, fifth,
    octave, tenth, twelfth; . a rest, - held."""
    pc, (third, fifth) = chord(name)
    r = in_range(pc, lo)
    tones = [r, r + third, r + fifth, r + 12, r + 12 + third, r + 12 + fifth]
    ev = []
    for ch in pat.replace(" ", ""):
        if ch == "-":
            h, d = ev[-1]
            ev[-1] = (h, d + 1)
        elif ch == ".":
            ev.append(("r", 1))
        else:
            ev.append((note_name(tones[int(ch)]), 1))
    assert sum(d for _, d in ev) == 16, pat
    v.ev += ev


def chord_bar(v, name, rhythm, lo=55):
    """A bar of the chord as an arpeggio a frame (the SID's chord), struck
    in a rhythm of note lengths (a negative: a rest)."""
    pc, (third, fifth) = chord(name)
    r = in_range(pc, lo)
    v.cmd("arp:%d,%d" % (third, fifth))
    for d in rhythm:
        if d < 0:
            v.rest(-d)
        else:
            v.note(r, d)
    assert sum(abs(d) for d in rhythm) == 16


DRUMS = {"K": ("@kick", "c3"), "S": ("@snare", "d4"), "G": ("@gsnare", "d4"),
         "C": ("@clap", "c5"), "T": ("@tom", "a3"), "M": ("@tom", "e3"),
         "L": ("@tom", "a2"), "h": ("@hat", "c#8"), "o": ("@ohat", "c#8"),
         "s": ("@shaker", "g7"), "x": ("@crash", "c#8")}


def drum_bar(v, pat):
    """A bar of drums: a step a 16th (DRUMS's letters, . a rest, - held)."""
    ev = []
    last = None
    for ch in pat.replace(" ", ""):
        if ch == "-":
            h, d = ev[-1]
            ev[-1] = (h, d + 1)
        elif ch == ".":
            ev.append(("r", 1))
        else:
            inst, n = DRUMS[ch]
            if inst != last:
                ev.append((inst, 0))
                last = inst
            ev.append((n, 1))
    assert sum(d for _, d in ev) == 16, pat
    v.ev += ev


def echo_of(src, delay, start, end, down=0):
    """A voice's notes from tick start to end, delay ticks later (the last
    delay ticks of it dropped), down semitones lower: an echo line."""
    v = Voice()
    v.rest(delay)
    for s, n, d in notes_of(src):
        a, b = max(s, start), min(s + d, end - delay)
        if b <= a:
            continue
        if n is None:
            v.rest(b - a)
        else:
            v.note(n - down, b - a)
    assert v.ticks() == end - start, (v.ticks(), end - start)
    return v.ev


def harmony_of(src, start, end, scale_pcs, steps=-2):
    """A voice's notes from start to end a diatonic third (steps scale
    degrees) away: a harmony line."""
    v = Voice()
    for s, n, d in notes_of(src):
        a, b = max(s, start), min(s + d, end)
        if b <= a:
            continue
        if n is None:
            v.rest(b - a)
            continue
        sc = [m for m in range(n - 24, n + 25) if m % 12 in scale_pcs]
        i = min(range(len(sc)), key=lambda k: abs(sc[k] - n))
        v.note(sc[i + steps], b - a)
    assert v.ticks() == end - start
    return v.ev


def scale(root, minor):
    steps = [0, 2, 3, 5, 7, 8, 10] if minor else [0, 2, 4, 5, 7, 9, 11]
    pc, _ = chord(root)
    return {(pc + s) % 12 for s in steps}


def rests(v, bars):
    v.rest(16 * bars)


def tune(name, bpm, voices):
    lens = [v.ticks() for v in voices]
    assert len(set(lens)) == 1, "%s: voices of %s ticks" % (name, lens)
    lines = ["tune %s %.2f loop 16" % (name, 750.0 / bpm)]
    for i, v in enumerate(voices):
        lines.append("v%d: %s" % (i + 1, v.text()))
    return "\n".join(lines)


# =============================================================================
# 1. skyline: the title (D major, 118 bpm)
# =============================================================================

def skyline():
    v = [Voice() for _ in range(6)]
    lead, cnt, seq, bass, drm, hats = v
    INTRO = "D D Bm Bm G G A A".split()
    VERSE = "D A Bm G D A G A".split()
    CHORUS = "G A F#m Bm G A Bm A".split()
    BREAK = "Em G D A Em G A A".split()
    SEQ = "0123 2312 0123 2312"
    SEQ2 = "0124 2412 0124 3421"
    BASS = "R-.O R-.O R-.O R-.O"
    BASS2 = "R-.O R-.R O-.R R-.F"
    BEAT = "K... C... K... C..."
    HAT = "..o. ..o. ..o. ..o."
    HAT2 = "s.os s.os s.os s.os"

    # intro: the sequencer opens, the pad, bass and kick join halfway
    lead.cmd("@lair")
    rests(lead, 8)
    cnt.cmd("@pad")
    rests(cnt, 4)
    for c in INTRO[4:]:
        chord_bar(cnt, c, [16], lo=57)
    seq.cmd("@seq")
    seq.cmd("cut:80,1")
    for c in INTRO:
        arp_bar(seq, c, SEQ)
    bass.cmd("@bround")
    rests(bass, 4)
    for c in INTRO[4:]:
        bass_bar(bass, c, BASS)
    rests(drm, 4)
    for b in range(4):
        drum_bar(drm, "K... K... K... K..." if b < 3 else "K... K... K.K. CCCC")
    rests(hats, 2)
    for b in range(6):
        drum_bar(hats, HAT if b else "x.o. ..o. ..o. ..o.")

    for x in v:
        x.loop()
    # the verse twice, the chorus twice, a breakdown
    verse_a = """f#5:6 e5:2 d5:4 a4:4 | c#5:6 d5:2 e5:8 | d5:6 c#5:2 b4:4 f#4:4 | g4:4 a4:4 b4:4 d5:4 |
                 f#5:6 e5:2 d5:4 a5:4 | g5:4 f#5:4 e5:8 | d5:4 e5:4 f#5:4 g5:2 f#5:2 | e5:12 r:4"""
    verse_b = """f#5:6 e5:2 d5:4 a4:4 | c#5:6 d5:2 e5:8 | d5:6 c#5:2 b4:4 f#4:4 | g4:4 a4:4 b4:4 d5:4 |
                 a5:6 f#5:2 e5:4 d5:4 | c#5:4 e5:4 a5:8 | b5:4 a5:4 g5:4 f#5:4 | e5:8 c#5:4 a4:4"""
    chorus_a = """b5:3 a5:3 g5:2 d5:4 g5:4 | a5:3 g5:3 f#5:2 e5:8 | f#5:3 e5:3 c#5:2 a4:4 c#5:4 | d5:6 c#5:2 b4:8 |
                  b5:3 a5:3 g5:2 d6^2,2:4 b5:4 | c#6:3 b5:3 a5:2 e5:8 | f#5:4 d5:4 b5:4 a5:4 | a5^3,2:12 r:4"""
    chorus_b = """b5:3 a5:3 g5:2 d5:4 g5:4 | a5:3 g5:3 f#5:2 e5:8 | f#5:3 e5:3 c#5:2 a4:4 c#5:4 | d5:6 c#5:2 b4:8 |
                  b5:3 a5:3 g5:2 d6^2,2:4 b5:4 | c#6:3 b5:3 a5:2 e5:8 | f#5:4 a5:4 d6:4 c#6:4 | c#6^2,2:12 r:4"""
    brk = "g5:8 f#5:8 | d5:16 | f#5:8 e5:8 | c#5:16 | b4:8 d5:8 | g5:16 | e5:8 f#5:4 g5:4 | a5^5,3:16"
    t0 = lead.ticks()
    lead.line(verse_a)
    lead.line(verse_b)
    t1 = lead.ticks()
    lead.line(chorus_a)
    lead.line(chorus_b)
    t2 = lead.ticks()
    lead.line(brk)

    # the counter line: an echo in the verses, a harmony in the choruses,
    # a pad in the breakdown
    cnt.cmd("@echo")
    cnt.ev += echo_of(lead, 3, t0, t1)
    cnt.cmd("@harm")
    cnt.ev += harmony_of(lead, t1, t2, scale("D", False))
    cnt.cmd("@pad")
    for c in BREAK:
        chord_bar(cnt, c, [16], lo=57)

    seq.cmd("cut:300,0")
    for c in VERSE * 2:
        arp_bar(seq, c, SEQ)
    seq.cmd("cut:280,1")
    for c in CHORUS * 2:
        arp_bar(seq, c, SEQ2)
    seq.cmd("cut:500,-1")
    for c in BREAK:
        arp_bar(seq, c, SEQ)

    for i, c in enumerate(VERSE * 2):
        bass_bar(bass, c, BASS if i % 4 < 3 else BASS2)
    for i, c in enumerate(CHORUS * 2):
        bass_bar(bass, c, BASS2 if i % 2 else BASS)
    for c in BREAK:
        bass_bar(bass, c, "R--- ---- ---- ----")

    for i in range(16):
        drum_bar(drm, BEAT if i % 8 < 7 else "K... C... K.K. CCKC")
    for i in range(16):
        drum_bar(drm, "K... C..K K... C..." if i % 8 < 7 else "K... C... K.C. CCCC")
    rests(drm, 6)
    drum_bar(drm, "K... K... K... K...")
    drum_bar(drm, "K.K. K.K. CCCC CCCC")

    for i in range(16):
        drum_bar(hats, HAT if i else "x.o. ..o. ..o. ..o.")
    for i in range(16):
        drum_bar(hats, HAT2 if i else "x.os s.os s.os s.os")
    for i in range(8):
        drum_bar(hats, "s... s... s... s..." if i < 6 else HAT2)
    return tune("skyline", 118, v)


# =============================================================================
# 2. machine: cold new wave (C minor, 128 bpm)
# =============================================================================

def machine():
    v = [Voice() for _ in range(6)]
    lead, dbl, pad, bass, drm, hats = v
    RIFF = "c4:2 c4:2 eb4:2 c4:2 g4:4 f4:2 eb4:2 | d4:2 d4:2 f4:2 d4:2 bb3:4 c4:4"
    RIFF_B = "c4:2 c4:2 eb4:2 c4:2 g4:4 f4:2 eb4:2 | d4:2 f4:2 bb4:2 f4:2 d4:4 bb3:2 c4:2"
    VAMP = "Cm Bb Cm Bb Cm Bb Cm Bb".split()
    CHORUS = "Ab Bb Cm Cm Ab Bb G G".split()
    BRIDGE = "Fm Fm Cm Cm Ab Bb G G".split()
    BASS = "R.R. O.R. R.R. O.R."
    BASS_C = "R.RO R.RO R.RO R.RO"
    BEAT = "K... S... K.K. S..."
    BEAT2 = "K... S..K K.K. S.S."
    HAT = "hhhh ohhh hhhh ohhh"

    # intro: drums and bass for four bars, then the riff (doubled for two)
    lead.cmd("@riff")
    rests(lead, 4)
    lead.line(RIFF + " | " + RIFF)
    dbl.cmd("@riff2")
    rests(dbl, 6)
    dbl.line(RIFF, transpose=-12)
    pad.cmd("@pad")
    pad.cmd("cut:100,1")
    rests(pad, 4)
    for c in VAMP[:4]:
        chord_bar(pad, c, [16])
    bass.cmd("@bmach")
    for c in VAMP:
        bass_bar(bass, c, BASS)
    for b in range(8):
        drum_bar(drm, BEAT if b < 7 else "K... S... SSSS SSSS")
    for b in range(8):
        drum_bar(hats, HAT if b > 1 else "h.h. h.h. h.h. h.h.")

    for x in v:
        x.loop()
    # verse (the riff, doubled), chorus, verse with a voice line, chorus,
    # the bridge: bleeps over the beat
    chorus = """c5:8 eb5:4 c5:4 | d5:8 f5:4 d5:4 | eb5:6 d5:2 c5:4 g4:4 | c5:16 |
                c5:8 eb5:4 ab5:4 | g5:8 f5:4 d5:4 | d5:4 f5:4 b4:4 d5:4 | g4^5,2:12 r:4"""
    voice = """g4:4 g4:2 g4:2 f4:4 eb4:4 | d4:8 f4:8 | g4:4 g4:2 bb4:2 c5:4 bb4:4 | g4:8 f4:8 |
               g4:4 g4:2 g4:2 f4:4 eb4:4 | d4:8 bb3:8 | c4:4 eb4:4 g4:4 bb4:4 | c5:12 r:4"""
    bleeps = """c6:1 r:3 c6:1 r:3 ab5:1 r:3 f5:1 r:3 | c6:1 r:1 c6:1 r:1 ab5:1 r:3 f5:1 r:7 |
                g5:1 r:3 g5:1 r:3 eb5:1 r:3 c5:1 r:3 | g5:1 r:1 g5:1 r:1 eb5:1 r:3 c5:1 r:7 |
                c6:1 r:3 c6:1 r:3 ab5:1 r:3 eb5:1 r:3 | d6:1 r:3 d6:1 r:3 bb5:1 r:3 f5:1 r:3 |
                b5:1 r:3 b5:1 r:3 g5:1 r:3 d5:1 r:3 | d6:1 r:1 b5:1 r:1 g5:1 r:1 d5:1 r:1 g4:8"""
    verse = " | ".join([RIFF] * 3 + [RIFF_B])
    lead.cmd("@riff")
    lead.line(verse)
    lead.cmd("@lead")
    lead.line(chorus)
    lead.cmd("@riff")
    lead.line(verse)
    lead.cmd("@lead")
    lead.line(chorus)
    lead.cmd("@blip")
    lead.line(bleeps)

    dbl.cmd("@riff2")
    dbl.line(verse, transpose=-12)
    dbl.cmd("@harm")
    t = dbl.ticks()
    dbl.ev += harmony_of(lead, t, t + 128, scale("C", True))
    dbl.cmd("@echo")
    dbl.line(voice)
    dbl.cmd("@harm")
    t = dbl.ticks()
    dbl.ev += harmony_of(lead, t, t + 128, scale("C", True))
    dbl.cmd("@riff2")
    dbl.line(" | ".join([RIFF] * 4), transpose=-12)

    for part in (VAMP, CHORUS, VAMP, CHORUS, BRIDGE):
        pad.cmd("cut:180,0" if part is VAMP else "cut:120,2")
        for c in part:
            chord_bar(pad, c, [6, 6, 4] if part is CHORUS else [16])
    for part in (VAMP, CHORUS, VAMP, CHORUS, BRIDGE):
        for c in part:
            bass_bar(bass, c, BASS_C if part is CHORUS else BASS)
    for part in range(5):
        for b in range(8):
            drum_bar(drm, "K... S... K.SS SSSS" if b == 7 else BEAT2 if part in (1, 3) else BEAT)
    for part in range(5):
        for b in range(8):
            drum_bar(hats, ("x" + HAT[1:]) if b == 0 else HAT if part != 4 else "h.h. h.h. h.h. h.h.")
    return tune("machine", 128, v)


# =============================================================================
# 3. afterglow: a ballad for the garage and the map (Eb major, 90 bpm)
# =============================================================================

def afterglow():
    v = [Voice() for _ in range(6)]
    lead, ep, pad, bass, drm, hats = v
    VERSE = "Eb Bb Cm Ab Eb Bb Cm Ab".split()
    CHORUS = "Ab Bb Gm Cm Ab Bb Eb Eb".split()
    INTRO = "Eb Ab Eb Bb".split()
    EP = "0.1. 2.3. 2.1. 0.2."
    EP2 = "0.2. 3.4. 3.2. 1.2."
    BEAT = "K... .... S... ..K."
    BEAT2 = "K... ..K. S... ..K."
    HAT = "h.h. h.h. h.h. h.h."

    # intro: the pad and the piano
    lead.cmd("@ltri")
    rests(lead, 4)
    ep.cmd("@epiano")
    for c in INTRO:
        arp_bar(ep, c, EP, lo=55)
    pad.cmd("@pad")
    pad.cmd("cut:100,0")
    for c in INTRO:
        chord_bar(pad, c, [16], lo=51)
    bass.cmd("@bsoft")
    for c in INTRO:
        bass_bar(bass, c, "R--- ---- ---- ----")
    rests(drm, 3)
    drum_bar(drm, "K... .... .... ....")
    rests(hats, 4)

    for x in v:
        x.loop()
    verse_a = """g5:6 f5:2 eb5:4 bb4:4 | d5:6 eb5:2 f5:8 | g5:6 f5:2 eb5:4 c5:4 | c5:4 bb4:4 ab4:8 |
                 g5:6 f5:2 eb5:4 g5:4 | bb5:6 ab5:2 g5:4 f5:4 | eb5:4 f5:4 g5:4 c6:4 | bb5^2,3:12 r:4"""
    verse_b = """g5:6 f5:2 eb5:4 bb4:4 | d5:6 eb5:2 f5:8 | g5:6 ab5:2 bb5:4 c6:4 | c6:4 bb5:4 ab5:8 |
                 g5:6 f5:2 eb5:4 g5:4 | bb5:6 ab5:2 g5:4 f5:4 | eb5:4 f5:4 g5:4 ab5:4 | bb5^2,3:12 r:4"""
    chorus_a = """c6:6 bb5:2 ab5:4 eb5:4 | d5:4 f5:4 bb5:8 | bb5:6 g5:2 d5:8 | eb5:4 g5:4 c6:8 |
                  c6:6 bb5:2 ab5:4 c6:4 | d6:6 c6:2 bb5:4 f5:4 | g5:16 | r:4 bb4:4 eb5:4 g5:4"""
    chorus_b = """c6:6 bb5:2 ab5:4 eb5:4 | d5:4 f5:4 bb5:8 | bb5:6 g5:2 d5:8 | eb5:4 g5:4 c6:8 |
                  c6:6 bb5:2 ab5:4 c6:4 | d6:6 c6:2 bb5:4 f5:4 | eb5^3,3:16 | r:16"""
    lead.line(verse_a)
    lead.line(chorus_a)
    lead.line(verse_b)
    lead.line(chorus_b)
    rests(lead, 4)

    for part, pat in ((VERSE, EP), (CHORUS, EP2), (VERSE, EP), (CHORUS, EP2), (INTRO, EP)):
        for c in part:
            arp_bar(ep, c, pat, lo=55)
    for part in (VERSE, CHORUS, VERSE, CHORUS, INTRO):
        pad.cmd("cut:100,1" if part is CHORUS else "cut:140,0")
        for c in part:
            chord_bar(pad, c, [16], lo=51)
    for part in (VERSE, CHORUS, VERSE, CHORUS, INTRO):
        for c in part:
            bass_bar(bass, c, "R--- ---- R--- O---" if part is CHORUS else "R--- ---- ---- F---")
    for part in (VERSE, CHORUS, VERSE, CHORUS):
        for b in range(8):
            drum_bar(drm, "K... .... S... S.S." if b == 7 else BEAT2 if part is CHORUS else BEAT)
    for b in range(4):
        drum_bar(drm, "K... .... .... ...." if b < 3 else "K... .... S... ....")
    for part in (VERSE, CHORUS, VERSE, CHORUS):
        for b in range(8):
            drum_bar(hats, HAT if part is CHORUS or b % 2 else "h... h... h... h...")
    rests(hats, 4)
    return tune("afterglow", 90, v)


# =============================================================================
# 4. autoroute: motorik, for the Grand Tour (F major, 130 bpm)
# =============================================================================

def autoroute():
    v = [Voice() for _ in range(6)]
    mel, horn, seq, bass, drm, hats = v
    A = "F F Bb Bb F F C C".split()
    B = "Dm Dm Bb Bb F F C C".split()
    BR = "F F F F Bb Bb C C".split()
    SEQ = "0232 0232 0232 0232"
    SEQ2 = "0323 1323 0323 2323"
    BASS = "R.R. R.R. O.R. R.F."
    BEAT = "K.h. S.K. K.h. S.h."
    HAT = "h.h. h.h. h.h. h.h."

    # intro: the sequencer, then the beat, the horn and the bass
    mel.cmd("@boop")
    rests(mel, 8)
    horn.cmd("@horn")
    rests(horn, 6)
    horn.line("f4:2 r:2 a4:2 r:10 | f4:2 r:2 a4:2 r:2 f4:2 r:2 a4:2 r:2")
    seq.cmd("@seq")
    seq.cmd("cut:60,1")
    for c in BR:
        arp_bar(seq, c, SEQ)
    bass.cmd("@bmoto")
    rests(bass, 4)
    for c in "F F C C".split():
        bass_bar(bass, c, BASS)
    rests(drm, 2)
    for b in range(6):
        drum_bar(drm, BEAT if b > 1 else "K... K... K... K...")
    rests(hats, 4)
    for b in range(4):
        drum_bar(hats, HAT)

    for x in v:
        x.loop()
    melody_a = """a4:4 c5:4 f5:4 c5:4 | a4:4 c5:4 f5:8 | bb4:4 d5:4 f5:4 d5:4 | bb4:4 d5:4 f5:8 |
                  c5:2 c5:2 a4:4 c5:2 c5:2 f5:4 | e5:4 f5:4 g5:8 | g5:4 e5:4 c5:4 e5:4 | g5:8 e5:4 c5:4"""
    melody_a2 = """a4:4 c5:4 f5:4 c5:4 | a4:4 c5:4 f5:8 | bb4:4 d5:4 f5:4 d5:4 | bb4:4 d5:4 f5:8 |
                   c5:2 c5:2 a4:4 c5:2 c5:2 f5:4 | e5:4 f5:4 g5:8 | g5:4 a5:4 bb5:4 g5:4 | c6:16"""
    cruise = """d5:16 | f5:16 | d5:8 c5:8 | bb4:16 | a4:16 | c5:16 | g4:8 a4:8 | c5:16 |
                d5:16 | a5:16 | f5:8 d5:8 | bb4:16 | c5:16 | f5:16 | e5:8 g5:8 | c6:16"""
    radio = """f5:1 r:1 c6:1 r:5 f5:1 r:1 c6:1 r:5 | a5:1 r:3 c6:1 r:3 f6:1 r:7 |
               f5:1 r:1 c6:1 r:5 f5:1 r:1 c6:1 r:5 | a5:1 r:3 c6:1 r:3 f6:1 r:7 |
               bb5:1 r:1 f6:1 r:5 bb5:1 r:1 f6:1 r:5 | d6:1 r:3 f6:1 r:3 bb6:1 r:7 |
               c6:1 r:1 g6:1 r:5 c6:1 r:1 g6:1 r:5 | e6:1 r:1 g6:1 r:1 c7:1 r:3 g6:1 r:1 e6:1 r:1 c6:1 r:3"""
    t0 = mel.ticks()
    mel.line(melody_a)
    mel.line(melody_a2)
    mel.cmd("@ltri")
    mel.line(cruise)
    mel.cmd("@blip")
    mel.line(radio)
    t1 = mel.ticks()
    mel.cmd("@boop")
    mel.line(melody_a)
    mel.line(melody_a2)

    # the second voice: the melody's echo an octave down, a vocoder pad
    # under the cruise, the horn, a harmony
    horn.cmd("@echo")
    horn.ev += echo_of(mel, 6, t0, t0 + 256, down=12)
    horn.cmd("@voco")
    horn.cmd("cut:100,1")
    for c in B * 2:
        chord_bar(horn, c, [16], lo=50)
    horn.cmd("@horn")
    horn.line(" | ".join(["f4:2 r:2 a4:2 r:10 | r:16"] * 4))
    horn.cmd("@harm")
    horn.ev += harmony_of(mel, t1, t1 + 256, scale("F", False))

    for part, pat, cut in ((A * 2, SEQ, "cut:240,0"), (B * 2, SEQ2, "cut:180,1"),
                           (BR, SEQ, "cut:400,-1"), (A * 2, SEQ2, "cut:260,0")):
        seq.cmd(cut)
        for c in part:
            arp_bar(seq, c, pat)
    for part in (A * 2, B * 2, BR, A * 2):
        for c in part:
            bass_bar(bass, c, BASS)
    for part in (A * 2, B * 2, BR, A * 2):
        for i in range(len(part)):
            drum_bar(drm, BEAT if i % 8 < 7 else "K.h. S.K. K.S. SSSS")
    for part in (A * 2, B * 2, BR, A * 2):
        for i in range(len(part)):
            drum_bar(hats, ("x" + HAT[1:]) if i == 0 else "hhhh hhhh hhhh hhhh" if part is BR else HAT)
    return tune("autoroute", 130, v)


# =============================================================================
# 5. vicetown: 80s TV-cop (A minor, 140 bpm)
# =============================================================================

def vicetown():
    v = [Voice() for _ in range(6)]
    lead, cnt, stab, bass, drm, hats = v
    VERSE = "Am Am G G F F G G".split()
    CHORUS = "F G Em Am F G E E".split()
    BRIDGE = "Dm Dm Am Am Dm Dm E E".split()
    STAB = [3, 3, -2, 3, 3, -2]
    STAB2 = [-2, 2, -1, 3, -2, 2, -1, 3]
    BASS = "R.RO R.RO R.RO R.RO"
    BASS2 = "R.R. RO.R R.R. ROFO"
    BEAT = "K... G-.. K.K. G-.."
    FILL = "TTMM MMLL LLLL G---"

    # intro: stabs and the bass, the drums building to a tom fill
    lead.cmd("@lsaw")
    rests(lead, 4)
    cnt.cmd("@echo")
    rests(cnt, 4)
    stab.cmd("@stab")
    for c in "Am Am G G".split():
        chord_bar(stab, c, STAB)
    bass.cmd("@bvice")
    for c in "Am Am G G".split():
        bass_bar(bass, c, BASS)
    for p in ("K... K... K... K...", BEAT, BEAT, FILL):
        drum_bar(drm, p)
    for b in range(4):
        drum_bar(hats, "h.h. h.h. h.h. h.h." if b < 3 else "h.h. h.h. C.C. CCCC")

    for x in v:
        x.loop()
    verse_a = """a4:3 c5:3 e5:2 g5:6 e5:2 | d5:3 e5:3 a4:10 | b4:3 d5:3 g5:2 a5:6 g5:2 | d5:3 e5:3 g4:10 |
                 a5:3 g5:3 f5:2 e5:4 c5:4 | c5:3 d5:3 f5:2 e5:8 | d5:2 d5:2 e5:2 g5:2 a5:4 b5:4 | b5:8 a5:4 g5:4"""
    verse_b = """a4:3 c5:3 e5:2 g5:6 e5:2 | d5:3 e5:3 a4:10 | b4:3 d5:3 g5:2 a5:6 g5:2 | d5:3 e5:3 g4:10 |
                 a5:3 g5:3 f5:2 e5:4 c5:4 | c5:3 d5:3 f5:2 e5:8 | g5:4 a5:4 b5:4 d6:4 | e6^2,2:12 r:4"""
    chorus = """c6:4 a5:4 f5:4 a5:4 | b5:4 g5:4 d5:4 g5:4 | g5:6 e5:2 b5:8 | a5:12 e5:4 |
                f5:3 a5:3 c6:2 f6^2,2:8 | d6:3 b5:3 g5:2 d6:8 | e6:4 d6:4 b5:4 g#5:4 | e5^5,2:12 r:4"""
    bridge = "f5:16 | e5:16 | c5:16 | e5:16 | f5:8 a5:8 | d6:16 | e6:16 | g#5:16"
    t0 = lead.ticks()
    lead.line(verse_a)
    lead.line(verse_b)
    lead.line(chorus)
    lead.line(chorus)
    lead.line(bridge)
    lead.line(chorus)

    # the counter line: the verse's echo an octave down, the chorus's
    # harmony, a pad under the bridge
    cnt.ev += echo_of(lead, 3, t0, t0 + 256, down=12)
    cnt.cmd("@harm")
    cnt.ev += harmony_of(lead, t0 + 256, t0 + 512, scale("A", True))
    cnt.cmd("@pad")
    for c in BRIDGE:
        chord_bar(cnt, c, [16], lo=57)
    cnt.cmd("@harm")
    cnt.ev += harmony_of(lead, t0 + 640, t0 + 768, scale("A", True))

    for part in (VERSE * 2, CHORUS * 2, BRIDGE, CHORUS):
        for c in part:
            chord_bar(stab, c, STAB2 if part is BRIDGE else STAB)
    for part in (VERSE * 2, CHORUS * 2, BRIDGE, CHORUS):
        for i, c in enumerate(part):
            bass_bar(bass, c, BASS2 if i % 4 == 3 else BASS)
    for part in (VERSE * 2, CHORUS * 2, BRIDGE, CHORUS):
        for i in range(len(part)):
            drum_bar(drm, FILL if i % 8 == 7 else BEAT)
    for part in (VERSE * 2, CHORUS * 2, BRIDGE, CHORUS):
        for i in range(len(part)):
            drum_bar(hats, "x.h. C.h. h.h. C.h." if i == 0 else
                     "h.o. h.o. h.o. h.o." if part is BRIDGE else "h.h. C.h. h.h. C.h.")
    return tune("vicetown", 140, v)


# =============================================================================
# 6. overdrive: synthwave (F# minor, 150 bpm)
# =============================================================================

def overdrive():
    v = [Voice() for _ in range(6)]
    lead, cnt, arp, bass, drm, hats = v
    VERSE = "F#m D A E F#m D A E".split()
    CHORUS = "D E C#m F#m D E F#m F#m".split()
    BREAK = "D E C#m F#m D E E E".split()
    ARP = "0123 0123 0123 0123"
    ARP2 = "0124 3210 0124 3210"
    BASS = "RORO RORO RORO RORO"
    BEAT = "K... S... K... S..."
    BEAT2 = "K... S..K K.K. S..."
    HAT = "hhoh hhoh hhoh hhoh"

    # intro: the arpeggio opening up, the bass and the kick joining
    lead.cmd("@lead")
    rests(lead, 8)
    cnt.cmd("@pad")
    rests(cnt, 8)
    arp.cmd("@seq")
    arp.cmd("cut:60,2")
    for c in VERSE:
        arp_bar(arp, c, ARP)
    bass.cmd("@bacid")
    rests(bass, 4)
    for c in VERSE[4:]:
        bass_bar(bass, c, BASS)
    rests(drm, 4)
    for b in range(4):
        drum_bar(drm, "K... K... K... K..." if b < 3 else "K... S... SSSS SSSS")
    rests(hats, 6)
    drum_bar(hats, "h.h. h.h. h.h. h.h.")
    drum_bar(hats, "hhhh hhhh hhhh hhhh")

    for x in v:
        x.loop()
    verse_a = """c#5:4 f#5:4 a5:4 g#5:2 f#5:2 | f#5:4 e5:4 d5:8 | e5:4 a5:4 c#6:4 b5:2 a5:2 | b5:8 g#5:4 e5:4 |
                 c#6:6 b5:2 a5:4 f#5:4 | a5:6 f#5:2 d5:4 f#5:4 | e5:4 a5:4 b5:4 c#6:4 | b5:12 r:4"""
    verse_b = """c#5:4 f#5:4 a5:4 g#5:2 f#5:2 | f#5:4 e5:4 d5:8 | e5:4 a5:4 c#6:4 b5:2 a5:2 | b5:8 g#5:4 e5:4 |
                 f#6^2,2:6 e6:2 c#6:4 a5:4 | d6:6 c#6:2 a5:4 f#5:4 | e5:4 a5:4 c#6:4 e6:4 | e6:8 b5:8"""
    chorus_a = """f#5:2 a5:2 d6^3,2:12 | e6:4 d6:4 b5:8 | c#6:6 b5:2 g#5:4 e5:4 | f#5:16 |
                  f#5:2 a5:2 d6:8 e6:4 | f#6:4 e6:4 b5:8 | c#6:4 a5:4 f#5:4 a5:4 | c#6^2,2:12 r:4"""
    chorus_b = """f#5:2 a5:2 d6^3,2:12 | e6:4 d6:4 b5:8 | c#6:6 b5:2 g#5:4 e5:4 | f#5:16 |
                  f#5:2 a5:2 d6:8 e6:4 | f#6:4 e6:4 b5:8 | f#6:4 e6:4 c#6:4 a5:4 | f#6^5,2:12 r:4"""
    t0 = lead.ticks()
    lead.line(verse_a)
    lead.line(verse_b)
    t1 = lead.ticks()
    lead.line(chorus_a)
    lead.line(chorus_b)
    t2 = lead.ticks()
    rests(lead, 8)
    lead.line(chorus_a)
    lead.line(chorus_b)

    cnt.cmd("@echo")
    cnt.ev += echo_of(lead, 3, t0, t1)
    cnt.cmd("@harm")
    cnt.ev += harmony_of(lead, t1, t2, scale("F#", True))
    cnt.cmd("@pad")
    for c in BREAK:
        chord_bar(cnt, c, [16], lo=57)
    cnt.cmd("@harm")
    cnt.ev += harmony_of(lead, t2 + 128, t2 + 384, scale("F#", True))

    for part, pat, cut in ((VERSE * 2, ARP, "cut:240,0"), (CHORUS * 2, ARP2, "cut:300,0"),
                           (BREAK, ARP, "cut:80,2"), (CHORUS * 2, ARP2, "cut:300,0")):
        arp.cmd(cut)
        for c in part:
            arp_bar(arp, c, pat)
    for part in (VERSE * 2, CHORUS * 2, BREAK, CHORUS * 2):
        for i, c in enumerate(part):
            bass_bar(bass, c, "R--- ---- ---- ----" if part is BREAK and i < 4 else BASS)
    for part in (VERSE * 2, CHORUS * 2):
        for i in range(len(part)):
            drum_bar(drm, "K... S... K.K. SSSS" if i % 8 == 7 else BEAT2 if part[0] == "D" else BEAT)
    for i in range(8):
        drum_bar(drm, "...." * 4 if i < 4 else "K... K... K... K..." if i < 6 else
                 "K.S. K.S. KSKS KSKS" if i == 6 else "SSSS SSSS SSSS SSSS")
    for i in range(16):
        drum_bar(drm, "K... S... K.K. SSSS" if i % 8 == 7 else BEAT2)
    for part in (VERSE * 2, CHORUS * 2, BREAK, CHORUS * 2):
        for i in range(len(part)):
            if part is BREAK:
                drum_bar(hats, "...." * 4 if i < 4 else "h.h. h.h. h.h. h.h.")
            else:
                drum_bar(hats, ("x" + HAT[1:]) if i == 0 else HAT)
    return tune("overdrive", 150, v)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "music.txt"
    parts = [INSTRUMENTS]
    for fn in (skyline, machine, afterglow, autoroute, vicetown, overdrive):
        parts.append(fn())
    with open(out, "w") as f:
        f.write("; generated by tools/gen_music.py - do not edit\n" + "\n\n".join(parts) + "\n")


if __name__ == "__main__":
    main()
