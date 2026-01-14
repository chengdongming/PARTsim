# ======================================================== #
# ---------------- Project Configuration ----------------- #
# ======================================================== #

cmake_minimum_required(VERSION 3.16)

project(
    cmdarg
    VERSION 0.1
    DESCRIPTION "A simple library for parsing command-line arguments"
    LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED true)

# Include main options for CMake
include(cmakeopts/CMakeLists.txt)

# ======================================================== #
# ---------------- Targets Configuration ----------------- #
# ======================================================== #

set(LIBRARY_NAME                cmdarg)
set(LIBRARY_TYPE                SHARED)
set(LIBRARY_VERSION             0.1)
set(LIBRARY_SOVERSION           0)
set(LIBRARY_INCLUDEDIR          "include/public")
set(LIBRARY_INCLUDEDIR_PRIVATE  "include/private")

# set(LIBRARY_PROPERTIES      )
# set(LIBRARY_DEPENDENCIES    )

set(LIBRARY_SOURCE_FILES
    src/actions.cc
    src/parser.cc
    src/help_formatter.cc
)

# Complete library configuration
include(cmakeopts/library.cmake)

# # ----------------------- Testing ------------------------ #

add_subdirectory(test)
