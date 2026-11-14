/* SPDX-License-Identifier: Apache-2.0 */

#include <stdint.h>
#include <string.h>

#include "py/runtime.h"
#include "py/obj.h"
#include "py_image.h"

#include "esp_heap_caps.h"
#include "s3_quirc.h"

/* Decoder persistente: se reutiliza entre llamadas. */
static struct quirc *decoder;
static int decoder_width;
static int decoder_height;

/* Estructuras grandes fuera del stack, preferentemente en PSRAM. */
static struct quirc_code *work_code;
static struct quirc_data *work_data;

typedef struct {
    mp_obj_base_t base;
    mp_obj_t corners;
    mp_obj_t x;
    mp_obj_t y;
    mp_obj_t w;
    mp_obj_t h;
    mp_obj_t payload;
    mp_obj_t version;
    mp_obj_t ecc_level;
    mp_obj_t data_type;
} py_quirc_result_t;

static mp_obj_t py_quirc_result_corners(mp_obj_t self_in) {
    return ((py_quirc_result_t *)self_in)->corners;
}
static MP_DEFINE_CONST_FUN_OBJ_1(
    py_quirc_result_corners_obj, py_quirc_result_corners
);

static mp_obj_t py_quirc_result_rect(mp_obj_t self_in) {
    py_quirc_result_t *self = self_in;
    return mp_obj_new_tuple(4, (mp_obj_t[]) {
        self->x, self->y, self->w, self->h
    });
}
static MP_DEFINE_CONST_FUN_OBJ_1(
    py_quirc_result_rect_obj, py_quirc_result_rect
);

static mp_obj_t py_quirc_result_payload(mp_obj_t self_in) {
    return ((py_quirc_result_t *)self_in)->payload;
}
static MP_DEFINE_CONST_FUN_OBJ_1(
    py_quirc_result_payload_obj, py_quirc_result_payload
);

static mp_obj_t py_quirc_result_version(mp_obj_t self_in) {
    return ((py_quirc_result_t *)self_in)->version;
}
static MP_DEFINE_CONST_FUN_OBJ_1(
    py_quirc_result_version_obj, py_quirc_result_version
);

static mp_obj_t py_quirc_result_ecc_level(mp_obj_t self_in) {
    return ((py_quirc_result_t *)self_in)->ecc_level;
}
static MP_DEFINE_CONST_FUN_OBJ_1(
    py_quirc_result_ecc_level_obj, py_quirc_result_ecc_level
);

static mp_obj_t py_quirc_result_data_type(mp_obj_t self_in) {
    return ((py_quirc_result_t *)self_in)->data_type;
}
static MP_DEFINE_CONST_FUN_OBJ_1(
    py_quirc_result_data_type_obj, py_quirc_result_data_type
);

static const mp_rom_map_elem_t py_quirc_result_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_corners), MP_ROM_PTR(&py_quirc_result_corners_obj) },
    { MP_ROM_QSTR(MP_QSTR_rect), MP_ROM_PTR(&py_quirc_result_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_payload), MP_ROM_PTR(&py_quirc_result_payload_obj) },
    { MP_ROM_QSTR(MP_QSTR_version), MP_ROM_PTR(&py_quirc_result_version_obj) },
    { MP_ROM_QSTR(MP_QSTR_ecc_level), MP_ROM_PTR(&py_quirc_result_ecc_level_obj) },
    { MP_ROM_QSTR(MP_QSTR_data_type), MP_ROM_PTR(&py_quirc_result_data_type_obj) },
};

static MP_DEFINE_CONST_DICT(
    py_quirc_result_locals_dict,
    py_quirc_result_locals_table
);

static MP_DEFINE_CONST_OBJ_TYPE(
    py_quirc_result_type,
    MP_QSTR_qrcode,
    MP_TYPE_FLAG_NONE,
    locals_dict, &py_quirc_result_locals_dict
);

static void py_quirc_init(void) {
    if (!decoder) {
        decoder = s3_quirc_new();
        if (!decoder) {
            mp_raise_msg(&mp_type_MemoryError,
                         MP_ERROR_TEXT("Quirc decoder allocation failed"));
        }
    }

    if (!work_code) {
        work_code = heap_caps_malloc(
            sizeof(struct quirc_code),
            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT
        );
    }

    if (!work_data) {
        work_data = heap_caps_malloc(
            sizeof(struct quirc_data),
            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT
        );
    }

    if (!work_code || !work_data) {
        if (work_code) {
            heap_caps_free(work_code);
            work_code = NULL;
        }
        if (work_data) {
            heap_caps_free(work_data);
            work_data = NULL;
        }

        mp_raise_msg(&mp_type_MemoryError,
                     MP_ERROR_TEXT("Quirc workspace allocation failed"));
    }
}


static mp_obj_t py_quirc_make_result(const struct quirc_code *code,
                                     const struct quirc_data *data) {
    int min_x = code->corners[0].x;
    int max_x = min_x;
    int min_y = code->corners[0].y;
    int max_y = min_y;

    mp_obj_t corners[4];

    for (int i = 0; i < 4; i++) {
        int x = code->corners[i].x;
        int y = code->corners[i].y;

        if (x < min_x) min_x = x;
        if (x > max_x) max_x = x;
        if (y < min_y) min_y = y;
        if (y > max_y) max_y = y;

        corners[i] = mp_obj_new_tuple(2, (mp_obj_t[]) {
            mp_obj_new_int(x),
            mp_obj_new_int(y)
        });
    }

    /* Crear primero los objetos para que estén protegidos durante GC. */
    mp_obj_t corners_obj = mp_obj_new_tuple(4, corners);
    mp_obj_t x_obj = mp_obj_new_int(min_x);
    mp_obj_t y_obj = mp_obj_new_int(min_y);
    mp_obj_t w_obj = mp_obj_new_int(max_x - min_x);
    mp_obj_t h_obj = mp_obj_new_int(max_y - min_y);

    mp_obj_t payload_obj = mp_obj_new_bytes(
        data->payload,
        data->payload_len
    );

    mp_obj_t version_obj = mp_obj_new_int(data->version);
    mp_obj_t ecc_obj = mp_obj_new_int(data->ecc_level);
    mp_obj_t data_type_obj = mp_obj_new_int(data->data_type);

    py_quirc_result_t *result = m_new_obj(py_quirc_result_t);
    result->base.type = &py_quirc_result_type;
    result->corners = corners_obj;
    result->x = x_obj;
    result->y = y_obj;
    result->w = w_obj;
    result->h = h_obj;
    result->payload = payload_obj;
    result->version = version_obj;
    result->ecc_level = ecc_obj;
    result->data_type = data_type_obj;

    return MP_OBJ_FROM_PTR(result);
}

static mp_obj_t py_quirc_decode(mp_obj_t image_obj) {
    if (!mp_obj_is_type(image_obj, &py_image_type)) {
        mp_raise_TypeError(
            MP_ERROR_TEXT("expected image.Image")
        );
    }

    image_t *img = py_image_cobj(image_obj);

    if (!img || img->w <= 0 || img->h <= 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("invalid image dimensions"));
    }

    if (img->is_compressed || !IM_IS_GS(img)) {
        mp_raise_ValueError(
            MP_ERROR_TEXT("Quirc requires an uncompressed GRAY8 image")
        );
    }

    if (!img->pixels) {
        mp_raise_ValueError(MP_ERROR_TEXT("image has no pixel buffer"));
    }

    if ((size_t)img->w > SIZE_MAX / (size_t)img->h) {
        mp_raise_ValueError(MP_ERROR_TEXT("image dimensions overflow"));
    }

    size_t image_size = (size_t)img->w * (size_t)img->h;

    py_quirc_init();

    if (img->w != decoder_width || img->h != decoder_height) {
        if (s3_quirc_resize(decoder, img->w, img->h) < 0) {
            mp_raise_msg(&mp_type_MemoryError,
                         MP_ERROR_TEXT("Quirc image buffer allocation failed"));
        }

        decoder_width = img->w;
        decoder_height = img->h;
    }

    int width = 0;
    int height = 0;

    uint8_t *buffer = s3_quirc_begin(decoder, &width, &height);

    if (!buffer || width != img->w || height != img->h) {
        mp_raise_msg(&mp_type_RuntimeError,
                     MP_ERROR_TEXT("Quirc image buffer error"));
    }

    memcpy(buffer, img->pixels, image_size);
    s3_quirc_end(decoder);

    mp_obj_t results = mp_obj_new_list(0, NULL);
    int count = s3_quirc_count(decoder);

    for (int i = 0; i < count; i++) {
        s3_quirc_extract(decoder, i, work_code);

        quirc_decode_error_t error =
            s3_quirc_decode(work_code, work_data);

        /* No devolver detecciones que no pudieron decodificarse. */
        if (error != QUIRC_SUCCESS) {
            continue;
        }

        if (work_data->payload_len < 0 ||
            work_data->payload_len > QUIRC_MAX_PAYLOAD) {
            continue;
        }

        mp_obj_t item = py_quirc_make_result(work_code, work_data);
        mp_obj_list_append(results, item);
    }

    return results;
}
static MP_DEFINE_CONST_FUN_OBJ_1(py_quirc_decode_obj, py_quirc_decode);

static const mp_rom_map_elem_t py_quirc_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_Quirc) },
    { MP_ROM_QSTR(MP_QSTR_decode), MP_ROM_PTR(&py_quirc_decode_obj) },
};

static MP_DEFINE_CONST_DICT(
    py_quirc_globals,
    py_quirc_globals_table
);

const mp_obj_module_t py_quirc_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&py_quirc_globals,
};

MP_REGISTER_MODULE(MP_QSTR_Quirc, py_quirc_module);
