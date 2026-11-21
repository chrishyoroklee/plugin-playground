# Builds one compiled effect: its offline renderer, and its plugin in every format.
#
# scripts/plugin.py drives this (npm run plugin). By hand:
#
#   cmake -S native -B build/x -DFX_EFFECT_DIR=<folder holding Effect.h> -DFX_NAME="Warm Tube"
#   cmake --build build/x --target fx_all
#
# The plugin is written once, as a CLAP (shell/plugin.cpp). clap-wrapper builds the VST3
# and the Audio Unit around the same code. CLAP, clap-wrapper and the VST3 SDK (3.8) are
# MIT licensed; Apple's AudioUnitSDK, used only for the Audio Unit, is Apache 2.0. All are
# downloaded at configure time, at the versions pinned below and in clap-wrapper.

cmake_minimum_required(VERSION 3.21)

set(CMAKE_OSX_DEPLOYMENT_TARGET "10.15" CACHE STRING "")
set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "")
# On Windows, link the C++ runtime into the plugin: it then needs no redistributable on
# the machine it is copied to, and it matches how the VST3 SDK is built.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

project(plugin-playground-fx VERSION 0.1.0 LANGUAGES C CXX)
if(APPLE)
    enable_language(OBJC OBJCXX)  # the VST3 and Audio Unit wrappers have Objective-C++ in them
endif()

set(FX_EFFECT_DIR "" CACHE PATH "The folder holding the Effect.h that patchc generated")
set(FX_NAME "Effect" CACHE STRING "The plugin's name, as hosts show it")
set(FX_SLUG "effect" CACHE STRING "The name in lower case with dashes, for identifiers")
set(FX_FORMATS "CLAP;VST3;AUV2" CACHE STRING "CLAP is always built; VST3 and AUV2 (macOS only) are optional")
set(FX_AU_SUBTYPE "Fx00" CACHE STRING "The Audio Unit's four-character subtype, unique per effect")
set(FX_KIND "effect" CACHE STRING "effect or instrument: what Effect.h is, which the Audio Unit has to declare")
option(FX_BUILD_PLUGIN "Build the plugin, which downloads the SDKs (off: only the renderer)" ON)

if(NOT EXISTS "${FX_EFFECT_DIR}/Effect.h")
    message(FATAL_ERROR "FX_EFFECT_DIR should be a folder holding Effect.h (got \"${FX_EFFECT_DIR}\")")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release)
endif()

# The effect: a header, and the kernels it is built on.
add_library(fx_effect INTERFACE)
target_include_directories(fx_effect INTERFACE "${FX_EFFECT_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}")

# The offline renderer, for checking the sound (scripts/maxcheck).
add_executable(fx_render render/render.cpp)
target_link_libraries(fx_render PRIVATE fx_effect)
set_target_properties(fx_render PROPERTIES OUTPUT_NAME render)

if(FX_BUILD_PLUGIN)
    include(FetchContent)
    set(CLAP_WRAPPER_DOWNLOAD_DEPENDENCIES TRUE CACHE BOOL "" FORCE)
    set(CLAP_WRAPPER_BUILD_AUV2 TRUE CACHE BOOL "" FORCE)
    set(CLAP_WRAPPER_DONT_ADD_TARGETS TRUE CACHE BOOL "" FORCE)
    FetchContent_Declare(clap-wrapper
        GIT_REPOSITORY https://github.com/free-audio/clap-wrapper.git
        GIT_TAG v0.18.0
        GIT_SHALLOW TRUE)
    FetchContent_MakeAvailable(clap-wrapper)

    if(FX_KIND STREQUAL "instrument")
        set(FX_AU_TYPE "aumu")  # a music device: notes in, audio out
    else()
        set(FX_AU_TYPE "aufx")
    endif()

    add_library(fx_impl STATIC shell/plugin.cpp)
    target_link_libraries(fx_impl PUBLIC clap clap-wrapper-extensions fx_effect)
    target_compile_definitions(fx_impl PRIVATE FX_VERSION="${PROJECT_VERSION}")

    make_clapfirst_plugins(
        TARGET_NAME fx
        IMPL_TARGET fx_impl
        OUTPUT_NAME "${FX_NAME}"
        ENTRY_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/shell/entry.cpp"
        BUNDLE_IDENTIFIER "com.plugin-playground.${FX_SLUG}"
        BUNDLE_VERSION ${PROJECT_VERSION}
        COPY_AFTER_BUILD FALSE
        PLUGIN_FORMATS ${FX_FORMATS}
        ASSET_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/products"
        AUV2_MANUFACTURER_NAME "Plugin Playground"
        AUV2_MANUFACTURER_CODE "PlPg"
        AUV2_SUBTYPE_CODE "${FX_AU_SUBTYPE}"
        AUV2_INSTRUMENT_TYPE "${FX_AU_TYPE}"
    )
    add_dependencies(fx_all fx_render)

    # A small host that loads the built .clap and holds it to Effect.h (ctest runs it).
    add_executable(fx_hostcheck test/hostcheck.cpp)
    target_link_libraries(fx_hostcheck PRIVATE clap fx_effect ${CMAKE_DL_LIBS})
    set_target_properties(fx_hostcheck PROPERTIES OUTPUT_NAME hostcheck)
    add_dependencies(fx_hostcheck fx_clap)
    add_dependencies(fx_all fx_hostcheck)
    enable_testing()
    add_test(NAME hostcheck COMMAND fx_hostcheck "$<TARGET_FILE:fx_clap>")
endif()
