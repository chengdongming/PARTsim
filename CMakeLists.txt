cmake_minimum_required(VERSION 3.16 FATAL_ERROR)

project(
    partsim
    VERSION 1.0.0 # -alpha
    DESCRIPTION "A power-aware real-time system simulator"
    LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED true)

# All CMake options offloaded to external dependency
include(cmakeopts/CMakeLists.txt)

# Each subdirectory contains a target
add_subdirectory(libmetasim)
add_subdirectory(librtsim)
add_subdirectory(rtsim)

# Set to ON to build and later run automated testing
if(BUILD_TESTING)
    enable_testing()
    include(cmakeopts/misc/test.cmake)
    add_subdirectory(test)
endif()
