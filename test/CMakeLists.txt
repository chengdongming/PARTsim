enable_testing()

add_executable(
  test_librtsim
  scheduler/fifo.cpp
  scheduler/truefifo.cpp
  scheduler/rm.cpp
  scheduler/asap_block.cpp
  scheduler/alap_block.cpp
  scheduler/alap_nonblock.cpp
  scheduler/alap_sync.cpp
)

target_link_libraries(
    test_librtsim
    PRIVATE GTest::gtest_main
    PRIVATE GTest::gmock
    PRIVATE rtsim
)

include(GoogleTest)
gtest_discover_tests(test_librtsim)

add_executable(
  test_asap_energy_model
  scheduler/asap_energy_model.cpp
)

target_link_libraries(
    test_asap_energy_model
    PRIVATE GTest::gtest_main
    PRIVATE rtsim
)

gtest_discover_tests(
    test_asap_energy_model
    WORKING_DIRECTORY ${PROJECT_SOURCE_DIR}
)
