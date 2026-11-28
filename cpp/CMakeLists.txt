cmake_minimum_required(VERSION 3.20)
project(tin_earthwork_cpp VERSION 3.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ---------- 核心算量与 I/O 静态库 ----------
add_library(tin_earthwork_core STATIC
    src/core/geometry.cpp
    src/core/delaunay.cpp
    src/core/calculator.cpp
    src/core/analysis.cpp
    src/core/engineering.cpp
    src/core/ai_optimizer.cpp
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
    $<$<BOOL:${WIN32}>:UNICODE>
    $<$<BOOL:${WIN32}>:_UNICODE>
    $<$<BOOL:${WIN32}>:WIN32_LEAN_AND_MEAN>
    $<$<BOOL:${MSVC}>:_CRT_SECURE_NO_WARNINGS>
)

if (MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

if (MINGW OR CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(tin_earthwork_core PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:-finput-charset=UTF-8 -fexec-charset=UTF-8 -O2 -Wall -Wextra>
        $<$<COMPILE_LANGUAGE:C>:-O2 -w>
    )
    set_source_files_properties(third_party/miniz/miniz.c PROPERTIES COMPILE_OPTIONS "-O2;-w")
elseif (CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(tin_earthwork_core PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:-O2 -Wall -Wextra>
        $<$<COMPILE_LANGUAGE:C>:-O2 -w>
    )
    set_source_files_properties(third_party/miniz/miniz.c PROPERTIES COMPILE_OPTIONS "-O2;-w")
elseif (MSVC)
    target_compile_options(tin_earthwork_core PRIVATE /utf-8 /O2 /W3)
endif()

# ---------- 跨平台 Dear ImGui + 南方CASS 工作站 UI 库（供 Win32 桌面与无头 UI 测试共用） ----------
set(IMGUI_CORE_SOURCES
    third_party/imgui/imgui.cpp
    third_party/imgui/imgui_draw.cpp
    third_party/imgui/imgui_tables.cpp
    third_party/imgui/imgui_widgets.cpp
)

add_library(tin_earthwork_ui_lib STATIC
    src/ui/theme.cpp
    src/ui/cad_canvas.cpp
    src/ui/viewport_3d.cpp
    src/ui/app.cpp
    ${IMGUI_CORE_SOURCES}
)

target_include_directories(tin_earthwork_ui_lib PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/imgui
)

target_link_libraries(tin_earthwork_ui_lib PUBLIC tin_earthwork_core)

if (MINGW OR CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    set_source_files_properties(${IMGUI_CORE_SOURCES} PROPERTIES COMPILE_OPTIONS "-O2;-w")
    target_compile_options(tin_earthwork_ui_lib PRIVATE
        -finput-charset=UTF-8 -fexec-charset=UTF-8 -O2 -Wall -Wextra
    )
elseif (CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    set_source_files_properties(${IMGUI_CORE_SOURCES} PROPERTIES COMPILE_OPTIONS "-O2;-w")
    target_compile_options(tin_earthwork_ui_lib PRIVATE -O2 -Wall -Wextra)
elseif (MSVC)
    target_compile_options(tin_earthwork_ui_lib PRIVATE /utf-8 /O2 /W3)
endif()

if (WIN32)
    target_link_libraries(tin_earthwork_ui_lib PRIVATE dwmapi comdlg32 gdi32 user32 shell32)

    # ---------- 原生 Win32 + D3D11 + ImGui 南方CASS 桌面工作站 ----------
    set(IMGUI_WIN32_BACKENDS
        third_party/imgui/backends/imgui_impl_win32.cpp
        third_party/imgui/backends/imgui_impl_dx11.cpp
    )

    add_executable(tin_earthwork_cass WIN32
        src/main.cpp
        ${IMGUI_WIN32_BACKENDS}
    )

    target_include_directories(tin_earthwork_cass PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/third_party/imgui/backends
    )

    target_link_libraries(tin_earthwork_cass PRIVATE
        tin_earthwork_ui_lib
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
        set_source_files_properties(${IMGUI_WIN32_BACKENDS} PROPERTIES COMPILE_OPTIONS "-O2;-w")
        target_compile_options(tin_earthwork_cass PRIVATE
            -finput-charset=UTF-8 -fexec-charset=UTF-8 -O2 -Wall -municode
        )
        target_link_options(tin_earthwork_cass PRIVATE
            -municode -static-libgcc -static-libstdc++ -static
        )
    elseif (MSVC)
        target_compile_options(tin_earthwork_cass PRIVATE /utf-8 /O2 /W3)
    endif()
endif()

# ---------- 核心单元测试 ----------
add_executable(tin_earthwork_tests
    tests/test_core.cpp
)

target_link_libraries(tin_earthwork_tests PRIVATE tin_earthwork_core)

if (MINGW OR CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(tin_earthwork_tests PRIVATE
        -finput-charset=UTF-8 -fexec-charset=UTF-8 -O2 -Wall -Wextra
    )
    if (MINGW)
        target_link_options(tin_earthwork_tests PRIVATE -static-libgcc -static-libstdc++ -static)
    endif()
elseif (CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(tin_earthwork_tests PRIVATE -O2 -Wall -Wextra)
elseif (MSVC)
    target_compile_options(tin_earthwork_tests PRIVATE /utf-8 /O2 /W3)
endif()

# ---------- 无头 Dear ImGui 南方CASS 工作站 UI 测试 ----------
add_executable(tin_earthwork_ui_tests
    tests/test_ui_headless.cpp
)

target_link_libraries(tin_earthwork_ui_tests PRIVATE tin_earthwork_ui_lib tin_earthwork_core)

if (MINGW OR CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(tin_earthwork_ui_tests PRIVATE
        -finput-charset=UTF-8 -fexec-charset=UTF-8 -O2 -Wall -Wextra
    )
    if (MINGW)
        target_link_options(tin_earthwork_ui_tests PRIVATE -static-libgcc -static-libstdc++ -static)
    endif()
elseif (CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(tin_earthwork_ui_tests PRIVATE -O2 -Wall -Wextra)
elseif (MSVC)
    target_compile_options(tin_earthwork_ui_tests PRIVATE /utf-8 /O2 /W3)
endif()

# ---------- AI 优化与工作站综合评分基准 ----------
add_executable(tin_earthwork_benchmark
    tests/test_ai_benchmark.cpp
)

target_link_libraries(tin_earthwork_benchmark PRIVATE tin_earthwork_ui_lib tin_earthwork_core)

if (MINGW OR CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(tin_earthwork_benchmark PRIVATE
        -finput-charset=UTF-8 -fexec-charset=UTF-8 -O2 -Wall -Wextra
    )
    if (MINGW)
        target_link_options(tin_earthwork_benchmark PRIVATE -static-libgcc -static-libstdc++ -static)
    endif()
elseif (CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(tin_earthwork_benchmark PRIVATE -O2 -Wall -Wextra)
elseif (MSVC)
    target_compile_options(tin_earthwork_benchmark PRIVATE /utf-8 /O2 /W3)
endif()

enable_testing()
add_test(
    NAME tin_earthwork_core_tests
    COMMAND tin_earthwork_tests "${CMAKE_CURRENT_SOURCE_DIR}/../test_data_1000.xlsx"
)
add_test(
    NAME tin_earthwork_ui_tests
    COMMAND tin_earthwork_ui_tests "${CMAKE_CURRENT_SOURCE_DIR}/../test_data_1000.xlsx"
)
add_test(
    NAME tin_earthwork_ai_benchmark
    COMMAND tin_earthwork_benchmark "${CMAKE_CURRENT_SOURCE_DIR}/../test_data_1000.xlsx"
)
