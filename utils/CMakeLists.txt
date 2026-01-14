cmake_minimum_required(VERSION 3.10)
project(UnifiedLoggerTest)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 添加可执行文件
add_executable(test_unified_logger test_unified_logger.cpp)

# 设置输出目录
set_target_properties(test_unified_logger PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin
)

# 添加包含目录
target_include_directories(test_unified_logger PRIVATE .)

# 添加线程库
find_package(Threads REQUIRED)
target_link_libraries(test_unified_logger Threads::Threads)

# 编译选项
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(test_unified_logger PRIVATE -Wall -Wextra -Wpedantic)
endif()

# 安装目标（可选）
install(TARGETS test_unified_logger
    RUNTIME DESTINATION bin
)
