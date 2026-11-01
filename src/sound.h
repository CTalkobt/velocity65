/* sound.h — engine, tyres and effects on the SIDs */
#pragma once

void sound_on(void);
void sound_off(void);
/* once per video frame: rpm 0..255 (0: engine off), skid 0..255 */
extern unsigned int snd_f;           /* the engine's pitch (SID frequency) */
void sound_engine(unsigned char rpm, unsigned char load, unsigned char skid);
void sound_crash(void);
void sound_bump(void);
void sound_beep(unsigned char high);
