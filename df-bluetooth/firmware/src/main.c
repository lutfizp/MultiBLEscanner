/*
 * nRF52840 Dongle BLE RSSI scanner.
 * Emits one JSON object per line over USB CDC-ACM for the Python host.
 *
 * Uses ACTIVE scan so SCAN_RSP (where Local Name often lives) is received.
 */

#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/byteorder.h>

#define THROTTLE_MS        200
#define NAME_MAX_LEN       28
#define TRACK_SLOTS        64
#define LED_BLINK_MS       500

struct track_entry {
	bt_addr_le_t addr;
	int64_t last_ms;
	bool used;
	bool has_name;
};

static struct track_entry tracks[TRACK_SLOTS];
static const struct gpio_dt_spec led =
	GPIO_DT_SPEC_GET_OR(DT_ALIAS(led0), gpios, {0});
static bool led_ready;

struct ad_ctx {
	char name[NAME_MAX_LEN + 1];
	bool has_cid;
	uint16_t cid;
};

/* Allow send if throttle expired, OR name just appeared for the first time. */
static bool track_allow(const bt_addr_le_t *addr, bool has_name)
{
	int64_t now = k_uptime_get();
	int free_idx = -1;

	for (int i = 0; i < TRACK_SLOTS; i++) {
		if (!tracks[i].used) {
			if (free_idx < 0) {
				free_idx = i;
			}
			continue;
		}
		if (bt_addr_le_eq(&tracks[i].addr, addr)) {
			bool name_first_time = has_name && !tracks[i].has_name;
			if (!name_first_time && (now - tracks[i].last_ms) < THROTTLE_MS) {
				return false;
			}
			tracks[i].last_ms = now;
			if (has_name) {
				tracks[i].has_name = true;
			}
			return true;
		}
	}

	if (free_idx < 0) {
		int64_t oldest = LLONG_MAX;
		free_idx = 0;
		for (int i = 0; i < TRACK_SLOTS; i++) {
			if (tracks[i].last_ms < oldest) {
				oldest = tracks[i].last_ms;
				free_idx = i;
			}
		}
	}

	tracks[free_idx].used = true;
	tracks[free_idx].addr = *addr;
	tracks[free_idx].last_ms = now;
	tracks[free_idx].has_name = has_name;
	return true;
}

static bool ad_parse(struct bt_data *data, void *user_data)
{
	struct ad_ctx *ctx = user_data;

	if (data->type == BT_DATA_NAME_COMPLETE ||
	    data->type == BT_DATA_NAME_SHORTENED) {
		size_t len = MIN(data->data_len, (uint8_t)NAME_MAX_LEN);
		memcpy(ctx->name, data->data, len);
		ctx->name[len] = '\0';
		return true; /* keep parsing for CID */
	}

	if (data->type == BT_DATA_MANUFACTURER_DATA && data->data_len >= 2 &&
	    !ctx->has_cid) {
		ctx->cid = sys_get_le16(data->data);
		ctx->has_cid = true;
	}

	return true;
}

static void json_escape_name(const char *in, char *out, size_t out_len)
{
	size_t j = 0;

	for (size_t i = 0; in[i] != '\0' && j + 2 < out_len; i++) {
		char c = in[i];
		if (c == '"' || c == '\\') {
			out[j++] = '\\';
			out[j++] = c;
		} else if ((unsigned char)c < 0x20) {
			continue;
		} else {
			out[j++] = c;
		}
	}
	out[j] = '\0';
}

static const char *addr_type_str(uint8_t type)
{
	switch (type) {
	case BT_ADDR_LE_PUBLIC:
		return "public";
	case BT_ADDR_LE_RANDOM:
		return "random";
	default:
		return "unknown";
	}
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	char addr_str[BT_ADDR_STR_LEN];
	struct ad_ctx actx = {0};
	char name_esc[NAME_MAX_LEN * 2 + 1] = {0};
	bool has_name;

	ARG_UNUSED(type);

	bt_data_parse(ad, ad_parse, &actx);
	has_name = (actx.name[0] != '\0');

	if (!track_allow(addr, has_name)) {
		return;
	}

	bt_addr_to_str(&addr->a, addr_str, sizeof(addr_str));
	json_escape_name(actx.name, name_esc, sizeof(name_esc));

	if (actx.has_cid) {
		printk("{\"t\":\"adv\",\"addr\":\"%s\",\"type\":\"%s\",\"rssi\":%d,\"name\":\"%s\",\"cid\":%u,\"at\":%lld}\n",
		       addr_str, addr_type_str(addr->type), (int)rssi, name_esc,
		       (unsigned)actx.cid, (long long)k_uptime_get());
	} else {
		printk("{\"t\":\"adv\",\"addr\":\"%s\",\"type\":\"%s\",\"rssi\":%d,\"name\":\"%s\",\"at\":%lld}\n",
		       addr_str, addr_type_str(addr->type), (int)rssi, name_esc,
		       (long long)k_uptime_get());
	}
}

static void led_blink_work(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(led_work, led_blink_work);

static void led_blink_work(struct k_work *work)
{
	ARG_UNUSED(work);
	static bool on;

	if (led_ready) {
		on = !on;
		gpio_pin_set_dt(&led, on);
	}
	k_work_schedule(&led_work, K_MSEC(LED_BLINK_MS));
}

static int led_init(void)
{
	if (!device_is_ready(led.port)) {
		return -ENODEV;
	}
	gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
	led_ready = true;
	k_work_schedule(&led_work, K_NO_WAIT);
	return 0;
}

int main(void)
{
	int err;
	struct bt_le_scan_param scan_param = {
		.type = BT_LE_SCAN_TYPE_ACTIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window = BT_GAP_SCAN_FAST_WINDOW,
	};

	k_msleep(1500);

	led_init();

	err = bt_enable(NULL);
	if (err) {
		printk("{\"t\":\"error\",\"msg\":\"bt_enable %d\"}\n", err);
		return 0;
	}

	err = bt_le_scan_start(&scan_param, device_found);
	if (err) {
		printk("{\"t\":\"error\",\"msg\":\"scan_start %d\"}\n", err);
		return 0;
	}

	printk("{\"t\":\"status\",\"scanning\":true,\"active\":true}\n");

	while (1) {
		k_sleep(K_FOREVER);
	}

	return 0;
}
