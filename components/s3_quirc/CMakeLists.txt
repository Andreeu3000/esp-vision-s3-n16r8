set(QUIRC_DIR "${CMAKE_CURRENT_LIST_DIR}/../../modules/quirc")

idf_component_register(
    SRCS
        "${QUIRC_DIR}/decode.c"
        "${QUIRC_DIR}/identify.c"
        "${QUIRC_DIR}/quirc.c"
        "${QUIRC_DIR}/version_db.c"
    INCLUDE_DIRS
        "include"
        "${QUIRC_DIR}"
)

# Aislar los símbolos de esta copia de Quirc.
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    quirc_version=s3_quirc_version
    quirc_new=s3_quirc_new
    quirc_destroy=s3_quirc_destroy
    quirc_resize=s3_quirc_resize
    quirc_begin=s3_quirc_begin
    quirc_end=s3_quirc_end
    quirc_strerror=s3_quirc_strerror
    quirc_count=s3_quirc_count
    quirc_extract=s3_quirc_extract
    quirc_decode=s3_quirc_decode
    quirc_flip=s3_quirc_flip
    quirc_version_db=s3_quirc_version_db
    QUIRC_FLOAT_TYPE=float
    QUIRC_USE_TGMATH
)
