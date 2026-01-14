enable_testing()

add_executable(
  test_librtsim
  scheduler/fifo.cpp
  scheduler/truefifo.cpp
  scheduler/rm.cpp
)

target_link_libraries(
    test_librtsim
    PRIVATE GTest::gtest_main
    PRIVATE GTest::gmock
    PRIVATE rtsim
)

include(GoogleTest)
gtest_discover_tests(test_librtsim)
