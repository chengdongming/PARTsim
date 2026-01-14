add_executable(rtsim-exe
    main.cpp
)

set_target_properties(rtsim-exe
    PROPERTIES OUTPUT_NAME rtsim
)

# find_package(cmdarg)
# Check if dependency library is installed
# set(LIBCMDARG BOOL:false)
# set(LIBCMDARG_INCLUDE BOOL:false)

find_library(LIBCMDARG "cmdarg")
find_path(LIBCMDARG_INCLUDE "cmdarg.hpp")

# If dependency library is installed and headers are also present
if ((NOT ${LIBCMDARG}) OR (NOT ${LIBCMDARG_INCLUDE}))
    # Get the sources
    # message("Library libcmdarg not installed, downloading sources...")

    # Use git submodules to download sources
    execute_process(COMMAND git submodule update --init --recursive -- cmdarg
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})

    # Add it as subdirectory (use CMake to build it as well)
    add_subdirectory(cmdarg)
    
    # add_library(cmdarg INTERFACE)
    # target_include_directories(cmdarg INTERFACE
    #     $<BUILD_INTERFACE:${LIBCMDARG_INCLUDE}>
    #     # $<INSTALL_INTERFACE:${some_dest}>
    #     )
    # Need to export target as well
    # install(TARGETS cmdarg EXPORT my_export_set DESTINATION ${some_dest})
else()
    # Create fake target to set as dependency for rtsim
    # executable
    add_library(cmdarg INTERFACE)
    target_include_directories(cmdarg INTERFACE ${LIBCMDARG_INCLUDE})
    target_link_libraries(cmdarg INTERFACE ${LIBCMDARG})
endif()

target_link_libraries(rtsim-exe PRIVATE cmdarg PRIVATE rtsim)
