cmake_minimum_required(VERSION 3.20)
project(tin_earthwork_cpp VERSION 2.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ---------- 核心算量与 I/O 静态库 ----------
add_library(tin_earthwork_core STATIC
    src/core/geometry.cpp
    src/core/delaunay.cpp
    src/core/calculator.cpp
    src/core/analysis.cpp
    src/io/io.cpp
    third_party/miniz/miniz.c
)

target_include_directories(tin_earthwork_core PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/nlohmann
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/miniz
)

target_compile_definitions(tin_earthwork_core PUBLIC
    NOMINMAX
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    $<$<BOOL:${MSVC}>:_CRT_SECURE_NO_WARNINGS>
)

if (MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

if (MINGW OR CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(tin_earthwork_core PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:-finput-charset=UTF-8 -fexec-charset=UTF-8 -O2 -Wall>
        $<$<COMPILE_LANGUAGE:C>:-O2 -w>
    )
    set_source_files_properties(third_party/miniz/miniz.c PROPERTIES COMPILE_OPTIONS "-O2;-w")
elseif (MSVC)
    target_compile_options(tin_earthwork_core PRIVATE /utf-8 /O2 /W3)
endif()

# ---------- 原生 Win32 + D3D11 + ImGui 南方CASS 桌面工作站 ----------
set(IMGUI_SOURCES
    third_party/imgui/imgui.cpp
    third_party/imgui/imgui_draw.cpp
    third_party/imgui/imgui_tables.cpp
    third_party/imgui/imgui_widgets.cpp
    third_party/imgui/backends/imgui_impl_win32.cpp
    third_party/imgui/backends/imgui_impl_dx11.cpp
)

add_executable(tin_earthwork_cass WIN32
    src/main.cpp
    src/ui/theme.cpp
    src/ui/cad_canvas.cpp
    src/ui/app.cpp
    ${IMGUI_SOURCES}
)

target_include_directories(tin_earthwork_cass PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/imgui
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/imgui/backends
)

target_link_libraries(tin_earthwork_cass PRIVATE
    tin_earthwork_core
    d3d11
    dxgi
    d3dcompiler
    dwmapi
    comdlg32
    gdi32
    user32
    shell32
)

if (MINGW)
    set_source_files_properties(${IMGUI_SOURCES} PROPERTIES COMPILE_OPTIONS "-O2;-w")
    target_compile_options(tin_earthwork_cass PRIVATE
        -finput-charset=UTF-8 -fexec-charset=UTF-8 -O2 -Wall -municode
    )
    target_link_options(tin_earthwork_cass PRIVATE
        -municode -static-libgcc -static-libstdc++ -static
    )
elseif (MSVC)
    target_compile_options(tin_earthwork_cass PRIVATE /utf-8 /O2 /W3)
endif()

# ---------- 核心单元测试 ----------
add_executable(tin_earthwork_tests
    tests/test_core.cpp
)

target_link_libraries(tin_earthwork_tests PRIVATE tin_earthwork_core)

if (MINGW)
    target_compile_options(tin_earthwork_tests PRIVATE
        -finput-charset=UTF-8 -fexec-charset=UTF-8 -O2 -Wall
    )
    target_link_options(tin_earthwork_tests PRIVATE
        -static-libgcc -static-libstdc++ -static
    )
elseif (MSVC)
    target_compile_options(tin_earthwork_tests PRIVATE /utf-8 /O2 /W3)
endif()

enable_testing()
add_test(
    NAME tin_earthwork_core_tests
    COMMAND tin_earthwork_tests "${CMAKE_CURRENT_SOURCE_DIR}/../test_data_1000.xlsx"
)
