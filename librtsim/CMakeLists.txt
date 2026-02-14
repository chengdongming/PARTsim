find_package(Python3 COMPONENTS Development REQUIRED)
set(LIBRARY_NAME                    rtsim)
set(LIBRARY_TYPE                    SHARED)
set(LIBRARY_VERSION                 3.0)
set(LIBRARY_SOVERSION               3)
set(LIBRARY_INCLUDEDIR              include)
set(LIBRARY_INCLUDEDIR_PRIVATE      )
set(LIBRARY_INCLUDEDIR_INTERFACE    )
set(LIBRARY_PROPERTIES              )
set(LIBRARY_DEPENDENCIES            metasim)

# 添加Python到依赖项
set(LIBRARY_DEPENDENCIES
    ${LIBRARY_DEPENDENCIES}
    Python3::Python
)

# 添加Python包含路径
include_directories(${Python3_INCLUDE_DIRS})

# 设置Python库链接
link_libraries(${Python3_LIBRARIES})

# 添加编译定义（如果需要）
add_definitions(-DPYTHON_VERSION=${Python3_VERSION})


set(LIBRARY_SOURCE_FILES
    resource/fcfsresmanager.cpp
    resource/resmanager.cpp
    resource/resource.cpp
    # resource/bwi.cpp                # Not ready for use
    # resource/piresmanager.cpp       # Not ready for use

    # TODO: check schedulers organization
    scheduler/edfsched.cpp
    scheduler/fifosched.cpp
    scheduler/truefifo.cpp
    scheduler/fpsched.cpp
    scheduler/rmsched.cpp
    scheduler/rrsched.cpp
    scheduler/scheduler.cpp
    scheduler/regsched.cpp
    scheduler/gpfp_asap_scheduler.cpp
    scheduler/gpfp_epp_scheduler.cpp
    scheduler/gpfp_efpp_scheduler.cpp
    scheduler/gpfp_cbpp_scheduler.cpp
    scheduler/gpfp_tie_scheduler.cpp
    scheduler/gpfp_btie_scheduler.cpp
    scheduler/gpfp_tgf_scheduler.cpp
    scheduler/gpfp_cascade_scheduler.cpp
    scheduler/gpfp_batch_scheduler.cpp
    scheduler/gpfp_alap_block_scheduler.cpp
    scheduler/gpfp_alap_nonblock_scheduler.cpp
    scheduler/gpfp_alap_sync_scheduler.cpp
    scheduler/energy_bridge.cpp
    scheduler/config_manager.cpp
    # # These two go together. If you want to compile against EnergyMRTKernel
    # # (unsupported), look for the following tag and uncomment all the entries:
    # # "If compiling against EnergyMRTKernel uncomment"
    #
    # scheduler/multisched.cpp
    #
    # energyMRTKernel.cpp

    # NOTE: lots of stuff is unused/untested but built against the library
    # anyway!!

    # AVRTask.cpp
    capacitytimer.cpp
    cbserver.cpp
    cpu.cpp
    exeinstr.cpp
    feedback.cpp
    feedbacktest.cpp
    grubserver.cpp
    instr.cpp
    interrupt.cpp
    json_trace.cpp
    jtrace.cpp
    kernel.cpp
    kernevt.cpp
    load.cpp
    migrationmanager.cpp
    mrtkernel.cpp
    pollingserver.cpp
    powermodel.cpp
    powermodel_params.cpp
    ps_trace.cpp
    reginstr.cpp
    regtask.cpp
    rttask.cpp
    # schedinstr.cpp
    schedpoints.cpp
    schedrta.cpp
    server.cpp
    sparepot.cpp
    sporadicserver.cpp
    supercbs.cpp
    suspend_instr.cpp
    system.cpp
    system_descriptor.cpp
    task.cpp
    taskevt.cpp
    texttrace.cpp

    # # NOTE: setting a preemption threshold is deprecated;
    # # mainly, the reason is it was not used anymore, so it
    # # remains largely untested with recent code changes.
    # # Code is still there if you want to check it out.
    # # Uncomment this line to add it to the compilation and
    # # then look all the commented [[deprecated]] functions that mention a
    # # threshold until it compiles again.
    # 
    # threinstr.cpp

    timer.cpp
    traceevent.cpp
    tracepower.cpp
    waitinstr.cpp
    yaml.cpp
)

# set(LIBRARY_PRECOMPILED_HEADER_FILES
#     # <rtsim/AVRTask.hpp>
#     <rtsim/absresmanager.hpp>
#     <rtsim/abstask.hpp>
#     <rtsim/capacitytimer.hpp>
#     <rtsim/class_utils.hpp>
#     <rtsim/consts.hpp>
#     <rtsim/cpu.hpp>
#     <rtsim/csv.hpp>
#     # <rtsim/energyMRTKernel.hpp>
#     <rtsim/exeinstr.hpp>
#     <rtsim/feedback.hpp>
#     <rtsim/feedbacktest.hpp>
#     # <rtsim/fileImporter.hpp>
#     <rtsim/grubserver.hpp>
#     <rtsim/instr.hpp>
#     <rtsim/interpolate.hpp>
#     <rtsim/interrupt.hpp>
#     <rtsim/jtrace.hpp>
#     <rtsim/kernevt.hpp>
#     <rtsim/load.hpp>
#     <rtsim/migrationmanager.hpp>
#     <rtsim/mrtkernel.hpp>
#     <rtsim/opp.hpp>
#     <rtsim/pollingserver.hpp>
#     <rtsim/powermodel.hpp>
#     <rtsim/powermodel_params.hpp>
#     <rtsim/ps_trace.hpp>
#     <rtsim/reginstr.hpp>
#     <rtsim/resource/bwi.hpp>
#     <rtsim/resource/fcfsresmanager.hpp>
#     <rtsim/resource/piresmanager.hpp>
#     <rtsim/resource/resmanager.hpp>
#     <rtsim/resource/resource.hpp>
#     <rtsim/rttask.hpp>
#     <rtsim/schedinstr.hpp>
#     <rtsim/schedpoints.hpp>
#     <rtsim/schedrta.hpp>
#     <rtsim/scheduler/edfsched.hpp>
#     <rtsim/scheduler/fifosched.hpp>
#     <rtsim/scheduler/multisched.hpp>
#     <rtsim/scheduler/rmsched.hpp>
#     <rtsim/scheduler/scheduler.hpp>
#     <rtsim/scheduler/fpsched.hpp>
#     <rtsim/scheduler/rrsched.hpp>
#     <rtsim/sortedcont.hpp>
#     <rtsim/sparepot.hpp>
#     <rtsim/sporadicserver.hpp>
#     <rtsim/stateless_cpumodel_base.hpp>
#     # <rtsim/stateless_cpumodel_bp.hpp>
#     # <rtsim/stateless_cpumodel_minimal.hpp>
#     <rtsim/stateless_cpumodel_param.hpp>
#     <rtsim/stateless_cpumodel_table.hpp>
#     <rtsim/stateless_cpumodel_tb.hpp>
#     <rtsim/stateless_cpumodel_tbapprox.hpp>
#     <rtsim/supercbs.hpp>
#     <rtsim/supervisor.hpp>
#     <rtsim/suspend_instr.hpp>
#     <rtsim/system.hpp>
#     <rtsim/system_descriptor.hpp>
#     <rtsim/task.hpp>
#     <rtsim/taskevt.hpp>
#     <rtsim/taskexc.hpp>
#     # <rtsim/taskstat.hpp>
#     <rtsim/timer.hpp>
#     <rtsim/traceevent.hpp>
#     <rtsim/tracepower.hpp>
#     <rtsim/trim.hpp>
#     <rtsim/utils.hpp>
#     <rtsim/yaml.hpp>
#     <rtsim/abskernel.hpp>
#     <rtsim/cbserver.hpp>
#     <rtsim/json_trace.hpp>
#     <rtsim/server.hpp>
#     <rtsim/texttrace.hpp>
#     <rtsim/waitinstr.hpp>
#     <rtsim/kernel.hpp>
#     <rtsim/threinstr.hpp>
#     <rtsim/map_single_it.hpp>
#     <rtsim/matching_it.hpp>
#     )
set(RTSIM_SOURCES
    ${RTSIM_SOURCES}
    ${CMAKE_CURRENT_SOURCE_DIR}/scheduler/config_manager.cpp
)


option(RTLIB_THERMAL "Enables the thermal modeling subsystem" OFF)
if (RTLIB_THERMAL EQUAL ON)
    # Append Eigen to dependences
    find_package (Eigen3 3.3 REQUIRED NO_MODULE)
    set(LIBRARY_DEPENDENCIES
        ${LIBRARY_DEPENDENCIES}
        Eigen3::Eigen
        )

    # Append header to precompiled headers
    set(LIBRARY_PRECOMPILED_HEADER_FILES
        ${LIBRARY_HEADER_FILES}
        <rtsim/thermalmodel.hpp>
        )
endif()

include(${PROJECT_SOURCE_DIR}/cmakeopts/library.cmake)
