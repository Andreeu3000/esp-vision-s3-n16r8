#include <stdint.h>
#include <stdbool.h>
#include "py/obj.h"
#include "py/runtime.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/portmacro.h"
#include "usb/cdc_acm_host.h"
#include "usb/cdc_host_types.h"
#include "usb/vcp_ch34x.h"
#include "usb/vcp_cp210x.h"
#include "usb/vcp_ftdi.h"

#if CONFIG_IDF_TARGET_ESP32S3
#define RX_CAP 4096
#define OPEN_SLICE_MS 80
static cdc_acm_dev_hdl_t s_cdc;
static bool s_driver;
static volatile bool s_connected;
static uint8_t s_rx[RX_CAP];
static size_t s_head, s_tail, s_count;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

static void raise_idf(const char *op, esp_err_t err) {
    mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("%s: %s"), op, esp_err_to_name(err));
}
static void rx_reset(void) {
    portENTER_CRITICAL(&s_mux); s_head = s_tail = s_count = 0; portEXIT_CRITICAL(&s_mux);
}
static bool rx_cb(const uint8_t *data, size_t len, void *arg) {
    (void)arg;
    if (!data || !len) return true;
    portENTER_CRITICAL(&s_mux);
    for (size_t i=0; i<len; ++i) {
        if (s_count == RX_CAP) { s_tail=(s_tail+1)%RX_CAP; --s_count; }
        s_rx[s_head]=data[i]; s_head=(s_head+1)%RX_CAP; ++s_count;
    }
    portEXIT_CRITICAL(&s_mux);
    return true;
}
static void event_cb(const cdc_acm_host_dev_event_data_t *ev, void *arg) {
    (void)arg;
    if (ev && (ev->type == CDC_ACM_HOST_DEVICE_DISCONNECTED || ev->type == CDC_ACM_HOST_ERROR))
        s_connected=false;
}
static cdc_acm_host_device_config_t cfg(uint32_t timeout) {
    cdc_acm_host_device_config_t c = {
        .connection_timeout_ms=timeout, .out_buffer_size=256, .in_buffer_size=512,
        .event_cb=event_cb, .data_cb=rx_cb, .user_arg=NULL
    };
    return c;
}
static esp_err_t try_open(uint32_t timeout, cdc_acm_dev_hdl_t *out) {
    cdc_acm_host_device_config_t c=cfg(timeout);
    esp_err_t e=ch34x_vcp_open(CH34X_PID_AUTO,0,&c,out); if(e==ESP_OK)return e;
    e=cp210x_vcp_open(CP210X_PID_AUTO,0,&c,out); if(e==ESP_OK)return e;
    e=ftdi_vcp_open(FTDI_PID_AUTO,0,&c,out); if(e==ESP_OK)return e;
    return cdc_acm_host_open(CDC_HOST_ANY_VID,CDC_HOST_ANY_PID,0,&c,out);
}
static mp_obj_t install(void) {
    if (!s_driver) { esp_err_t e=cdc_acm_host_install(NULL); if(e!=ESP_OK)raise_idf("USB_CDC_INSTALL_FAILED (connect Host first)",e); s_driver=true; }
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(install_obj,install);
static mp_obj_t open_serial(size_t n,const mp_obj_t *a) {
    mp_int_t baud=n?mp_obj_get_int(a[0]):115200, timeout=n>1?mp_obj_get_int(a[1]):10000;
    if(baud<1200||baud>3000000||timeout<0||timeout>120000)mp_raise_ValueError(MP_ERROR_TEXT("baudrate/timeout out of range"));
    if(!s_driver)mp_raise_msg(&mp_type_OSError,MP_ERROR_TEXT("USB_CDC_NOT_INSTALLED: call usb.host.connect first"));
    if(s_cdc&&s_connected)return mp_const_true;
    if(s_cdc){(void)cdc_acm_host_close(s_cdc);s_cdc=NULL;}
    rx_reset(); TickType_t start=xTaskGetTickCount(), total=pdMS_TO_TICKS(timeout); esp_err_t err=ESP_ERR_NOT_FOUND;
    do {
        cdc_acm_dev_hdl_t h=NULL; err=try_open(OPEN_SLICE_MS,&h);
        if(err==ESP_OK&&h){
            s_cdc=h;
            cdc_acm_line_coding_t line={.dwDTERate=(uint32_t)baud,.bCharFormat=0,.bParityType=0,.bDataBits=8};
            esp_err_t le=cdc_acm_host_line_coding_set(s_cdc,&line);
            if(le!=ESP_OK&&le!=ESP_ERR_NOT_SUPPORTED&&le!=ESP_ERR_INVALID_RESPONSE){(void)cdc_acm_host_close(s_cdc);s_cdc=NULL;raise_idf("USB_CDC_LINE_CODING_FAILED",le);}
            s_connected=true; return mp_const_true;
        }
        if(!timeout)break; vTaskDelay(pdMS_TO_TICKS(10));
    } while((xTaskGetTickCount()-start)<total);
    mp_raise_msg_varg(&mp_type_OSError,MP_ERROR_TEXT("USB_SERIAL_NOT_FOUND after %d ms: %s"),(int)timeout,esp_err_to_name(err));
    return mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(open_obj,0,2,open_serial);
static mp_obj_t write_serial(mp_obj_t obj,mp_obj_t tout) {
    if(!s_cdc||!s_connected)mp_raise_msg(&mp_type_OSError,MP_ERROR_TEXT("USB_SERIAL_DISCONNECTED"));
    mp_buffer_info_t b; mp_get_buffer_raise(obj,&b,MP_BUFFER_READ); if(!b.len)return MP_OBJ_NEW_SMALL_INT(0);
    mp_int_t t=mp_obj_get_int(tout); if(t<0||t>120000)mp_raise_ValueError(MP_ERROR_TEXT("timeout out of range"));
    esp_err_t e=cdc_acm_host_data_tx_blocking(s_cdc,b.buf,b.len,(uint32_t)t);
    if(e!=ESP_OK){if(e==ESP_ERR_INVALID_STATE||e==ESP_ERR_NOT_FOUND)s_connected=false;raise_idf("USB_SERIAL_WRITE_FAILED",e);}
    return mp_obj_new_int_from_uint(b.len);
}
static MP_DEFINE_CONST_FUN_OBJ_2(write_obj,write_serial);
static mp_obj_t any_serial(void){portENTER_CRITICAL(&s_mux);size_t n=s_count;portEXIT_CRITICAL(&s_mux);return mp_obj_new_int_from_uint(n);}
static MP_DEFINE_CONST_FUN_OBJ_0(any_obj,any_serial);
static mp_obj_t read_serial(size_t n,const mp_obj_t *a) {
    mp_int_t req=n?mp_obj_get_int(a[0]):0,t=n>1?mp_obj_get_int(a[1]):0;
    if(req<0||req>RX_CAP||t<0||t>120000)mp_raise_ValueError(MP_ERROR_TEXT("read size/timeout out of range"));
    TickType_t start=xTaskGetTickCount(),total=pdMS_TO_TICKS(t);size_t avail;
    do{portENTER_CRITICAL(&s_mux);avail=s_count;portEXIT_CRITICAL(&s_mux);if(avail||!t||!s_connected)break;vTaskDelay(pdMS_TO_TICKS(1));}while((xTaskGetTickCount()-start)<total);
    size_t amount=(!req||req>avail)?avail:req;if(!amount)return mp_obj_new_bytes((const byte*)"",0);
    uint8_t *tmp=m_malloc(amount);if(!tmp)mp_raise_msg(&mp_type_MemoryError,MP_ERROR_TEXT("USB serial read buffer"));
    portENTER_CRITICAL(&s_mux);if(amount>s_count)amount=s_count;
    for(size_t i=0;i<amount;i++){tmp[i]=s_rx[s_tail];s_tail=(s_tail+1)%RX_CAP;}s_count-=amount;portEXIT_CRITICAL(&s_mux);
    mp_obj_t result=mp_obj_new_bytes(tmp,amount);m_free(tmp);return result;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(read_obj,0,2,read_serial);
static mp_obj_t connected(void){return mp_obj_new_bool(s_connected&&s_cdc);}
static MP_DEFINE_CONST_FUN_OBJ_0(connected_obj,connected);
static mp_obj_t close_serial(void){
    s_connected=false;if(s_cdc){esp_err_t e=cdc_acm_host_close(s_cdc);s_cdc=NULL;if(e!=ESP_OK&&e!=ESP_ERR_INVALID_STATE&&e!=ESP_ERR_NOT_FOUND)raise_idf("USB_SERIAL_CLOSE_FAILED",e);}
    rx_reset();return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(close_obj,close_serial);
static mp_obj_t shutdown_serial(void){
    (void)close_serial();if(s_driver){esp_err_t e=cdc_acm_host_uninstall();TickType_t st=xTaskGetTickCount();
        while(e==ESP_ERR_NOT_FINISHED&&(xTaskGetTickCount()-st)<pdMS_TO_TICKS(3000)){vTaskDelay(pdMS_TO_TICKS(10));e=cdc_acm_host_uninstall();}
        if(e!=ESP_OK)raise_idf("USB_CDC_UNINSTALL_FAILED",e);s_driver=false;}
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(shutdown_obj,shutdown_serial);
static const mp_rom_map_elem_t globals_table[]={
    {MP_ROM_QSTR(MP_QSTR___name__),MP_ROM_QSTR(MP_QSTR__usb_serial)},
    {MP_ROM_QSTR(MP_QSTR_install),MP_ROM_PTR(&install_obj)},
    {MP_ROM_QSTR(MP_QSTR_open),MP_ROM_PTR(&open_obj)},
    {MP_ROM_QSTR(MP_QSTR_write),MP_ROM_PTR(&write_obj)},
    {MP_ROM_QSTR(MP_QSTR_read),MP_ROM_PTR(&read_obj)},
    {MP_ROM_QSTR(MP_QSTR_any),MP_ROM_PTR(&any_obj)},
    {MP_ROM_QSTR(MP_QSTR_is_connected),MP_ROM_PTR(&connected_obj)},
    {MP_ROM_QSTR(MP_QSTR_close),MP_ROM_PTR(&close_obj)},
    {MP_ROM_QSTR(MP_QSTR_shutdown),MP_ROM_PTR(&shutdown_obj)},
};
static MP_DEFINE_CONST_DICT(globals,globals_table);
const mp_obj_module_t usb_serial_module={.base={&mp_type_module},.globals=(mp_obj_dict_t*)&globals};
MP_REGISTER_MODULE(MP_QSTR__usb_serial,usb_serial_module);
#endif
