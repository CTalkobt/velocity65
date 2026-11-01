# race — VELOCITY 65, a road racer for the MEGA65
#
#   make              build build/race.prg and build/race.d81 (the art is on the disk)
#   make run          build and run it in xemu-xmega65 (the disk on drive 8)
#   make demo         the self-driving build (build/demo/), run in xemu
#   make screenshot   run the self-driving build headless, save build/screenshot.png
#   make profile      the self-driving build with timings on the HUD
#   make clean        remove build/
#
# Variables: M65 (the M65Compiler tree, default ../m65compiler), XEMU,
# SHOT_DELAY (seconds).

M65     ?= ../m65compiler
CC45    := $(M65)/bin/cc45
CA45    := $(M65)/bin/ca45
LN45    := $(M65)/bin/ln45
DISK45  := $(M65)/bin/disk45
PACK45  := $(M65)/bin/pack45
TUNE45  := $(M65)/bin/tune45
STDLIB  := $(M65)/lib/build/c45.lib
XEMU    ?= xemu-xmega65
PYTHON  ?= python3

BUILD   ?= build
DEFS    ?=

CFLAGS  := -O2 -I$(M65)/lib/include -Isrc -I$(BUILD) $(DEFS)
AFLAGS  := -I$(BUILD) $(DEFS) -PNoSeqExtract

TARGET  := $(BUILD)/race.prg
DISK    := $(BUILD)/race.d81
C_MODS  := game track video sound garage disk map vsetup racex results
OBJS    := $(C_MODS:%=$(BUILD)/%.o45) $(BUILD)/tables.o45 $(BUILD)/render.o45 $(BUILD)/kernal.o45
# the circuits (gen_assets.py CIRCUITS): a file each, VELC0-VELC7, for attic
# RAM at TRK_ADDR + c * CIRC_SIZE
NCIRC   := 0 1 2 3 4 5 6 7
CIRC_BIN := $(NCIRC:%=$(BUILD)/art_circ%.bin)
CIRC_PK  := $(NCIRC:%=$(BUILD)/velc%.pk)
HEADERS := $(wildcard src/*.h) $(BUILD)/gen_config.h $(BUILD)/music.h $(BUILD)/circuits.h
ART     := $(BUILD)/art_chip.bin $(BUILD)/art_attic.bin $(BUILD)/art_attic2.bin $(BUILD)/art_attic3.bin \
           $(BUILD)/art_attic4.bin $(BUILD)/art_attic5.bin $(CIRC_BIN) $(BUILD)/art_map.bin \
           $(BUILD)/circuits.h

.PHONY: all run demo screenshot profile profshot music clean

all: $(DISK)

$(BUILD):
	mkdir -p $@

# the art, the palette and the tables
$(BUILD)/gen_config.h $(BUILD)/tables.c $(ART) &: tools/gen_assets.py | $(BUILD)
	$(PYTHON) tools/gen_assets.py $(BUILD)

# the music: composed by tools/gen_music.py, made into the tune player's
# data by tune45 (music.h: each tune's offset in it), packed for attic RAM
$(BUILD)/music.txt: tools/gen_music.py | $(BUILD)
	$(PYTHON) tools/gen_music.py $@

$(BUILD)/music.bin $(BUILD)/music.h &: $(BUILD)/music.txt
	$(TUNE45) convert $< --bin -o $(BUILD)/music.bin --header $(BUILD)/music.h

$(BUILD)/velmusic.pk: $(BUILD)/music.bin
	$(PACK45) --best $< $@ -d 0x8200000

$(BUILD)/velchip.pk: $(BUILD)/art_chip.bin
	$(PACK45) --best $< $@ -d 0x12000

# (each part of the attic art (gen_assets.py: ATTIC_SPLIT bytes apart) is
# loaded into bank 4 and the start of 5 before it is unpacked: at most 64 KB)
$(BUILD)/velattic.pk: $(BUILD)/art_attic.bin
	$(PACK45) --best $< $@ -d 0x8000000
	@test $$(stat -c %s $@) -le 65536 || { echo "$@ is over 64 KB: it can't be loaded"; rm -f $@; exit 1; }

$(BUILD)/velc%.pk: $(BUILD)/art_circ%.bin
	$(PACK45) --best $< $@ -d $$(printf '0x%X' $$((0x8100000 + $* * 0xA000)))

# the route map's picture (gen_assets.py MAP_ADDR)
$(BUILD)/velmap.pk: $(BUILD)/art_map.bin
	$(PACK45) --best $< $@ -d 0x8160000

$(BUILD)/velatt5.pk: $(BUILD)/art_attic5.bin
	$(PACK45) --best $< $@ -d 0x80E0000
	@test $$(stat -c %s $@) -le 65536 || { echo "$@ is over 64 KB: it can't be loaded"; rm -f $@; exit 1; }

$(BUILD)/velatt3.pk: $(BUILD)/art_attic3.bin
	$(PACK45) --best $< $@ -d 0x8070000
	@test $$(stat -c %s $@) -le 65536 || { echo "$@ is over 64 KB: it can't be loaded"; rm -f $@; exit 1; }

$(BUILD)/velatt4.pk: $(BUILD)/art_attic4.bin
	$(PACK45) --best $< $@ -d 0x80A8000
	@test $$(stat -c %s $@) -le 65536 || { echo "$@ is over 64 KB: it can't be loaded"; rm -f $@; exit 1; }

$(BUILD)/velatt2.pk: $(BUILD)/art_attic2.bin
	$(PACK45) --best $< $@ -d 0x8038000
	@test $$(stat -c %s $@) -le 65536 || { echo "$@ is over 64 KB: it can't be loaded"; rm -f $@; exit 1; }

$(BUILD)/%.o45: src/%.c $(HEADERS) | $(BUILD)
	$(CC45) $(CFLAGS) -c $< -o $@

# the game's logic for size (bank 0 is full); the renderer is assembly
$(BUILD)/game.o45: CFLAGS += -Os -fstack-top=0xCFFF
$(BUILD)/garage.o45 $(BUILD)/disk.o45 $(BUILD)/map.o45 $(BUILD)/vsetup.o45 $(BUILD)/racex.o45 $(BUILD)/results.o45: CFLAGS += -Os

$(BUILD)/tables.o45: $(BUILD)/tables.c $(BUILD)/gen_config.h
	$(CC45) $(CFLAGS) -c $< -o $@

$(BUILD)/render.o45: src/render.s45 $(BUILD)/gen_config.h | $(BUILD)
	$(CA45) $(AFLAGS) -c $< -o $@

$(BUILD)/kernal.o45: src/kernal.s45 | $(BUILD)
	$(CA45) $(AFLAGS) -c $< -o $@

$(TARGET): $(OBJS) $(STDLIB)
	$(LN45) -basic --ram-top=0xD000 --stack-size=0x200 --overlay-store=0x8300000-0x830FFFF \
	    -M $(BUILD)/race.map -o $@ $(OBJS) $(STDLIB)

$(STDLIB):
	$(MAKE) -C $(M65) lib

# (the code overlays, garage.c and disk.c: race.ov1, which the startup code
# loads into bank 5 and copies to attic RAM at $8300000)
$(BUILD)/race.ov1: $(TARGET)

$(DISK): $(TARGET) $(BUILD)/velchip.pk $(BUILD)/velattic.pk $(BUILD)/velatt2.pk $(BUILD)/velatt3.pk \
         $(BUILD)/velatt4.pk $(BUILD)/velatt5.pk $(CIRC_PK) $(BUILD)/velmap.pk $(BUILD)/velmusic.pk
	rm -f $@
	$(DISK45) create $@ -n "VELOCITY 65" -i "V6"
	$(DISK45) add $@ $(TARGET) RACE
	$(DISK45) add $@ $(BUILD)/race.ov1 RACE.OV1
	$(DISK45) add $@ $(BUILD)/velchip.pk VELCHIP
	$(DISK45) add $@ $(BUILD)/velattic.pk VELATTIC
	$(DISK45) add $@ $(BUILD)/velatt2.pk VELATT2
	$(DISK45) add $@ $(BUILD)/velatt3.pk VELATT3
	$(DISK45) add $@ $(BUILD)/velatt4.pk VELATT4
	$(DISK45) add $@ $(BUILD)/velatt5.pk VELATT5
	for c in $(NCIRC); do $(DISK45) add $@ $(BUILD)/velc$$c.pk VELC$$c || exit 1; done
	$(DISK45) add $@ $(BUILD)/velmap.pk VELMAP
	$(DISK45) add $@ $(BUILD)/velmusic.pk VELMUSIC

run: $(DISK)
	$(XEMU) -8 $(abspath $(DISK)) -prgmode 65 -prg $(abspath $(TARGET))

demo:
	$(MAKE) BUILD=build/demo DEFS=-DDEMO run

profile:
	$(MAKE) BUILD=build/profile DEFS="-DDEMO -DPROFILE" run

screenshot:
	$(MAKE) BUILD=build/demo DEFS=-DDEMO all
	DISK=$(abspath build/demo/race.d81) $(PYTHON) tools/screenshot.py build/demo/race.prg build/screenshot.png $(SHOT_DELAY)

clean:
	rm -rf build

profshot:
	$(MAKE) BUILD=build/profile DEFS="-DDEMO -DPROFILE" all
	DISK=$(abspath build/profile/race.d81) $(PYTHON) tools/screenshot.py build/profile/race.prg build/profshot.png $(SHOT_DELAY)

# hear the music on the PC (tune45's reSID model)
music: $(BUILD)/music.txt
	$(TUNE45) play $<
