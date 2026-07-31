/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * whisplay-core.c -- Whisplay unified sound card driver.
 *
 * One module, two chips. Auto-detects ES8389 or WM8960 at probe time.
 * ES8389: embedded codec backend (es8389.c)
 * WM8960: kernel's built-in snd-soc-wm8960 codec driver
 */

#include <linux/module.h>
#include <linux/i2c.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/of.h>
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/mutex.h>
#include <linux/string.h>
#include <linux/workqueue.h>
#include <linux/atomic.h>
#include <linux/version.h>
#include <sound/soc.h>
#include <sound/control.h>
#include <sound/pcm_params.h>
#include <uapi/sound/asound.h>

#include "whisplay.h"
#include "es8389.h"
#include "whisplay-gain-lut.h"

#ifndef snd_soc_rtd_to_codec
#define snd_soc_rtd_to_codec asoc_rtd_to_codec
#endif
#ifndef snd_soc_rtd_to_cpu
#define snd_soc_rtd_to_cpu asoc_rtd_to_cpu
#endif

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 12, 0)
#define whisplay_substream_to_rtd snd_soc_substream_to_rtd
#else
#define whisplay_substream_to_rtd asoc_substream_to_rtd
#endif

enum whisplay_chip_type whisplay_active_chip = WHISPLAY_CHIP_UNKNOWN;
EXPORT_SYMBOL_GPL(whisplay_active_chip);

struct device_node *whisplay_codec_np;
EXPORT_SYMBOL_GPL(whisplay_codec_np);

static DEFINE_MUTEX(whisplay_lock);
static struct snd_soc_component *whisplay_codec_component;

static void whisplay_hide_legacy_controls(struct snd_soc_card *card);
static int whisplay_apply_boot_defaults(struct snd_soc_card *card);

/* Defer hiding until boot defaults are applied (late_probe + short margin). */
static struct snd_soc_card *whisplay_hide_card;
static struct delayed_work whisplay_hide_dwork;
static struct delayed_work whisplay_boot_dwork;
static struct delayed_work whisplay_wm8960_reprime_dwork;
static struct delayed_work whisplay_wm8960_adc_unmute_dwork;
static atomic_t whisplay_wm8960_capture_active = ATOMIC_INIT(0);
static bool whisplay_hide_dwork_inited;
static bool whisplay_boot_dwork_inited;
static bool whisplay_wm8960_reprime_dwork_inited;
static bool whisplay_wm8960_adc_unmute_dwork_inited;

#define WHISPLAY_BOOT_DELAY_MS 1000

/* Set 1 during laboratory calibration so amixer can reach PGA/ADC/OSR. */
static bool skip_legacy_hide;
module_param(skip_legacy_hide, bool, 0644);
MODULE_PARM_DESC(skip_legacy_hide, "Do not hide legacy mixer controls (for LUT calibration)");

static void whisplay_hide_work_fn(struct work_struct *work)
{
	if (skip_legacy_hide || !whisplay_hide_card)
		return;
	whisplay_hide_legacy_controls(whisplay_hide_card);
}

static void whisplay_boot_work_fn(struct work_struct *work)
{
	if (whisplay_hide_card)
		whisplay_apply_boot_defaults(whisplay_hide_card);
}

/*
 * Unified user-facing gain controls (shown in alsamixer):
 *   - speaker
 *   - mic
 *
 * Control range is 0..100.  Index 100 is each chip's maximum clean output
 * before the speaker clips; 0 is minimum.  Per-chip raw values come from
 * observer-calibrated lookup tables in whisplay-gain-lut.h.
 */
#define WHISPLAY_GAIN_MIN 0
#define WHISPLAY_GAIN_MAX (WHISPLAY_LUT_SIZE - 1)
#define WHISPLAY_PLAYBACK_GAIN_DEFAULT 80
#define WHISPLAY_CAPTURE_GAIN_DEFAULT 80
#define WHISPLAY_CARD_ID "whisplaysound"
#define WHISPLAY_CARD_DISPLAY_NAME "Whisplay Sound"
#define WHISPLAY_SPEAKER_CTL "speaker"
#define WHISPLAY_MIC_CTL "mic"
/* Re-apply hide once in case a codec registers controls slightly after late_probe. */
#define WHISPLAY_HIDE_RETRY_MS 500
#define WHISPLAY_WM8960_ADC_PCM_VOLUME 195
#define WHISPLAY_WM8960_ADC_STARTUP_MUTE_MS 5
#define WHISPLAY_WM8960_ADC_RAMP_STEPS 24
#define WHISPLAY_WM8960_ADC_RAMP_US 1000
#define WHISPLAY_A733_RATE 48000U
#define WHISPLAY_A733_PLL_RATE 24576000U
#define WHISPLAY_A733_SLOTS 2
#define WHISPLAY_A733_SLOT_WIDTH 32
#define WHISPLAY_A733_I2S_TX0CHSEL 0x34
#define WHISPLAY_A733_I2S_TX1CHSEL 0x38
#define WHISPLAY_A733_I2S_TX2CHSEL 0x3c
#define WHISPLAY_A733_I2S_TX3CHSEL 0x40
#define WHISPLAY_A733_I2S_RXCHSEL 0x64
#define WHISPLAY_A733_I2S_DATA_DELAY_SHIFT 20
#define WHISPLAY_A733_I2S_DATA_DELAY_MASK \
	(0x3U << WHISPLAY_A733_I2S_DATA_DELAY_SHIFT)
#define WHISPLAY_A733_I2S_DATA_DELAY_I2S \
	(0x1U << WHISPLAY_A733_I2S_DATA_DELAY_SHIFT)

static int whisplay_playback_gain_cache = WHISPLAY_PLAYBACK_GAIN_DEFAULT;
static int whisplay_capture_gain_cache = WHISPLAY_CAPTURE_GAIN_DEFAULT;

static int whisplay_lut_raw(const int *lut, int gain);
static int whisplay_apply_capture_gain(struct snd_soc_card *card, int gain);
static int whisplay_wm8960_prime_capture_muted(void);
static void whisplay_wm8960_adc_unmute_work_fn(struct work_struct *work);
static void whisplay_wm8960_reprime_work_fn(struct work_struct *work);

static bool whisplay_is_public_control(const char *name)
{
	return !strcmp(name, WHISPLAY_SPEAKER_CTL) ||
	       !strcmp(name, WHISPLAY_MIC_CTL);
}

#define WM8960_LINVOL 0x00
#define WM8960_RINVOL 0x01
#define WM8960_LOUT1 0x02
#define WM8960_ROUT1 0x03
#define WM8960_DACCTL1 0x05
#define WM8960_LDAC 0x0a
#define WM8960_RDAC 0x0b
#define WM8960_LADC 0x15
#define WM8960_RADC 0x16
#define WM8960_POWER1 0x19
#define WM8960_LINPATH 0x20
#define WM8960_RINPATH 0x21
#define WM8960_LOUTMIX 0x22
#define WM8960_ROUTMIX 0x25
#define WM8960_MONOMIX1 0x26
#define WM8960_MONOMIX2 0x27
#define WM8960_LOUT2 0x28
#define WM8960_ROUT2 0x29
#define WM8960_BYPASS1 0x2d
#define WM8960_BYPASS2 0x2e
#define WM8960_CLASSD3 0x33

#define WM8960_POWER1_VMID_50K BIT(7)
#define WM8960_POWER1_VREF BIT(6)
#define WM8960_POWER1_AINL BIT(5)
#define WM8960_POWER1_AINR BIT(4)
#define WM8960_POWER1_ADCL BIT(3)
#define WM8960_POWER1_ADCR BIT(2)
#define WM8960_POWER1_MICB BIT(1)
#define WM8960_CAPTURE_POWER_MASK (BIT(8) | BIT(7) | \
				   WM8960_POWER1_VREF | \
				   WM8960_POWER1_AINL | \
				   WM8960_POWER1_AINR | \
				   WM8960_POWER1_ADCL | \
				   WM8960_POWER1_ADCR | \
				   WM8960_POWER1_MICB)
#define WM8960_CAPTURE_POWER_ON (WM8960_POWER1_VMID_50K | \
				 WM8960_POWER1_VREF | \
				 WM8960_POWER1_AINL | \
				 WM8960_POWER1_AINR | \
				 WM8960_POWER1_ADCL | \
				 WM8960_POWER1_ADCR | \
				 WM8960_POWER1_MICB)

static inline int whisplay_clamp(int v, int lo, int hi)
{
	if (v < lo)
		return lo;
	if (v > hi)
		return hi;
	return v;
}

static int whisplay_find_kctl(struct snd_soc_card *card, const char *name,
			      struct snd_kcontrol **out)
{
	struct snd_ctl_elem_id id = { 0 };
	struct snd_kcontrol *kctl;

	if (!card || !card->snd_card)
		return -ENODEV;

	id.iface = SNDRV_CTL_ELEM_IFACE_MIXER;
	strscpy(id.name, name, sizeof(id.name));
	kctl = snd_ctl_find_id(card->snd_card, &id);
	if (!kctl)
		return -ENOENT;

	*out = kctl;
	return 0;
}

static int whisplay_kctl_set_int(struct snd_soc_card *card, const char *name,
				 long left, long right, bool stereo)
{
	struct snd_kcontrol *kctl;
	struct snd_ctl_elem_value uval = { 0 };
	int ret;

	ret = whisplay_find_kctl(card, name, &kctl);
	if (ret < 0)
		return ret;
	if (!kctl->put)
		return -EOPNOTSUPP;

	uval.value.integer.value[0] = left;
	uval.value.integer.value[1] = stereo ? right : left;
	return kctl->put(kctl, &uval);
}

static int whisplay_kctl_set_int_opt(struct snd_soc_card *card, const char *name,
				     long left, long right, bool stereo)
{
	int ret = whisplay_kctl_set_int(card, name, left, right, stereo);

	if (ret == -ENOENT)
		return 0;
	return ret;
}

static int whisplay_kctl_set_bool(struct snd_soc_card *card, const char *name,
				  bool on, bool stereo)
{
	return whisplay_kctl_set_int(card, name, on ? 1 : 0, on ? 1 : 0, stereo);
}

/* Non-fatal for optional route switches (may be absent before codec kctls exist). */
static int whisplay_kctl_set_bool_opt(struct snd_soc_card *card, const char *name,
				      bool on, bool stereo)
{
	int ret = whisplay_kctl_set_bool(card, name, on, stereo);

	if (ret == -ENOENT)
		return 0;
	return ret;
}

static int whisplay_codec_update_bits(unsigned int reg, unsigned int mask,
				      unsigned int val)
{
	if (!whisplay_codec_component)
		return -ENODEV;
	return snd_soc_component_update_bits(whisplay_codec_component, reg, mask, val);
}

static int whisplay_codec_write(unsigned int reg, unsigned int val)
{
	if (!whisplay_codec_component)
		return -ENODEV;
	return snd_soc_component_write(whisplay_codec_component, reg, val);
}

static int whisplay_wm8960_direct_playback(int hp_raw)
{
	int ret;

	ret = whisplay_codec_write(WM8960_LDAC, 0x100 | 255);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_write(WM8960_RDAC, 0x100 | 255);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_update_bits(WM8960_DACCTL1, BIT(7), 0);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_update_bits(WM8960_LOUTMIX, BIT(8), BIT(8));
	if (ret < 0)
		return ret;
	ret = whisplay_codec_update_bits(WM8960_ROUTMIX, BIT(8), BIT(8));
	if (ret < 0)
		return ret;
	ret = whisplay_codec_update_bits(WM8960_CLASSD3, 0x3f, (5 << 3) | 5);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_write(WM8960_LOUT1, 0x180 | hp_raw);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_write(WM8960_ROUT1, 0x180 | hp_raw);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_write(WM8960_LOUT2, 0x180 | hp_raw);
	if (ret < 0)
		return ret;
	return whisplay_codec_write(WM8960_ROUT2, 0x180 | hp_raw);
}

static int whisplay_wm8960_keep_capture_powered(void)
{
	return whisplay_codec_update_bits(WM8960_POWER1,
					  WM8960_CAPTURE_POWER_MASK,
					  WM8960_CAPTURE_POWER_ON);
}

static int whisplay_wm8960_direct_capture_adc(int gain, int cap_raw, int adc_raw)
{
	int boost;
	int ret;

	boost = whisplay_clamp((gain * 3) / 100, 0, 3);
	if (gain > 0 && boost < 1)
		boost = 1;

	ret = whisplay_wm8960_keep_capture_powered();
	if (ret < 0)
		return ret;
	ret = whisplay_codec_update_bits(WM8960_LINPATH, BIT(8) | BIT(7) |
					 BIT(6) | BIT(3) | (3 << 4),
					 BIT(8) | BIT(3) | (boost << 4));
	if (ret < 0)
		return ret;
	ret = whisplay_codec_update_bits(WM8960_RINPATH, BIT(8) | BIT(7) |
					 BIT(6) | BIT(3) | (3 << 4),
					 BIT(8) | BIT(3) | (boost << 4));
	if (ret < 0)
		return ret;
	adc_raw = whisplay_clamp(adc_raw, 0, 255);

	ret = whisplay_codec_write(WM8960_LADC, 0x100 | adc_raw);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_write(WM8960_RADC, 0x100 | adc_raw);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_write(WM8960_LINVOL, 0x140 | cap_raw);
	if (ret < 0)
		return ret;
	return whisplay_codec_write(WM8960_RINVOL, 0x140 | cap_raw);
}

static int whisplay_wm8960_direct_capture(int gain, int cap_raw)
{
	return whisplay_wm8960_direct_capture_adc(gain, cap_raw,
						  WHISPLAY_WM8960_ADC_PCM_VOLUME);
}

static int whisplay_wm8960_prime_capture_muted(void)
{
	int raw;

	raw = whisplay_clamp(whisplay_lut_raw(whisplay_wm8960_capture_lut,
					      whisplay_capture_gain_cache),
			     0, WHISPLAY_WM8960_CAP_MAX);
	return whisplay_wm8960_direct_capture_adc(whisplay_capture_gain_cache,
						  raw, 0);
}

static void whisplay_wm8960_adc_unmute_work_fn(struct work_struct *work)
{
	int i;
	int raw;

	if (whisplay_active_chip != WHISPLAY_CHIP_WM8960 ||
	    !whisplay_codec_component ||
	    !atomic_read(&whisplay_wm8960_capture_active))
		return;

	for (i = 1; i <= WHISPLAY_WM8960_ADC_RAMP_STEPS; i++) {
		if (!atomic_read(&whisplay_wm8960_capture_active))
			return;

		raw = DIV_ROUND_CLOSEST(WHISPLAY_WM8960_ADC_PCM_VOLUME * i,
					WHISPLAY_WM8960_ADC_RAMP_STEPS);
		whisplay_codec_write(WM8960_LADC, 0x100 | raw);
		whisplay_codec_write(WM8960_RADC, 0x100 | raw);

		if (i != WHISPLAY_WM8960_ADC_RAMP_STEPS)
			usleep_range(WHISPLAY_WM8960_ADC_RAMP_US,
				     WHISPLAY_WM8960_ADC_RAMP_US + 500);
	}
}

static void whisplay_wm8960_reprime_work_fn(struct work_struct *work)
{
	int ret;

	if (!whisplay_hide_card || whisplay_active_chip != WHISPLAY_CHIP_WM8960 ||
	    atomic_read(&whisplay_wm8960_capture_active))
		return;

	ret = whisplay_wm8960_prime_capture_muted();
	if (ret < 0)
		dev_warn(whisplay_hide_card->dev,
			 "WM8960 capture re-prime failed: %d\n", ret);
}

static int whisplay_es8389_direct_playback(int raw)
{
	int ret;

	ret = whisplay_codec_write(ES8389_DACL_VOL, raw);
	if (ret < 0)
		return ret;
	return whisplay_codec_write(ES8389_DACR_VOL, raw);
}

static int whisplay_es8389_direct_capture(int gain)
{
	int pga, adc, osr;
	int ret;

	pga = whisplay_clamp(whisplay_lut_raw(whisplay_es8389_pga_lut, gain), 0, 14);
	adc = whisplay_clamp(whisplay_lut_raw(whisplay_es8389_adc_lut, gain), 0, 255);
	osr = whisplay_clamp(whisplay_lut_raw(whisplay_es8389_osr_lut, gain), 0, 255);

	ret = whisplay_codec_update_bits(ES8389_MIC1_GAIN, 0x0f, pga);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_update_bits(ES8389_MIC2_GAIN, 0x0f, pga);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_write(ES8389_ADCL_VOL, adc);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_write(ES8389_ADCR_VOL, adc);
	if (ret < 0)
		return ret;
	ret = whisplay_codec_write(ES8389_OSR_VOL, osr);
	if (ret < 0)
		return ret;
	return whisplay_codec_update_bits(ES8389_ADC_MUTE, 0xc0, 0xc0);
}

/* WM8960: ensure PCM reaches speaker/HP at full digital headroom. */
static int whisplay_wm8960_playback_chain(struct snd_soc_card *card, int hp_raw)
{
	int ret;

	ret = whisplay_kctl_set_int(card, "Playback Volume", 255, 255, true);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int(card, "PCM Playback -6dB Switch", 0, 0, true);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int(card, "Left Output Mixer PCM Playback Switch", 1, 1, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int(card, "Right Output Mixer PCM Playback Switch", 1, 1, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int(card, "Speaker DC Volume", 5, 5, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int(card, "Speaker AC Volume", 5, 5, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int(card, "Headphone Playback Volume", hp_raw, hp_raw, true);
	if (ret < 0)
		return ret;
	return whisplay_kctl_set_int(card, "Speaker Playback Volume", hp_raw, hp_raw, true);
}

/*
 * WM8960 capture: analog boost (LINPUT1 0..3) + ADC digital gain (Capture Volume 0..63)
 * + full PCM ADC headroom.  Previously only Capture Volume was set and LINPUT1
 * stayed at 1, so UI/readback looked stuck and level barely changed.
 */
static int __maybe_unused whisplay_wm8960_capture_chain(struct snd_soc_card *card,
							int gain, int cap_raw)
{
	int boost;
	int ret;

	boost = whisplay_clamp((gain * 3) / 100, 0, 3);
	if (gain > 0 && boost < 1)
		boost = 1;

	ret = whisplay_kctl_set_int(card, "Left Input Boost Mixer LINPUT1 Volume",
				    boost, boost, false);
	if (ret < 0 && ret != -ENOENT)
		return ret;
	ret = whisplay_kctl_set_int(card, "Right Input Boost Mixer RINPUT1 Volume",
				    boost, boost, false);
	if (ret < 0 && ret != -ENOENT)
		return ret;

	ret = whisplay_kctl_set_int(card, "ADC PCM Capture Volume",
				    WHISPLAY_WM8960_ADC_PCM_VOLUME,
				    WHISPLAY_WM8960_ADC_PCM_VOLUME, true);
	if (ret < 0 && ret != -ENOENT)
		return ret;

	return whisplay_kctl_set_int(card, "Capture Volume", cap_raw, cap_raw, true);
}

static int whisplay_lut_raw(const int *lut, int gain)
{
	int raw = lut[gain];

	if (raw < 0)
		raw = 0;
	return raw;
}

/*
 * ES8389 capture: three independent LUTs (PGA / ADC L/R / OSR).
 * Grid-calibrated from Whisplay bench measurements.
 */
static int __maybe_unused whisplay_es8389_capture_apply(struct snd_soc_card *card,
							int gain)
{
	int pga, adc, osr;
	int ret;

	gain = whisplay_clamp(gain, WHISPLAY_GAIN_MIN, WHISPLAY_GAIN_MAX);
	pga = whisplay_clamp(whisplay_lut_raw(whisplay_es8389_pga_lut, gain), 0, 14);
	adc = whisplay_clamp(whisplay_lut_raw(whisplay_es8389_adc_lut, gain), 0, 255);
	osr = whisplay_clamp(whisplay_lut_raw(whisplay_es8389_osr_lut, gain), 0, 255);

	ret = whisplay_kctl_set_int(card, "ADCL PGA Volume", pga, pga, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int(card, "ADCR PGA Volume", pga, pga, false);
	if (ret < 0)
		return ret;

	ret = whisplay_kctl_set_int(card, "ADCL Capture Volume", adc, adc, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int(card, "ADCR Capture Volume", adc, adc, false);
	if (ret < 0)
		return ret;

	ret = whisplay_kctl_set_int(card, "ADC OSR Volume", osr, osr, false);
	if (ret < 0)
		return ret;

	/* Unmute / enable OSR digital volume */
	ret = whisplay_kctl_set_int(card, "ADC OSR Volume ON Switch", 1, 1, true);
	return ret;
}

static int whisplay_apply_playback_gain(struct snd_soc_card *card, int gain)
{
	const int *lut;
	int raw;
	int ret;

	gain = whisplay_clamp(gain, WHISPLAY_GAIN_MIN, WHISPLAY_GAIN_MAX);

	if (whisplay_active_chip == WHISPLAY_CHIP_ES8389) {
		lut = whisplay_es8389_playback_lut;
		raw = whisplay_clamp(whisplay_lut_raw(lut, gain), 0, 255);
		ret = whisplay_es8389_direct_playback(raw);
		if (ret < 0)
			return ret;
		whisplay_playback_gain_cache = gain;
		return 0;
	}

	lut = whisplay_wm8960_playback_lut;
	raw = whisplay_clamp(whisplay_lut_raw(lut, gain), 0, 127);
	ret = whisplay_wm8960_playback_chain(card, raw);
	if (ret == -ENOENT)
		ret = whisplay_wm8960_direct_playback(raw);
	if (ret < 0)
		return ret;
	whisplay_playback_gain_cache = gain;
	return 0;
}

static int whisplay_playback_gain_get(struct snd_kcontrol *kcontrol,
				      struct snd_ctl_elem_value *ucontrol)
{
	ucontrol->value.integer.value[0] =
		whisplay_clamp(whisplay_playback_gain_cache,
			       WHISPLAY_GAIN_MIN, WHISPLAY_GAIN_MAX);
	return 0;
}

static int whisplay_playback_gain_put(struct snd_kcontrol *kcontrol,
				      struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_card *card = snd_kcontrol_chip(kcontrol);
	int gain = ucontrol->value.integer.value[0];
	int ret;

	ret = whisplay_apply_playback_gain(card, gain);
	return ret < 0 ? ret : 1;
}

static int whisplay_capture_gain_get(struct snd_kcontrol *kcontrol,
				     struct snd_ctl_elem_value *ucontrol)
{
	ucontrol->value.integer.value[0] =
		whisplay_clamp(whisplay_capture_gain_cache,
			       WHISPLAY_GAIN_MIN, WHISPLAY_GAIN_MAX);
	return 0;
}

static int whisplay_apply_capture_gain(struct snd_soc_card *card, int gain)
{
	int raw;
	int ret;

	gain = whisplay_clamp(gain, WHISPLAY_GAIN_MIN, WHISPLAY_GAIN_MAX);

	if (whisplay_active_chip == WHISPLAY_CHIP_ES8389)
		ret = whisplay_es8389_direct_capture(gain);
	else {
		raw = whisplay_clamp(whisplay_lut_raw(whisplay_wm8960_capture_lut, gain),
				     0, WHISPLAY_WM8960_CAP_MAX);
		ret = whisplay_wm8960_direct_capture(gain, raw);
	}

	if (ret < 0)
		return ret;
	whisplay_capture_gain_cache = gain;
	return 0;
}

static int whisplay_capture_gain_put(struct snd_kcontrol *kcontrol,
				     struct snd_ctl_elem_value *ucontrol)
{
	struct snd_soc_card *card = snd_kcontrol_chip(kcontrol);
	int gain = ucontrol->value.integer.value[0];
	int ret;

	ret = whisplay_apply_capture_gain(card, gain);
	return ret < 0 ? ret : 1;
}

/* Apply WM8960 mic/playback routes for the Whisplay HAT wiring. */
static int whisplay_wm8960_apply_routing(struct snd_soc_card *card)
{
	int ret;

	ret = whisplay_kctl_set_bool_opt(card, "Capture Switch", true, true);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Capture Volume ZC Switch", true, true);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Headphone Playback ZC Switch", true, true);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Speaker Playback ZC Switch", true, true);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "ADC High Pass Filter Switch", true, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int_opt(card, "ADC Data Output Select", 0, 0, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_int_opt(card, "ALC Function", 0, 0, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Noise Gate Switch", false, false);
	if (ret < 0)
		return ret;

	ret = whisplay_kctl_set_bool_opt(card, "Left Boost Mixer LINPUT1 Switch", true, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Right Boost Mixer RINPUT1 Switch", true, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Left Input Mixer Boost Switch", true, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Right Input Mixer Boost Switch", true, false);
	if (ret < 0)
		return ret;

	ret = whisplay_kctl_set_bool_opt(card, "Left Boost Mixer LINPUT2 Switch", false, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Left Boost Mixer LINPUT3 Switch", false, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Right Boost Mixer RINPUT2 Switch", false, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Right Boost Mixer RINPUT3 Switch", false, false);
	if (ret < 0)
		return ret;

	ret = whisplay_kctl_set_bool(card, "Left Output Mixer PCM Playback Switch", true, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool(card, "Right Output Mixer PCM Playback Switch", true, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Left Output Mixer LINPUT3 Switch", false, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Left Output Mixer Boost Bypass Switch", false, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Right Output Mixer RINPUT3 Switch", false, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Right Output Mixer Boost Bypass Switch", false, false);
	if (ret < 0)
		return ret;
	ret = whisplay_kctl_set_bool_opt(card, "Mono Output Mixer Left Switch", false, false);
	if (ret < 0)
		return ret;
	return whisplay_kctl_set_bool_opt(card, "Mono Output Mixer Right Switch", false, false);
}

static int whisplay_wm8960_apply_defaults(struct snd_soc_card *card)
{
	int ret;

	ret = whisplay_wm8960_apply_routing(card);
	if (ret < 0)
		return ret;
	ret = whisplay_apply_playback_gain(card, WHISPLAY_PLAYBACK_GAIN_DEFAULT);
	if (ret < 0)
		return ret;
	return whisplay_apply_capture_gain(card, WHISPLAY_CAPTURE_GAIN_DEFAULT);
}

static int whisplay_es8389_apply_static_routing(struct snd_soc_card *card)
{
	/* OUTL/OUTR MUX = Normal (enum 0) for headphone playback. */
	return whisplay_kctl_set_int(card, "OUTL MUX", 0, 0, false);
}

static int whisplay_es8389_apply_defaults(struct snd_soc_card *card)
{
	int ret;

	ret = whisplay_es8389_apply_static_routing(card);
	if (ret < 0)
		dev_warn(card->dev, "ES8389 OUT MUX: %d\n", ret);
	ret = whisplay_kctl_set_int(card, "OUTR MUX", 0, 0, false);
	if (ret < 0)
		dev_warn(card->dev, "ES8389 OUTR MUX: %d\n", ret);

	ret = whisplay_apply_playback_gain(card, WHISPLAY_PLAYBACK_GAIN_DEFAULT);
	if (ret < 0)
		return ret;
	return whisplay_apply_capture_gain(card, WHISPLAY_CAPTURE_GAIN_DEFAULT);
}

static int whisplay_apply_boot_defaults(struct snd_soc_card *card)
{
	int ret;

	if (whisplay_active_chip == WHISPLAY_CHIP_ES8389)
		ret = whisplay_es8389_apply_defaults(card);
	else
		ret = whisplay_wm8960_apply_defaults(card);

	if (ret < 0) {
		dev_warn(card->dev, "Boot defaults apply failed: %d\n", ret);
		return ret;
	}

	dev_info(card->dev,
		 "Boot defaults applied (Whisplay playback = %d, capture = %d)\n",
		 WHISPLAY_PLAYBACK_GAIN_DEFAULT, WHISPLAY_CAPTURE_GAIN_DEFAULT);
	return 0;
}

/*
 * Legacy chip mixers are needed briefly while boot routing is applied through
 * codec kcontrols.  After that, remove them from the ALSA card so alsamixer
 * only sees mic and speaker.  Runtime gain changes use direct codec
 * register writes, so these backing kcontrols are no longer needed.
 */
static void whisplay_hide_legacy_controls(struct snd_soc_card *card)
{
	struct snd_card *snd_card = card->snd_card;
	struct snd_kcontrol *kctl;
	struct snd_ctl_elem_id id;
	unsigned int hidden = 0;
	unsigned int kept = 0;
	bool found;

	if (!snd_card)
		return;

	do {
		found = false;
		memset(&id, 0, sizeof(id));

		down_read(&snd_card->controls_rwsem);
		list_for_each_entry(kctl, &snd_card->controls, list) {
			if (kctl->id.iface != SNDRV_CTL_ELEM_IFACE_MIXER)
				continue;
			if (whisplay_is_public_control(kctl->id.name))
				continue;
			id = kctl->id;
			found = true;
			break;
		}
		up_read(&snd_card->controls_rwsem);

		if (found && !snd_ctl_remove_id(snd_card, &id))
			hidden++;
	} while (found);

	down_read(&snd_card->controls_rwsem);
	list_for_each_entry(kctl, &snd_card->controls, list) {
		if (kctl->id.iface == SNDRV_CTL_ELEM_IFACE_MIXER &&
		    whisplay_is_public_control(kctl->id.name))
			kept++;
	}
	up_read(&snd_card->controls_rwsem);

	dev_info(card->dev,
		 "Userspace mixer: %u public controls active, %u legacy controls removed\n",
		 kept, hidden);
}

static const struct snd_kcontrol_new whisplay_unified_gain_controls[] = {
	SOC_SINGLE_EXT(WHISPLAY_SPEAKER_CTL, SND_SOC_NOPM, 0,
		       WHISPLAY_GAIN_MAX, 0,
		       whisplay_playback_gain_get, whisplay_playback_gain_put),
	SOC_SINGLE_EXT(WHISPLAY_MIC_CTL, SND_SOC_NOPM, 0,
		       WHISPLAY_GAIN_MAX, 0,
		       whisplay_capture_gain_get, whisplay_capture_gain_put),
};

/* ===== I2C Probe for ES8389 only (0x10) ===== */

#define WHISPLAY_I2C_MAX_ATTEMPTS 5
#define WHISPLAY_I2C_GAP_US 1000
#define WHISPLAY_I2C_RETRY_US 2000

/*
 * The A733 vendor TWI controller occasionally completes a transfer before
 * its STOP state is fully idle.  ES8389 setup contains dense register bursts,
 * so the next transfer can otherwise fail with BUS error/arbitration lost.
 * Keep the workaround at the regmap bus boundary so codec init, DAPM, mixer
 * controls and PCM hw_params all receive the same whole-transaction retry.
 */
static int whisplay_i2c_regmap_write(void *context, const void *data,
				    size_t count)
{
	struct i2c_client *i2c = context;
	int attempt;
	int ret = -EIO;

	for (attempt = 0; attempt < WHISPLAY_I2C_MAX_ATTEMPTS; attempt++) {
		ret = i2c_master_send(i2c, data, count);
		if (ret == count) {
			usleep_range(WHISPLAY_I2C_GAP_US,
				     WHISPLAY_I2C_GAP_US + 500);
			return 0;
		}
		if (ret >= 0)
			ret = -EIO;
		usleep_range(WHISPLAY_I2C_RETRY_US,
			     WHISPLAY_I2C_RETRY_US + 1000);
	}

	return ret;
}

static int whisplay_i2c_regmap_read(void *context,
				   const void *reg_buf, size_t reg_size,
				   void *val_buf, size_t val_size)
{
	struct i2c_client *i2c = context;
	struct i2c_msg msgs[2] = {
		{
			.addr = i2c->addr,
			.flags = i2c->flags,
			.len = reg_size,
			.buf = (void *)reg_buf,
		},
		{
			.addr = i2c->addr,
			.flags = i2c->flags | I2C_M_RD,
			.len = val_size,
			.buf = val_buf,
		},
	};
	int attempt;
	int ret = -EIO;

	for (attempt = 0; attempt < WHISPLAY_I2C_MAX_ATTEMPTS; attempt++) {
		ret = i2c_transfer(i2c->adapter, msgs, ARRAY_SIZE(msgs));
		if (ret == ARRAY_SIZE(msgs)) {
			usleep_range(WHISPLAY_I2C_GAP_US,
				     WHISPLAY_I2C_GAP_US + 500);
			return 0;
		}
		if (ret >= 0)
			ret = -EIO;
		usleep_range(WHISPLAY_I2C_RETRY_US,
			     WHISPLAY_I2C_RETRY_US + 1000);
	}

	return ret;
}

static const struct regmap_bus whisplay_es8389_regmap_bus = {
	.write = whisplay_i2c_regmap_write,
	.read = whisplay_i2c_regmap_read,
	.reg_format_endian_default = REGMAP_ENDIAN_BIG,
	.val_format_endian_default = REGMAP_ENDIAN_BIG,
};

static int whisplay_i2c_probe(struct i2c_client *i2c)
{
	struct whisplay_priv *priv;
	struct regmap *regmap;
	unsigned int val;
	int ret;

	if (i2c->addr != 0x10)
		return -ENODEV;

	dev_info(&i2c->dev, "Whisplay probing ES8389 at 0x10\n");

	priv = devm_kzalloc(&i2c->dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->i2c = i2c;
	priv->codec_of_node = i2c->dev.of_node;
	i2c_set_clientdata(i2c, priv);

	if (device_property_read_bool(&i2c->dev,
				      "whisplay,a733-i2c-retry"))
		regmap = devm_regmap_init(&i2c->dev,
					 &whisplay_es8389_regmap_bus,
					 i2c, &es8389_regmap_config);
	else
		regmap = devm_regmap_init_i2c(i2c, &es8389_regmap_config);
	if (IS_ERR(regmap))
		return PTR_ERR(regmap);

	priv->regmap = regmap;

	/* Try read; on Pi5 RP1 I2C, reads may fail before init */
	ret = regmap_read(regmap, ES8389_CHIP_ID0, &val);
	if (ret < 0) {
		/* Fallback: try a write to verify chip presence */
		ret = regmap_write(regmap, ES8389_ISO_CTL, 0x00);
		if (ret < 0) {
			dev_dbg(&i2c->dev, "ES8389 not present at 0x10\n");
			return -ENODEV;
		}
		dev_info(&i2c->dev, "Detected ES8389 at 0x10 (write OK)\n");
	} else if (val != 0xFF && val != 0x00) {
		dev_info(&i2c->dev, "Detected ES8389 at 0x10 (CHIP_ID0=0x%02x)\n", val);
	} else {
		dev_dbg(&i2c->dev, "No ES8389 at 0x10\n");
		return -ENODEV;
	}

	priv->chip = WHISPLAY_CHIP_ES8389;

	ret = devm_snd_soc_register_component(&i2c->dev, &es8389_component_driver,
					      &es8389_dai, 1);
	if (ret < 0) {
		dev_err(&i2c->dev, "Failed to register ES8389: %d\n", ret);
		return ret;
	}

	mutex_lock(&whisplay_lock);
	whisplay_active_chip = WHISPLAY_CHIP_ES8389;
	whisplay_codec_np = i2c->dev.of_node;
	mutex_unlock(&whisplay_lock);

	dev_info(&i2c->dev, "Whisplay ES8389 ready\n");
	return 0;
}

#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 3, 0)
static int whisplay_i2c_probe_legacy(struct i2c_client *i2c,
				     const struct i2c_device_id *id)
{
	return whisplay_i2c_probe(i2c);
}
#endif

#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 1, 0)
static int whisplay_i2c_remove(struct i2c_client *i2c)
#else
static void whisplay_i2c_remove(struct i2c_client *i2c)
#endif
{
	mutex_lock(&whisplay_lock);
	whisplay_active_chip = WHISPLAY_CHIP_UNKNOWN;
	whisplay_codec_np = NULL;
	mutex_unlock(&whisplay_lock);

#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 1, 0)
	return 0;
#endif
}

static const struct of_device_id whisplay_i2c_of_match[] = {
	{ .compatible = "pisugar,whisplay", },
	{ }
};
MODULE_DEVICE_TABLE(of, whisplay_i2c_of_match);

static const struct i2c_device_id whisplay_i2c_id[] = {
	{ "whisplay", 0 },
	{ }
};
MODULE_DEVICE_TABLE(i2c, whisplay_i2c_id);

static struct i2c_driver whisplay_i2c_driver = {
	.driver = {
		.name = "whisplay",
		.of_match_table = of_match_ptr(whisplay_i2c_of_match),
	},
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 3, 0)
	.probe = whisplay_i2c_probe_legacy,
#else
	.probe = whisplay_i2c_probe,
#endif
	.remove = whisplay_i2c_remove,
	.id_table = whisplay_i2c_id,
};

/* ===== WM8960 Chip Detection ===== */

/*
 * Use SMBus QUICK transaction (address-only, no data) to check for ACK.
 * This is non-destructive: it does NOT issue a write to any register so
 * the wm8960 driver (if loaded first) is undisturbed.
 *
 * Returns true if a device ACKs at 0x1a on the given adapter.
 */
static bool whisplay_wm8960_present(struct i2c_adapter *adap)
{
	union i2c_smbus_data dummy = {};
	int ret;

	if (!i2c_check_functionality(adap, I2C_FUNC_SMBUS_QUICK))
		return false;

	ret = i2c_smbus_xfer(adap, 0x1a, 0, I2C_SMBUS_WRITE, 0,
			     I2C_SMBUS_QUICK, &dummy);
	return ret >= 0;
}

/*
 * Locate the i2c adapter that holds the WM8960 node, given the codec
 * device tree node. Returns NULL if not bound yet.
 */
static struct i2c_adapter *whisplay_get_codec_adapter(struct device_node *codec_np)
{
	struct device_node *parent;
	struct i2c_adapter *adap;

	if (!codec_np)
		return NULL;

	parent = of_get_parent(codec_np);
	if (!parent)
		return NULL;

	adap = of_get_i2c_adapter_by_node(parent);
	of_node_put(parent);
	return adap;
}

/*
 * WM8960 registers are write-only, so probing the address directly is not
 * reliable on every controller (A733 sunxi-twi, in particular, times out on
 * zero-length transfers).  Let the upstream codec driver perform its reset
 * write and treat a successfully bound device as positive detection.
 */
static bool whisplay_wm8960_bound(struct device_node *codec_np)
{
	struct i2c_client *client;
	bool bound;

	if (!codec_np)
		return false;

	client = of_find_i2c_device_by_node(codec_np);
	if (!client)
		return false;

	/* device_is_bound() is not exported by the A733 5.15 vendor kernel. */
	bound = client->dev.driver != NULL;
	put_device(&client->dev);
	return bound;
}

/* ===== Codec Default Gain Calibration =====
 *
 * Goal: after boot, both chips should produce roughly the same SPL on
 * headphone-out for a given digital signal, and the same capture level
 * for the same acoustic input. End-users may still fine-tune via amixer
 * or a saved ALSA state file.
 *
 * Boot defaults: whisplay_apply_boot_defaults() in late_probe.
 * ES8389 regmap init remains in es8389_chip_init(); levels follow the LUT.
 */

/* ===== Platform Machine Driver ===== */

/*
 * Machine-level DAPM widgets and routes.
 * These connect external jacks to the codec's analog pins, completing
 * the DAPM audio path so the framework can power up the codec properly.
 * Mirrors what simple-audio-card provides in the reference ES8389 DTS.
 */
static const struct snd_soc_dapm_widget whisplay_es8389_widgets[] = {
	SND_SOC_DAPM_MIC("Mic Jack", NULL),
	SND_SOC_DAPM_HP("Headphone Jack", NULL),
};

static const struct snd_soc_dapm_route whisplay_es8389_routes[] = {
	{ "Headphone Jack", NULL, "HPOL" },
	{ "Headphone Jack", NULL, "HPOR" },
	{ "INPUT1", NULL, "Mic Jack" },
	{ "INPUT2", NULL, "Mic Jack" },
};

static const struct snd_soc_dapm_widget whisplay_wm8960_widgets[] = {
	SND_SOC_DAPM_MIC("Mic Jack", NULL),
	SND_SOC_DAPM_HP("Headphone Jack", NULL),
	SND_SOC_DAPM_SPK("Ext Spk", NULL),
};

static const struct snd_soc_dapm_route whisplay_wm8960_routes[] = {
	{ "Headphone Jack", NULL, "HP_L" },
	{ "Headphone Jack", NULL, "HP_R" },
	{ "Ext Spk", NULL, "SPK_LP" },
	{ "Ext Spk", NULL, "SPK_LN" },
	{ "Ext Spk", NULL, "SPK_RP" },
	{ "Ext Spk", NULL, "SPK_RN" },
	{ "Mic Jack", NULL, "MICB" },
	{ "LINPUT1", NULL, "Mic Jack" },
	{ "RINPUT1", NULL, "Mic Jack" },
};

static int whisplay_dai_init(struct snd_soc_pcm_runtime *rtd)
{
	struct snd_soc_dai *codec_dai = snd_soc_rtd_to_codec(rtd, 0);
	unsigned int mclk_rate;
	u32 configured_rate;
	int ret;

	whisplay_codec_component = codec_dai->component;

	if (whisplay_active_chip == WHISPLAY_CHIP_WM8960) {
		if (!of_property_read_u32(rtd->card->dev->of_node,
					  "whisplay,wm8960-sysclk",
					  &configured_rate))
			mclk_rate = configured_rate;
		else
			mclk_rate = 24000000;
	} else if (!of_property_read_u32(rtd->card->dev->of_node,
					 "whisplay,es8389-sysclk",
					 &configured_rate)) {
		mclk_rate = configured_rate;
	} else {
		mclk_rate = 24576000;
	}

	ret = snd_soc_dai_set_sysclk(codec_dai, 0, mclk_rate,
				      SND_SOC_CLOCK_IN);
	if (ret && ret != -ENOTSUPP)
		dev_warn(rtd->dev, "Failed to set codec sysclk: %d\n", ret);

	return 0;
}

static int whisplay_dai_startup(struct snd_pcm_substream *substream)
{
	struct snd_soc_pcm_runtime *rtd = whisplay_substream_to_rtd(substream);

	/*
	 * The A733 BSP I2S path is validated at 48 kHz.  Constraining ALSA here
	 * also prevents PipeWire/PulseAudio from repeatedly probing a clock
	 * setup that the vendor driver cannot service safely.
	 */
	if (of_property_read_bool(rtd->card->dev->of_node,
				  "whisplay,force-48k"))
		return snd_pcm_hw_constraint_single(substream->runtime,
						    SNDRV_PCM_HW_PARAM_RATE,
						    WHISPLAY_A733_RATE);

	return 0;
}

static int whisplay_dai_prepare(struct snd_pcm_substream *substream)
{
	int ret;

	if (whisplay_active_chip != WHISPLAY_CHIP_WM8960 ||
	    substream->stream != SNDRV_PCM_STREAM_CAPTURE)
		return 0;

	if (whisplay_wm8960_adc_unmute_dwork_inited)
		cancel_delayed_work_sync(&whisplay_wm8960_adc_unmute_dwork);
	if (whisplay_wm8960_reprime_dwork_inited)
		cancel_delayed_work_sync(&whisplay_wm8960_reprime_dwork);

	atomic_set(&whisplay_wm8960_capture_active, 0);
	ret = whisplay_wm8960_prime_capture_muted();
	if (ret < 0)
		return ret;

	return 0;
}

/*
 * The A733 BSP carries I2S's one-bit data delay outside the standard
 * snd_soc_dai_set_fmt() API.  Its stock machine driver sends a private
 * snd_sunxi DAI_UCFMT notifier with data_late=1; an external machine driver
 * cannot use that private header, and set_fmt(I2S) otherwise leaves every
 * TX/RX offset at zero.  The result is a one-bit-early capture which drops
 * the sign bit (samples alternate between zero and positive full scale).
 *
 * Program the same TX_OFFSET/RX_OFFSET fields used by the vendor notifier.
 * This is deliberately gated by whisplay,sunxi-a733-i2s and is applied after
 * every set_fmt(), since the vendor callback rewrites these fields there.
 */
static int whisplay_a733_set_i2s_data_delay(struct snd_soc_dai *cpu_dai)
{
	static const unsigned int channel_select_regs[] = {
		WHISPLAY_A733_I2S_TX0CHSEL,
		WHISPLAY_A733_I2S_TX1CHSEL,
		WHISPLAY_A733_I2S_TX2CHSEL,
		WHISPLAY_A733_I2S_TX3CHSEL,
		WHISPLAY_A733_I2S_RXCHSEL,
	};
	struct regmap *regmap;
	int i;
	int ret;

	regmap = dev_get_regmap(cpu_dai->dev, NULL);
	if (!regmap)
		return -ENODEV;

	for (i = 0; i < ARRAY_SIZE(channel_select_regs); i++) {
		ret = regmap_update_bits(regmap, channel_select_regs[i],
					 WHISPLAY_A733_I2S_DATA_DELAY_MASK,
					 WHISPLAY_A733_I2S_DATA_DELAY_I2S);
		if (ret)
			return ret;
	}

	return 0;
}

static int whisplay_dai_hw_params(struct snd_pcm_substream *substream,
				  struct snd_pcm_hw_params *params)
{
	struct snd_soc_pcm_runtime *rtd = whisplay_substream_to_rtd(substream);
	struct snd_soc_dai *cpu_dai = snd_soc_rtd_to_cpu(rtd, 0);
	struct snd_soc_dai *codec_dai = snd_soc_rtd_to_codec(rtd, 0);
	const char *mclk_fs_property;
	unsigned int bclk_ratio;
	unsigned int rate;
	unsigned int sysclk;
	u32 mclk_fs;
	bool sunxi_a733;
	int ret;

	sunxi_a733 = of_property_read_bool(rtd->card->dev->of_node,
					  "whisplay,sunxi-a733-i2s");
	rate = params_rate(params);

	if (whisplay_active_chip == WHISPLAY_CHIP_WM8960)
		mclk_fs_property = "whisplay,wm8960-mclk-fs";
	else if (whisplay_active_chip == WHISPLAY_CHIP_ES8389)
		mclk_fs_property = "whisplay,es8389-mclk-fs";
	else
		return 0;

	if (of_property_read_u32(rtd->card->dev->of_node, mclk_fs_property,
				 &mclk_fs))
		return 0;

	sysclk = rate * mclk_fs;

	/*
	 * Allwinner's A733 BSP CPU DAI does not derive its PLL implicitly.
	 * Its official machine driver programs PLL, MCLK, BCLK and TDM in this
	 * order.  Calling set_sysclk() without set_pll() leaves pllclk_freq at
	 * zero; the following CPU hw_params then fails in a tight userspace
	 * retry loop and can eventually crash the vendor kernel.
	 */
	if (sunxi_a733) {
		if (rate != WHISPLAY_A733_RATE)
			return -EINVAL;

		ret = snd_soc_dai_set_pll(cpu_dai, substream->stream, 0,
					  WHISPLAY_A733_PLL_RATE,
					  WHISPLAY_A733_PLL_RATE);
		if (ret) {
			dev_err(rtd->dev, "Failed to set A733 CPU PLL: %d\n", ret);
			return ret;
		}
	}

	ret = snd_soc_dai_set_sysclk(cpu_dai, 0, sysclk, SND_SOC_CLOCK_OUT);
	if (ret && ret != -ENOTSUPP) {
		dev_warn(rtd->dev, "Failed to set CPU sysclk %u: %d\n",
			 sysclk, ret);
		if (sunxi_a733)
			return ret;
	}

	ret = snd_soc_dai_set_sysclk(codec_dai, 0, sysclk, SND_SOC_CLOCK_IN);
	if (ret && ret != -ENOTSUPP) {
		dev_warn(rtd->dev, "Failed to set codec sysclk %u: %d\n",
			 sysclk, ret);
		if (sunxi_a733)
			return ret;
	}

	if (!sunxi_a733)
		return 0;

	bclk_ratio = WHISPLAY_A733_PLL_RATE /
		     (rate * WHISPLAY_A733_SLOTS * WHISPLAY_A733_SLOT_WIDTH);
	ret = snd_soc_dai_set_bclk_ratio(cpu_dai, bclk_ratio);
	if (ret && ret != -ENOTSUPP) {
		dev_err(rtd->dev, "Failed to set A733 BCLK ratio %u: %d\n",
			bclk_ratio, ret);
		return ret;
	}

	ret = snd_soc_dai_set_fmt(cpu_dai, rtd->dai_link->dai_fmt);
	if (ret && ret != -ENOTSUPP) {
		dev_err(rtd->dev, "Failed to set A733 CPU DAI format: %d\n",
			ret);
		return ret;
	}

	ret = whisplay_a733_set_i2s_data_delay(cpu_dai);
	if (ret) {
		dev_err(rtd->dev, "Failed to set A733 I2S data delay: %d\n",
			ret);
		return ret;
	}

	ret = snd_soc_dai_set_tdm_slot(cpu_dai, 0, 0,
				       WHISPLAY_A733_SLOTS,
				       WHISPLAY_A733_SLOT_WIDTH);
	if (ret && ret != -ENOTSUPP) {
		dev_err(rtd->dev, "Failed to set A733 TDM slots: %d\n", ret);
		return ret;
	}

	return 0;
}

static int whisplay_dai_trigger(struct snd_pcm_substream *substream, int cmd)
{
	if (whisplay_active_chip != WHISPLAY_CHIP_WM8960 ||
	    substream->stream != SNDRV_PCM_STREAM_CAPTURE)
		return 0;

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
	case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
		atomic_set(&whisplay_wm8960_capture_active, 1);
		if (whisplay_wm8960_adc_unmute_dwork_inited)
			schedule_delayed_work(&whisplay_wm8960_adc_unmute_dwork,
					      msecs_to_jiffies(WHISPLAY_WM8960_ADC_STARTUP_MUTE_MS));
		break;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
	case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
		atomic_set(&whisplay_wm8960_capture_active, 0);
		if (whisplay_wm8960_adc_unmute_dwork_inited)
			cancel_delayed_work(&whisplay_wm8960_adc_unmute_dwork);
		if (whisplay_wm8960_reprime_dwork_inited)
			schedule_delayed_work(&whisplay_wm8960_reprime_dwork,
					      msecs_to_jiffies(50));
		break;
	default:
		break;
	}

	return 0;
}

static const struct snd_soc_ops whisplay_dai_ops = {
	.startup = whisplay_dai_startup,
	.hw_params = whisplay_dai_hw_params,
	.prepare = whisplay_dai_prepare,
	.trigger = whisplay_dai_trigger,
};

static int whisplay_card_late_probe(struct snd_soc_card *card)
{
	int ret;

	strscpy(card->snd_card->id, WHISPLAY_CARD_ID,
		sizeof(card->snd_card->id));
	strscpy(card->snd_card->shortname, WHISPLAY_CARD_DISPLAY_NAME,
		sizeof(card->snd_card->shortname));
	strscpy(card->snd_card->longname, WHISPLAY_CARD_DISPLAY_NAME,
		sizeof(card->snd_card->longname));

	ret = snd_soc_add_card_controls(card, whisplay_unified_gain_controls,
					ARRAY_SIZE(whisplay_unified_gain_controls));
	if (ret < 0)
		dev_warn(card->dev, "Failed to add unified gain controls: %d\n", ret);

	whisplay_hide_card = card;

	if (!whisplay_boot_dwork_inited) {
		INIT_DELAYED_WORK(&whisplay_boot_dwork, whisplay_boot_work_fn);
		whisplay_boot_dwork_inited = true;
	}
	schedule_delayed_work(&whisplay_boot_dwork, msecs_to_jiffies(WHISPLAY_BOOT_DELAY_MS));

	if (!whisplay_wm8960_reprime_dwork_inited) {
		INIT_DELAYED_WORK(&whisplay_wm8960_reprime_dwork,
				  whisplay_wm8960_reprime_work_fn);
		whisplay_wm8960_reprime_dwork_inited = true;
	}
	if (!whisplay_wm8960_adc_unmute_dwork_inited) {
		INIT_DELAYED_WORK(&whisplay_wm8960_adc_unmute_dwork,
				  whisplay_wm8960_adc_unmute_work_fn);
		whisplay_wm8960_adc_unmute_dwork_inited = true;
	}

	if (!skip_legacy_hide) {
		if (!whisplay_hide_dwork_inited) {
			INIT_DELAYED_WORK(&whisplay_hide_dwork, whisplay_hide_work_fn);
			whisplay_hide_dwork_inited = true;
		}
		/* Remove legacy controls after boot defaults have used them for routing. */
		schedule_delayed_work(&whisplay_hide_dwork,
				      msecs_to_jiffies(WHISPLAY_BOOT_DELAY_MS +
						       WHISPLAY_HIDE_RETRY_MS));
	}

	return 0;
}

static int whisplay_card_probe(struct platform_device *pdev)
{
	struct device_node *cpu_np, *codec_np = NULL, *wm_np = NULL;
	struct snd_soc_dai_link_component *codecs;
	struct snd_soc_dai_link_component *cpus;
	struct snd_soc_dai_link_component *platforms;
	struct snd_soc_dai_link *dai_link;
	struct snd_soc_card *card;
	struct i2c_adapter *adap;
	struct device *dev = &pdev->dev;
	int chip;
	int ret;

	dev_info(dev, "Whisplay sound card probe\n");

	cpu_np = of_parse_phandle(dev->of_node, "i2s-controller", 0);
	if (!cpu_np) {
		dev_err(dev, "Missing 'i2s-controller' phandle\n");
		return -EINVAL;
	}

	mutex_lock(&whisplay_lock);
	chip = whisplay_active_chip;
	mutex_unlock(&whisplay_lock);

	if (chip == WHISPLAY_CHIP_UNKNOWN) {
		wm_np = of_parse_phandle(dev->of_node,
					 "whisplay,wm8960-codec", 0);
		if (of_property_read_bool(dev->of_node,
					  "whisplay,wm8960-bound-detect")) {
			if (whisplay_wm8960_bound(wm_np)) {
				chip = WHISPLAY_CHIP_WM8960;
				dev_info(dev,
					 "Detected bound WM8960 codec at 0x1a\n");
				mutex_lock(&whisplay_lock);
				whisplay_active_chip = WHISPLAY_CHIP_WM8960;
				mutex_unlock(&whisplay_lock);
			}
		} else {
			adap = whisplay_get_codec_adapter(wm_np);
			if (!adap)
				adap = i2c_get_adapter(1);
			if (!adap) {
				of_node_put(wm_np);
				of_node_put(cpu_np);
				return -EPROBE_DEFER;
			}

			if (whisplay_wm8960_present(adap)) {
				chip = WHISPLAY_CHIP_WM8960;
				dev_info(dev, "Detected WM8960 at 0x1a on %s\n",
					 adap->name);
				mutex_lock(&whisplay_lock);
				whisplay_active_chip = WHISPLAY_CHIP_WM8960;
				mutex_unlock(&whisplay_lock);
			}
			i2c_put_adapter(adap);
		}

		if (chip == WHISPLAY_CHIP_UNKNOWN) {
			of_node_put(wm_np);
			of_node_put(cpu_np);
			dev_dbg(dev, "No chip detected yet, deferring\n");
			return -EPROBE_DEFER;
		}
	}

	if (chip == WHISPLAY_CHIP_WM8960) {
		if (!wm_np)
			wm_np = of_parse_phandle(dev->of_node,
						 "whisplay,wm8960-codec", 0);
		codec_np = wm_np;
		if (!codec_np)
			codec_np = of_find_compatible_node(NULL, NULL,
							   "wlf,wm8960");
	} else if (chip == WHISPLAY_CHIP_ES8389) {
		mutex_lock(&whisplay_lock);
		codec_np = of_node_get(whisplay_codec_np);
		mutex_unlock(&whisplay_lock);
		if (!codec_np)
			codec_np = of_parse_phandle(dev->of_node,
						    "whisplay,es8389-codec", 0);
	}

	if (!codec_np) {
		of_node_put(cpu_np);
		return (chip == WHISPLAY_CHIP_WM8960) ? -EPROBE_DEFER : -ENODEV;
	}

	codecs = devm_kzalloc(dev, sizeof(*codecs), GFP_KERNEL);
	cpus = devm_kzalloc(dev, sizeof(*cpus), GFP_KERNEL);
	platforms = devm_kzalloc(dev, sizeof(*platforms), GFP_KERNEL);
	dai_link = devm_kzalloc(dev, sizeof(*dai_link), GFP_KERNEL);
	card = devm_kzalloc(dev, sizeof(*card), GFP_KERNEL);
	if (!codecs || !cpus || !platforms || !dai_link || !card) {
		of_node_put(cpu_np);
		of_node_put(codec_np);
		return -ENOMEM;
	}

	cpus->of_node = cpu_np;
	cpus->dai_name = NULL;
	platforms->of_node = cpu_np;

	if (chip == WHISPLAY_CHIP_WM8960) {
		codecs->of_node = codec_np;
		codecs->dai_name = "wm8960-hifi";
	} else {
		codecs->of_node = codec_np;
		codecs->dai_name = "ES8389 HiFi";
	}

	dai_link->name = "whisplay";
	dai_link->stream_name = "Whisplay HiFi";
	dai_link->codecs = codecs;
	dai_link->num_codecs = 1;
	dai_link->cpus = cpus;
	dai_link->num_cpus = 1;
	dai_link->platforms = platforms;
	dai_link->num_platforms = 1;
	dai_link->dai_fmt = SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_NB_NF;
	if (chip == WHISPLAY_CHIP_ES8389 &&
	    of_property_read_bool(dev->of_node, "whisplay,es8389-codec-master"))
		dai_link->dai_fmt |= SND_SOC_DAIFMT_CBC_CFP;
	else
		dai_link->dai_fmt |= SND_SOC_DAIFMT_CBC_CFC;
#ifdef SND_SOC_DAIFMT_CONT
	if (!of_property_read_bool(dev->of_node, "whisplay,no-continuous-clock"))
		dai_link->dai_fmt |= SND_SOC_DAIFMT_CONT;
#endif
	dai_link->init = whisplay_dai_init;
	dai_link->ops = &whisplay_dai_ops;

	card->name = WHISPLAY_CARD_ID;
	card->long_name = WHISPLAY_CARD_DISPLAY_NAME;
	card->driver_name = WHISPLAY_CARD_ID;
	card->dev = dev;
	card->owner = THIS_MODULE;
	card->dai_link = dai_link;
	card->num_links = 1;
	card->fully_routed = true;
	card->late_probe = whisplay_card_late_probe;

	if (chip == WHISPLAY_CHIP_ES8389) {
		card->dapm_widgets = whisplay_es8389_widgets;
		card->num_dapm_widgets = ARRAY_SIZE(whisplay_es8389_widgets);
		card->dapm_routes = whisplay_es8389_routes;
		card->num_dapm_routes = ARRAY_SIZE(whisplay_es8389_routes);
	} else {
		card->dapm_widgets = whisplay_wm8960_widgets;
		card->num_dapm_widgets = ARRAY_SIZE(whisplay_wm8960_widgets);
		card->dapm_routes = whisplay_wm8960_routes;
		card->num_dapm_routes = ARRAY_SIZE(whisplay_wm8960_routes);
	}

	dev_set_drvdata(dev, card);

	ret = devm_snd_soc_register_card(dev, card);

	of_node_put(cpu_np);
	of_node_put(codec_np);

	if (ret < 0) {
		if (ret != -EPROBE_DEFER)
			dev_err(dev, "Failed to register sound card: %d\n", ret);
		return ret;
	}

	dev_info(dev, "Whisplay '%s' registered (chip=%s)\n",
		 card->name,
		 chip == WHISPLAY_CHIP_ES8389 ? "ES8389" : "WM8960");

	return 0;
}

static const struct of_device_id whisplay_card_of_match[] = {
	{ .compatible = "pisugar,whisplay-soundcard", },
	{ }
};
MODULE_DEVICE_TABLE(of, whisplay_card_of_match);

static struct platform_driver whisplay_card_driver = {
	.driver = {
		.name = "whisplay-soundcard",
		.of_match_table = of_match_ptr(whisplay_card_of_match),
	},
	.probe = whisplay_card_probe,
};

/* ===== Module Init ===== */

static int __init whisplay_init(void)
{
	int ret;

	ret = i2c_add_driver(&whisplay_i2c_driver);
	if (ret) {
		pr_err("whisplay: I2C driver failed: %d\n", ret);
		return ret;
	}

	ret = platform_driver_register(&whisplay_card_driver);
	if (ret) {
		pr_err("whisplay: platform driver failed: %d\n", ret);
		i2c_del_driver(&whisplay_i2c_driver);
		return ret;
	}

	return 0;
}

static void __exit whisplay_exit(void)
{
	cancel_delayed_work_sync(&whisplay_boot_dwork);
	cancel_delayed_work_sync(&whisplay_hide_dwork);
	cancel_delayed_work_sync(&whisplay_wm8960_reprime_dwork);
	cancel_delayed_work_sync(&whisplay_wm8960_adc_unmute_dwork);
	platform_driver_unregister(&whisplay_card_driver);
	i2c_del_driver(&whisplay_i2c_driver);
}

module_init(whisplay_init);
module_exit(whisplay_exit);

MODULE_DESCRIPTION("Whisplay unified sound card driver (ES8389 / WM8960)");
MODULE_AUTHOR("PiSugar");
MODULE_LICENSE("GPL");
