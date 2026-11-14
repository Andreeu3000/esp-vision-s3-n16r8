#ifndef S3_QUIRC_H
#define S3_QUIRC_H

#define quirc_version s3_quirc_version
#define quirc_new s3_quirc_new
#define quirc_destroy s3_quirc_destroy
#define quirc_resize s3_quirc_resize
#define quirc_begin s3_quirc_begin
#define quirc_end s3_quirc_end
#define quirc_strerror s3_quirc_strerror
#define quirc_count s3_quirc_count
#define quirc_extract s3_quirc_extract
#define quirc_decode s3_quirc_decode
#define quirc_flip s3_quirc_flip
#define quirc_version_db s3_quirc_version_db

#include "quirc.h"

#undef quirc_version
#undef quirc_new
#undef quirc_destroy
#undef quirc_resize
#undef quirc_begin
#undef quirc_end
#undef quirc_strerror
#undef quirc_count
#undef quirc_extract
#undef quirc_decode
#undef quirc_flip
#undef quirc_version_db

#endif
