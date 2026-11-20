/* ESP32-S3 USB Device <-> Host role control for MicroPython. */
#include <string.h>
#include "py/obj.h"
#include "py/runtime.h"
#include "py/mphal.h"
#include "esp_err.h"
#include "esp_intr_alloc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "usb/usb_host.h"
#include "usb/usb_types_ch9.h"
#include "tusb.h"
#include "mp_usbd.h"

#if CONFIG_IDF_TARGET_ESP32S3 && MICROPY_HW_ENABLE_USBDEV

extern esp_err_t mp_usb_phy_deinit(void);
extern void usb_phy_init(void);

#define USB_HOST_ALL_FREE_BIT    (1U << 0)
#define USB_HOST_STOPPED_BIT     (1U << 1)
#define USB_HOST_STOP_TIMEOUT_MS 5000
#define USB_SERIAL_RX_BUF_SIZE   1024
#define USB_SERIAL_TX_BUF_SIZE   256

static EventGroupHandle_t s_host_events;
static volatile bool s_host_stop_requested;
static bool s_host_installed;
static bool s_device_released;

/* Minimal USB Host serial client state for CDC/bulk CNC controllers */
static usb_host_client_handle_t s_ser_client = NULL;
static usb_device_handle_t s_ser_dev = NULL;
static TaskHandle_t s_ser_client_task = NULL;
static volatile bool s_ser_stop_task = false;
static uint8_t s_ser_if_num = 0;
static bool s_ser_if_claimed = false;
static uint8_t s_ser_ep_in = 0;
static uint8_t s_ser_ep_out = 0;
static uint16_t s_ser_mps_in = 64;
static uint16_t s_ser_mps_out = 64;
static usb_transfer_t *s_ser_tx_xfer = NULL;
static usb_transfer_t *s_ser_rx_xfer = NULL;
static SemaphoreHandle_t s_ser_tx_done = NULL;
static volatile esp_err_t s_ser_tx_status = ESP_OK;

static uint8_t s_ser_rx_ring[USB_SERIAL_RX_BUF_SIZE];
static volatile size_t s_ser_rx_head = 0;
static volatile size_t s_ser_rx_tail = 0;
static SemaphoreHandle_t s_ser_rx_mutex = NULL;
static volatile bool s_ser_open = false;

static void usb_role_raise_idf_error(const char *operation, esp_err_t err) {
    mp_raise_msg_varg(&mp_type_OSError,
        MP_ERROR_TEXT("%s: %s"), operation, esp_err_to_name(err));
}

static void ser_rx_push(const uint8_t *data, size_t len) {
    if (!s_ser_rx_mutex || !data || len == 0) {
        return;
    }
    if (xSemaphoreTake(s_ser_rx_mutex, pdMS_TO_TICKS(20)) == pdTRUE) {
        for (size_t i = 0; i < len; i++) {
            size_t next = (s_ser_rx_head + 1) % USB_SERIAL_RX_BUF_SIZE;
            if (next != s_ser_rx_tail) {
                s_ser_rx_ring[s_ser_rx_head] = data[i];
                s_ser_rx_head = next;
            }
        }
        xSemaphoreGive(s_ser_rx_mutex);
    }
}

static size_t ser_rx_pop(uint8_t *out, size_t max_len) {
    size_t count = 0;
    if (!s_ser_rx_mutex || !out || max_len == 0) {
        return 0;
    }
    if (xSemaphoreTake(s_ser_rx_mutex, pdMS_TO_TICKS(20)) == pdTRUE) {
        while (count < max_len && s_ser_rx_tail != s_ser_rx_head) {
            out[count++] = s_ser_rx_ring[s_ser_rx_tail];
            s_ser_rx_tail = (s_ser_rx_tail + 1) % USB_SERIAL_RX_BUF_SIZE;
        }
        xSemaphoreGive(s_ser_rx_mutex);
    }
    return count;
}

static void ser_tx_cb(usb_transfer_t *transfer) {
    s_ser_tx_status = transfer->status;
    if (s_ser_tx_done) {
        xSemaphoreGive(s_ser_tx_done);
    }
}

static void ser_rx_cb(usb_transfer_t *transfer) {
    if (!s_ser_open || s_ser_stop_task) {
        return;
    }
    if (transfer->status == USB_TRANSFER_STATUS_COMPLETED && transfer->actual_num_bytes > 0) {
        ser_rx_push(transfer->data_buffer, (size_t)transfer->actual_num_bytes);
    }
    if (s_ser_open && !s_ser_stop_task && s_ser_rx_xfer) {
        s_ser_rx_xfer->num_bytes = s_ser_mps_in;
        (void)usb_host_transfer_submit(s_ser_rx_xfer);
    }
}

static void ser_client_event_cb(const usb_host_client_event_msg_t *event_msg, void *arg) {
    (void)arg;
    if (event_msg->event == USB_HOST_CLIENT_EVENT_DEV_GONE) {
        s_ser_open = false;
    }
}

static void ser_client_task(void *arg) {
    (void)arg;
    while (!s_ser_stop_task && s_ser_client != NULL) {
        (void)usb_host_client_handle_events(s_ser_client, pdMS_TO_TICKS(50));
    }
    vTaskDelete(NULL);
}

static void usb_role_serial_close_internal(void) {
    s_ser_open = false;
    s_ser_stop_task = true;

    if (s_ser_tx_xfer) {
        (void)usb_host_transfer_cancel(s_ser_tx_xfer);
    }
    if (s_ser_rx_xfer) {
        (void)usb_host_transfer_cancel(s_ser_rx_xfer);
    }

    if (s_ser_client_task) {
        vTaskDelay(pdMS_TO_TICKS(80));
        s_ser_client_task = NULL;
    }

    if (s_ser_tx_xfer) {
        (void)usb_host_transfer_free(s_ser_tx_xfer);
        s_ser_tx_xfer = NULL;
    }
    if (s_ser_rx_xfer) {
        (void)usb_host_transfer_free(s_ser_rx_xfer);
        s_ser_rx_xfer = NULL;
    }

    if (s_ser_if_claimed && s_ser_dev) {
        (void)usb_host_interface_release(s_ser_client, s_ser_dev, s_ser_if_num);
    }
    s_ser_if_claimed = false;

    if (s_ser_dev) {
        (void)usb_host_device_close(s_ser_client, s_ser_dev);
        s_ser_dev = NULL;
    }

    if (s_ser_client) {
        (void)usb_host_client_deregister(s_ser_client);
        s_ser_client = NULL;
    }

    if (s_ser_tx_done) {
        vSemaphoreDelete(s_ser_tx_done);
        s_ser_tx_done = NULL;
    }
    if (s_ser_rx_mutex) {
        vSemaphoreDelete(s_ser_rx_mutex);
        s_ser_rx_mutex = NULL;
    }

    s_ser_rx_head = 0;
    s_ser_rx_tail = 0;
    s_ser_ep_in = 0;
    s_ser_ep_out = 0;
}

static esp_err_t ser_find_bulk_endpoints(usb_device_handle_t dev) {
    const usb_config_desc_t *cfg = NULL;
    esp_err_t err = usb_host_get_active_config_descriptor(dev, &cfg);
    if (err != ESP_OK || cfg == NULL) {
        return err != ESP_OK ? err : ESP_ERR_NOT_FOUND;
    }

    int offset = 0;
    const usb_standard_desc_t *desc = (const usb_standard_desc_t *)cfg;
    uint8_t cur_if = 0;
    bool found_in = false;
    bool found_out = false;

    while (offset < cfg->wTotalLength) {
        desc = usb_parse_next_descriptor_of_type(desc, cfg->wTotalLength, USB_W_VALUE_DT_ANY, &offset);
        if (desc == NULL) {
            break;
        }
        if (desc->bDescriptorType == USB_B_DESCRIPTOR_TYPE_INTERFACE) {
            const usb_intf_desc_t *intf = (const usb_intf_desc_t *)desc;
            cur_if = intf->bInterfaceNumber;
            if (!found_in || !found_out) {
                s_ser_if_num = cur_if;
                found_in = false;
                found_out = false;
            }
        } else if (desc->bDescriptorType == USB_B_DESCRIPTOR_TYPE_ENDPOINT) {
            const usb_ep_desc_t *ep = (const usb_ep_desc_t *)desc;
            if ((ep->bmAttributes & USB_BM_ATTRIBUTES_XFERTYPE_MASK) == USB_BM_ATTRIBUTES_XFER_BULK) {
                if (ep->bEndpointAddress & USB_B_ENDPOINT_ADDRESS_EP_DIR_MASK) {
                    if (!found_in) {
                        s_ser_ep_in = ep->bEndpointAddress;
                        s_ser_mps_in = ep->wMaxPacketSize ? ep->wMaxPacketSize : 64;
                        found_in = true;
                    }
                } else {
                    if (!found_out) {
                        s_ser_ep_out = ep->bEndpointAddress;
                        s_ser_mps_out = ep->wMaxPacketSize ? ep->wMaxPacketSize : 64;
                        found_out = true;
                    }
                }
            }
            if (found_in && found_out) {
                return ESP_OK;
            }
        }
    }
    return ESP_ERR_NOT_FOUND;
}

/* PATCH_USB_HOST_CLEAN_TEARDOWN */
static void usb_role_drain_host_devices(void) {
    (void)usb_host_device_free_all();
    if (s_host_events != NULL) {
        (void)xEventGroupWaitBits(
            s_host_events,
            USB_HOST_ALL_FREE_BIT,
            pdTRUE,
            pdFALSE,
            pdMS_TO_TICKS(500)
        );
    }
}

static void usb_host_daemon_task(void *arg) {
    (void)arg;
    EventGroupHandle_t events = s_host_events;

    while (!s_host_stop_requested) {
        uint32_t event_flags = 0;
        esp_err_t err = usb_host_lib_handle_events(pdMS_TO_TICKS(100), &event_flags);

        if ((event_flags & USB_HOST_LIB_EVENT_FLAGS_ALL_FREE) && events != NULL) {
            xEventGroupSetBits(events, USB_HOST_ALL_FREE_BIT);
        }

        if (err != ESP_OK && err != ESP_ERR_TIMEOUT && !s_host_stop_requested) {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }

    if (events != NULL) {
        xEventGroupSetBits(events, USB_HOST_STOPPED_BIT);
    }
    vTaskDelete(NULL);
}

/* PATCH_USB_DEVICE_PREPARE_PHY */
static mp_obj_t usb_role_device_prepare(void) {
    if (s_host_installed) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_HOST_ACTIVE: disconnect Host first"));
    }
    if (s_device_released) {
        usb_phy_init();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(usb_role_device_prepare_obj, usb_role_device_prepare);

static mp_obj_t usb_role_device_started(void) {
    if (s_host_installed) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_HOST_ACTIVE: disconnect Host first"));
    }
    if (!tud_inited()) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_DEVICE_START_FAILED: TinyUSB is not initialized"));
    }
    s_device_released = false;
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(usb_role_device_started_obj, usb_role_device_started);

static mp_obj_t usb_role_device_release(void) {
    if (s_host_installed) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_HOST_ACTIVE: disconnect Host first"));
    }

    if (tud_inited()) {
        mp_usbd_task();
        if (tud_inited() && !tud_deinit(TUD_OPT_RHPORT)) {
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_DEVICE_DEINIT_FAILED: tud_deinit"));
        }
    }

    esp_err_t err = mp_usb_phy_deinit();
    if (err != ESP_OK) {
        usb_role_raise_idf_error("USB_DEVICE_PHY_RELEASE_FAILED", err);
    }

    s_device_released = true;
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(usb_role_device_release_obj, usb_role_device_release);

static mp_obj_t usb_role_host_connect(void) {
    if (s_host_installed) {
        return mp_const_true;
    }
    if (!s_device_released) {
        mp_raise_msg(&mp_type_OSError,
            MP_ERROR_TEXT("USB_ROLE_ORDER: call usb.device.disconnect() before usb.host.connect()"));
    }

    s_host_events = xEventGroupCreate();
    if (s_host_events == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_HOST_NO_MEMORY: event group"));
    }
    s_host_stop_requested = false;

    const usb_host_config_t host_config = {
        .skip_phy_setup = false,
        .root_port_unpowered = false,
        .intr_flags = ESP_INTR_FLAG_LEVEL1,
    };

    esp_err_t err = usb_host_install(&host_config);
    if (err != ESP_OK) {
        vEventGroupDelete(s_host_events);
        s_host_events = NULL;
        usb_role_raise_idf_error("USB_HOST_INSTALL_FAILED", err);
    }
    s_host_installed = true;

    if (xTaskCreate(usb_host_daemon_task, "usb_host_daemon", 4096, NULL, 5, NULL) != pdPASS) {
        s_host_stop_requested = true;
        usb_role_drain_host_devices();
        err = usb_host_uninstall();
        s_host_installed = false;
        vEventGroupDelete(s_host_events);
        s_host_events = NULL;
        if (err != ESP_OK) {
            usb_role_raise_idf_error("USB_HOST_ROLLBACK_FAILED", err);
        }
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_HOST_NO_MEMORY: daemon task"));
    }

    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(usb_role_host_connect_obj, usb_role_host_connect);

static mp_obj_t usb_role_host_disconnect(void) {
    if (!s_host_installed) {
        return mp_const_true;
    }

    usb_role_serial_close_internal();

    xEventGroupClearBits(s_host_events, USB_HOST_ALL_FREE_BIT | USB_HOST_STOPPED_BIT);
    esp_err_t err = usb_host_device_free_all();
    if (err == ESP_ERR_NOT_FINISHED) {
        EventBits_t bits = xEventGroupWaitBits(
            s_host_events, USB_HOST_ALL_FREE_BIT, pdFALSE, pdTRUE,
            pdMS_TO_TICKS(USB_HOST_STOP_TIMEOUT_MS));
        if ((bits & USB_HOST_ALL_FREE_BIT) == 0) {
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_HOST_TIMEOUT: waiting for devices to be freed"));
        }
    } else if (err != ESP_OK) {
        usb_role_raise_idf_error("USB_HOST_FREE_DEVICES_FAILED", err);
    }

    s_host_stop_requested = true;
    err = usb_host_lib_unblock();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        usb_role_raise_idf_error("USB_HOST_UNBLOCK_FAILED", err);
    }

    EventBits_t bits = xEventGroupWaitBits(
        s_host_events, USB_HOST_STOPPED_BIT, pdFALSE, pdTRUE,
        pdMS_TO_TICKS(3000));
    if ((bits & USB_HOST_STOPPED_BIT) == 0) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_HOST_TIMEOUT: daemon did not stop"));
    }

    usb_role_drain_host_devices();
    err = usb_host_uninstall();
    if (err != ESP_OK) {
        usb_role_raise_idf_error("USB_HOST_UNINSTALL_FAILED", err);
    }

    s_host_installed = false;
    s_device_released = true;
    vEventGroupDelete(s_host_events);
    s_host_events = NULL;
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(usb_role_host_disconnect_obj, usb_role_host_disconnect);

static mp_obj_t usb_role_host_info(void) {
    usb_host_lib_info_t info = {0};
    if (s_host_installed) {
        esp_err_t err = usb_host_lib_info(&info);
        if (err != ESP_OK) {
            usb_role_raise_idf_error("USB_HOST_INFO_FAILED", err);
        }
    }

    mp_obj_t result = mp_obj_new_dict(3);
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_active), mp_obj_new_bool(s_host_installed));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_devices), mp_obj_new_int(info.num_devices));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_clients), mp_obj_new_int(info.num_clients));
    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_0(usb_role_host_info_obj, usb_role_host_info);

static mp_obj_t usb_role_serial_open(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    (void)n_args;
    (void)pos_args;
    (void)kw_args;
    if (!s_host_installed) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_HOST_NOT_ACTIVE: call usb.host.connect() first"));
    }
    if (s_ser_open) {
        usb_role_serial_close_internal();
    }

    s_ser_rx_mutex = xSemaphoreCreateMutex();
    s_ser_tx_done = xSemaphoreCreateBinary();
    if (!s_ser_rx_mutex || !s_ser_tx_done) {
        usb_role_serial_close_internal();
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_SERIAL_NO_MEMORY"));
    }

    usb_host_client_config_t client_cfg = {
        .is_synchronous = false,
        .client_event_callback = ser_client_event_cb,
        .callback_arg = NULL,
        .max_num_event_msg = 5,
    };
    esp_err_t err = usb_host_client_register(&client_cfg, &s_ser_client);
    if (err != ESP_OK) {
        usb_role_serial_close_internal();
        usb_role_raise_idf_error("USB_SERIAL_CLIENT_FAILED", err);
    }

    s_ser_stop_task = false;
    if (xTaskCreate(ser_client_task, "usb_ser_cli", 3072, NULL, 5, &s_ser_client_task) != pdPASS) {
        s_ser_client_task = NULL;
        usb_role_serial_close_internal();
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_SERIAL_TASK_FAILED"));
    }

    /* Open first enumerated USB device (address 1) */
    err = usb_host_device_open(s_ser_client, 1, &s_ser_dev);
    if (err != ESP_OK) {
        usb_role_serial_close_internal();
        usb_role_raise_idf_error("USB_SERIAL_DEV_OPEN_FAILED", err);
    }

    err = ser_find_bulk_endpoints(s_ser_dev);
    if (err != ESP_OK) {
        usb_role_serial_close_internal();
        usb_role_raise_idf_error("USB_SERIAL_NO_BULK_EPS", err);
    }

    err = usb_host_interface_claim(s_ser_client, s_ser_dev, s_ser_if_num, 0);
    if (err != ESP_OK) {
        usb_role_serial_close_internal();
        usb_role_raise_idf_error("USB_SERIAL_CLAIM_FAILED", err);
    }
    s_ser_if_claimed = true;

    err = usb_host_transfer_alloc(USB_SERIAL_TX_BUF_SIZE, 0, &s_ser_tx_xfer);
    if (err == ESP_OK) {
        err = usb_host_transfer_alloc(s_ser_mps_in > USB_SERIAL_RX_BUF_SIZE ? USB_SERIAL_RX_BUF_SIZE : s_ser_mps_in, 0, &s_ser_rx_xfer);
    }
    if (err != ESP_OK || !s_ser_tx_xfer || !s_ser_rx_xfer) {
        usb_role_serial_close_internal();
        usb_role_raise_idf_error("USB_SERIAL_XFER_ALLOC_FAILED", err != ESP_OK ? err : ESP_ERR_NO_MEM);
    }

    s_ser_open = true;

    s_ser_rx_xfer->device_handle = s_ser_dev;
    s_ser_rx_xfer->bEndpointAddress = s_ser_ep_in;
    s_ser_rx_xfer->callback = ser_rx_cb;
    s_ser_rx_xfer->num_bytes = s_ser_rx_xfer->data_buffer_size;
    err = usb_host_transfer_submit(s_ser_rx_xfer);
    if (err != ESP_OK) {
        usb_role_serial_close_internal();
        usb_role_raise_idf_error("USB_SERIAL_RX_SUBMIT_FAILED", err);
    }

    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(usb_role_serial_open_obj, 0, usb_role_serial_open);

static mp_obj_t usb_role_serial_write(mp_obj_t data_obj, mp_obj_t timeout_obj) {
    if (!s_ser_open || !s_ser_tx_xfer || !s_ser_tx_done) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_SERIAL_CLOSED"));
    }
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(data_obj, &bufinfo, MP_BUFFER_READ);
    uint32_t timeout_ms = (uint32_t)mp_obj_get_int(timeout_obj);
    if (timeout_ms == 0) {
        timeout_ms = 1000;
    }

    size_t sent_total = 0;
    const uint8_t *src = (const uint8_t *)bufinfo.buf;
    while (sent_total < bufinfo.len) {
        size_t chunk = bufinfo.len - sent_total;
        if (chunk > USB_SERIAL_TX_BUF_SIZE) {
            chunk = USB_SERIAL_TX_BUF_SIZE;
        }
        xSemaphoreTake(s_ser_tx_done, 0);
        memcpy(s_ser_tx_xfer->data_buffer, src + sent_total, chunk);
        s_ser_tx_xfer->device_handle = s_ser_dev;
        s_ser_tx_xfer->bEndpointAddress = s_ser_ep_out;
        s_ser_tx_xfer->num_bytes = chunk;
        s_ser_tx_xfer->callback = ser_tx_cb;
        s_ser_tx_status = ESP_OK;

        esp_err_t err = usb_host_transfer_submit(s_ser_tx_xfer);
        if (err != ESP_OK) {
            usb_role_raise_idf_error("USB_SERIAL_TX_SUBMIT_FAILED", err);
        }
        if (xSemaphoreTake(s_ser_tx_done, pdMS_TO_TICKS(timeout_ms)) != pdTRUE) {
            (void)usb_host_transfer_cancel(s_ser_tx_xfer);
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_SERIAL_TX_TIMEOUT"));
        }
        if (s_ser_tx_status != USB_TRANSFER_STATUS_COMPLETED) {
            mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("USB_SERIAL_TX_ERR_%d"), (int)s_ser_tx_status);
        }
        sent_total += s_ser_tx_xfer->actual_num_bytes;
    }
    return mp_obj_new_int((mp_int_t)sent_total);
}
static MP_DEFINE_CONST_FUN_OBJ_2(usb_role_serial_write_obj, usb_role_serial_write);

static mp_obj_t usb_role_serial_read(mp_obj_t max_len_obj, mp_obj_t timeout_obj) {
    if (!s_ser_open) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_SERIAL_CLOSED"));
    }
    size_t max_len = (size_t)mp_obj_get_int(max_len_obj);
    if (max_len == 0) {
        max_len = 64;
    }
    int timeout_ms = mp_obj_get_int(timeout_obj);

    uint8_t tmp[256];
    if (max_len > sizeof(tmp)) {
        max_len = sizeof(tmp);
    }

    uint32_t start = mp_hal_ticks_ms();
    size_t got = ser_rx_pop(tmp, max_len);
    while (got == 0 && ((int)(mp_hal_ticks_ms() - start) < timeout_ms)) {
        vTaskDelay(pdMS_TO_TICKS(10));
        got = ser_rx_pop(tmp, max_len);
    }
    return mp_obj_new_bytes(tmp, got);
}
static MP_DEFINE_CONST_FUN_OBJ_2(usb_role_serial_read_obj, usb_role_serial_read);

static mp_obj_t usb_role_serial_close(void) {
    usb_role_serial_close_internal();
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(usb_role_serial_close_obj, usb_role_serial_close);

static const mp_rom_map_elem_t usb_role_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR__usb_role) },
    { MP_ROM_QSTR(MP_QSTR_device_prepare), MP_ROM_PTR(&usb_role_device_prepare_obj) },
    { MP_ROM_QSTR(MP_QSTR_device_started), MP_ROM_PTR(&usb_role_device_started_obj) },
    { MP_ROM_QSTR(MP_QSTR_device_release), MP_ROM_PTR(&usb_role_device_release_obj) },
    { MP_ROM_QSTR(MP_QSTR_host_connect), MP_ROM_PTR(&usb_role_host_connect_obj) },
    { MP_ROM_QSTR(MP_QSTR_host_disconnect), MP_ROM_PTR(&usb_role_host_disconnect_obj) },
    { MP_ROM_QSTR(MP_QSTR_host_info), MP_ROM_PTR(&usb_role_host_info_obj) },
    { MP_ROM_QSTR(MP_QSTR_serial_open), MP_ROM_PTR(&usb_role_serial_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_serial_write), MP_ROM_PTR(&usb_role_serial_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_serial_read), MP_ROM_PTR(&usb_role_serial_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_serial_close), MP_ROM_PTR(&usb_role_serial_close_obj) },
};
static MP_DEFINE_CONST_DICT(usb_role_module_globals, usb_role_module_globals_table);

const mp_obj_module_t usb_role_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&usb_role_module_globals,
};
MP_REGISTER_MODULE(MP_QSTR__usb_role, usb_role_module);

#endif // CONFIG_IDF_TARGET_ESP32S3 && MICROPY_HW_ENABLE_USBDEV
