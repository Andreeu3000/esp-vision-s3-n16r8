/* ESP32-S3 USB Device <-> Host role control for MicroPython. */
#include "py/obj.h"
#include "py/runtime.h"
#include "esp_err.h"
#include "esp_intr_alloc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "usb/usb_host.h"
#include "tusb.h"
#include "mp_usbd.h"

#if CONFIG_IDF_TARGET_ESP32S3 && MICROPY_HW_ENABLE_USBDEV

extern esp_err_t mp_usb_phy_deinit(void);

#define USB_HOST_ALL_FREE_BIT   (1U << 0)
#define USB_HOST_STOPPED_BIT    (1U << 1)
#define USB_HOST_STOP_TIMEOUT_MS 5000

static EventGroupHandle_t s_host_events;
static volatile bool s_host_stop_requested;
static bool s_host_installed;
static bool s_device_released;

static void usb_role_raise_idf_error(const char *operation, esp_err_t err) {
    mp_raise_msg_varg(&mp_type_OSError,
        MP_ERROR_TEXT("%s: %s"), operation, esp_err_to_name(err));
}

/* PATCH_USB_HOST_CLEAN_TEARDOWN */
static void usb_role_drain_host_devices(void) {
    // Solicita liberar dispositivos enumerados en Host antes de desinstalar
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
            // Keep the daemon alive; host.disconnect() will perform the orderly teardown.
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }

    if (events != NULL) {
        xEventGroupSetBits(events, USB_HOST_STOPPED_BIT);
    }
    vTaskDelete(NULL);
}

static mp_obj_t usb_role_device_prepare(void) {
    if (s_host_installed) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("USB_HOST_ACTIVE: disconnect Host first"));
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

    // Process active(False) on the MicroPython main task before deinitializing
    // TinyUSB. tud_deinit() must run in the same task context as tud_task().
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

    // No Host clients are registered by this role-only layer. Free all
    // enumerated devices, then stop the sole Host-library event daemon.
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

static const mp_rom_map_elem_t usb_role_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR__usb_role) },
    { MP_ROM_QSTR(MP_QSTR_device_prepare), MP_ROM_PTR(&usb_role_device_prepare_obj) },
    { MP_ROM_QSTR(MP_QSTR_device_started), MP_ROM_PTR(&usb_role_device_started_obj) },
    { MP_ROM_QSTR(MP_QSTR_device_release), MP_ROM_PTR(&usb_role_device_release_obj) },
    { MP_ROM_QSTR(MP_QSTR_host_connect), MP_ROM_PTR(&usb_role_host_connect_obj) },
    { MP_ROM_QSTR(MP_QSTR_host_disconnect), MP_ROM_PTR(&usb_role_host_disconnect_obj) },
    { MP_ROM_QSTR(MP_QSTR_host_info), MP_ROM_PTR(&usb_role_host_info_obj) },
};
static MP_DEFINE_CONST_DICT(usb_role_module_globals, usb_role_module_globals_table);

const mp_obj_module_t usb_role_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&usb_role_module_globals,
};
MP_REGISTER_MODULE(MP_QSTR__usb_role, usb_role_module);

#endif // CONFIG_IDF_TARGET_ESP32S3 && MICROPY_HW_ENABLE_USBDEV
