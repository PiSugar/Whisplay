/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * whisplay.h -- Whisplay unified sound card driver
 */

#ifndef _WHISPLAY_H
#define _WHISPLAY_H

#include <linux/i2c.h>
#include <linux/regmap.h>
#include <sound/soc.h>

enum whisplay_chip_type {
	WHISPLAY_CHIP_UNKNOWN = 0,
	WHISPLAY_CHIP_ES8389,
	WHISPLAY_CHIP_WM8960,
};

struct whisplay_priv {
	struct i2c_client *i2c;
	struct regmap *regmap;
	enum whisplay_chip_type chip;
	struct device_node *codec_of_node;
};

extern struct snd_soc_component_driver es8389_component_driver;
extern struct snd_soc_dai_driver es8389_dai;
extern const struct regmap_config es8389_regmap_config;
extern int es8389_chip_init(struct snd_soc_component *component);
extern void es8389_apply_default_gains(struct snd_soc_component *component);

extern enum whisplay_chip_type whisplay_active_chip;
extern struct device_node *whisplay_codec_np;

#endif
