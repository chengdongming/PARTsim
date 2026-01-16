/**
 * 抢占式调度测试程序
 *
 * 测试EDF、FP、RM调度器的抢占行为
 */

#include <getopt.h>
#include <iostream>
#include <memory>
#include <metasim/simul.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/mrtkernel.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/scheduler/fpsched.hpp>
#include <rtsim/scheduler/rmsched.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/task.hpp>
#include <rtsim/exeinstr.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/cpu.hpp>

using namespace MetaSim;
using namespace RTSim;

// 创建周期性任务
PeriodicTask* createPeriodicTask(const std::string& name,
                                  Tick period,
                                  Tick deadline,
                                  Tick duration,
                                  const std::string& workload) {
    PeriodicTask* task = new PeriodicTask(period, deadline, 0, name);
    task->addInstr(new FixedInstr(task, duration, workload));
    task->setInstanceCount(100);  // 限制实例数量，避免无限运行
    return task;
}

// 测试EDF调度器
void testEDF(const std::string& trace_file) {
    std::cout << "\n========== 测试EDF调度器 ==========" << std::endl;

    // 创建调度器
    EDFScheduler edf_sched;
    edf_sched.setName("EDF");

    // 创建内核
    MRTKernel kernel(&edf_sched);
    kernel.setName("kernel_edf");

    // 创建CPU
    CPU* cpu = new CPU("CPU0", 8100, 1.0);
    kernel.addCPU(cpu);

    // 创建JSON追踪
    JSONTrace* trace = new JSONTrace(trace_file);
    trace->attachToKernel(kernel);

    // 创建任务
    std::cout << "创建任务..." << std::endl;
    PeriodicTask* task_high = createPeriodicTask("task_high", 50, 50, 10, "bzip2");
    PeriodicTask* task_mid = createPeriodicTask("task_mid", 100, 100, 20, "crc32");
    PeriodicTask* task_low = createPeriodicTask("task_low", 200, 200, 30, "basicmath");

    // 添加任务到内核
    std::cout << "添加任务到内核..." << std::endl;
    kernel.addTask(*task_high, "");
    kernel.addTask(*task_mid, "");
    kernel.addTask(*task_low, "");

    // 显示任务信息
    std::cout << "\n任务集信息:" << std::endl;
    std::cout << "  task_high: 周期=50ms, deadline=50ms, 执行时间=10ms" << std::endl;
    std::cout << "  task_mid:  周期=100ms, deadline=100ms, 执行时间=20ms" << std::endl;
    std::cout << "  task_low:  周期=200ms, deadline=200ms, 执行时间=30ms" << std::endl;

    // 计算利用率
    double utilization = 10.0/50.0 + 20.0/100.0 + 30.0/200.0;
    std::cout << "\n总利用率: " << (utilization * 100) << "%" << std::endl;
    std::cout << "EDF可调度性条件: " << (utilization <= 1.0 ? "满足 ✓" : "不满足 ✗") << std::endl;

    // 运行仿真
    std::cout << "\n开始仿真..." << std::endl;
    SIMUL.run(500, false);  // 运行500ms

    std::cout << "仿真完成！" << std::endl;
    std::cout << "Trace文件: " << trace_file << std::endl;
}

// 测试FP调度器
void testFP(const std::string& trace_file) {
    std::cout << "\n========== 测试FP调度器 ==========" << std::endl;

    // 创建调度器
    FPScheduler fp_sched;
    fp_sched.setName("FP");

    // 创建内核
    MRTKernel kernel(&fp_sched);
    kernel.setName("kernel_fp");

    // 创建CPU
    CPU* cpu = new CPU("CPU0", 8100, 1.0);
    kernel.addCPU(cpu);

    // 创建JSON追踪
    JSONTrace* trace = new JSONTrace(trace_file);
    trace->attachToKernel(kernel);

    // 创建任务
    std::cout << "创建任务..." << std::endl;
    PeriodicTask* task_high = createPeriodicTask("task_high", 50, 50, 10, "bzip2");
    PeriodicTask* task_mid = createPeriodicTask("task_mid", 100, 100, 20, "crc32");
    PeriodicTask* task_low = createPeriodicTask("task_low", 200, 200, 30, "basicmath");

    // 添加任务到内核（指定优先级）
    std::cout << "添加任务到内核（显式优先级）..." << std::endl;
    fp_sched.addTask(task_high, 1);  // 优先级1最高
    fp_sched.addTask(task_mid, 2);   // 优先级2中等
    fp_sched.addTask(task_low, 3);   // 优先级3最低

    kernel.addTask(*task_high, "");
    kernel.addTask(*task_mid, "");
    kernel.addTask(*task_low, "");

    // 显示任务信息
    std::cout << "\n任务集信息:" << std::endl;
    std::cout << "  task_high: 周期=50ms, 优先级=1 (最高)" << std::endl;
    std::cout << "  task_mid:  周期=100ms, 优先级=2 (中)" << std::endl;
    std::cout << "  task_low:  周期=200ms, 优先级=3 (最低)" << std::endl;

    // 计算利用率
    double utilization = 10.0/50.0 + 20.0/100.0 + 30.0/200.0;
    std::cout << "\n总利用率: " << (utilization * 100) << "%" << std::endl;

    // 运行仿真
    std::cout << "\n开始仿真..." << std::endl;
    SIMUL.run(500, false);

    std::cout << "仿真完成！" << std::endl;
    std::cout << "Trace文件: " << trace_file << std::endl;
}

// 测试RM调度器
void testRM(const std::string& trace_file) {
    std::cout << "\n========== 测试RM调度器 ==========" << std::endl;

    // 创建调度器
    RMScheduler rm_sched;
    rm_sched.setName("RM");

    // 创建内核
    MRTKernel kernel(&rm_sched);
    kernel.setName("kernel_rm");

    // 创建CPU
    CPU* cpu = new CPU("CPU0", 8100, 1.0);
    kernel.addCPU(cpu);

    // 创建JSON追踪
    JSONTrace* trace = new JSONTrace(trace_file);
    trace->attachToKernel(kernel);

    // 创建任务
    std::cout << "创建任务..." << std::endl;
    PeriodicTask* task_high = createPeriodicTask("task_high", 50, 50, 10, "bzip2");
    PeriodicTask* task_mid = createPeriodicTask("task_mid", 100, 100, 20, "crc32");
    PeriodicTask* task_low = createPeriodicTask("task_low", 200, 200, 30, "basicmath");

    // 添加任务到内核（RM自动根据周期分配优先级）
    std::cout << "添加任务到内核（RM自动分配优先级）..." << std::endl;
    kernel.addTask(*task_high, "");
    kernel.addTask(*task_mid, "");
    kernel.addTask(*task_low, "");

    // 显示任务信息
    std::cout << "\n任务集信息:" << std::endl;
    std::cout << "  task_high: 周期=50ms  → 优先级最高 (RM)" << std::endl;
    std::cout << "  task_mid:  周期=100ms → 优先级中 (RM)" << std::endl;
    std::cout << "  task_low:  周期=200ms → 优先级最低 (RM)" << std::endl;

    // 计算利用率
    double utilization = 10.0/50.0 + 20.0/100.0 + 30.0/200.0;
    std::cout << "\n总利用率: " << (utilization * 100) << "%" << std::endl;

    // RM可调度性充分条件（利用率界限）
    double rm_bound = 3.0 * (pow(2.0, 1.0/3.0) - 1.0);  // n=3
    std::cout << "RM可调度性界限: " << (rm_bound * 100) << "%" << std::endl;
    std::cout << "RM可调度性条件: " << (utilization <= rm_bound ? "满足 ✓" : "不满足（但不一定不可调度）") << std::endl;

    // 运行仿真
    std::cout << "\n开始仿真..." << std::endl;
    SIMUL.run(500, false);

    std::cout << "仿真完成！" << std::endl;
    std::cout << "Trace文件: " << trace_file << std::endl;
}

void printUsage(const char* prog_name) {
    std::cout << "用法: " << prog_name << " [选项]" << std::endl;
    std::cout << "选项:" << std::endl;
    std::cout << "  -s, --scheduler=TYPE  调度器类型 (edf|fp|rm)" << std::endl;
    std::cout << "  -o, --output=FILE     输出trace文件路径" << std::endl;
    std::cout << "  -h, --help           显示帮助信息" << std::endl;
    std::cout << "\n示例:" << std::endl;
    std::cout << "  " << prog_name << " -s edf -o trace_edf.json" << std::endl;
    std::cout << "  " << prog_name << " -s fp -o trace_fp.json" << std::endl;
    std::cout << "  " << prog_name << " -s rm -o trace_rm.json" << std::endl;
}

int main(int argc, char* argv[]) {
    std::string scheduler_type;
    std::string output_file = "trace_preemptive.json";

    // 解析命令行参数
    static struct option long_options[] = {
        {"scheduler", required_argument, 0, 's'},
        {"output", required_argument, 0, 'o'},
        {"help", no_argument, 0, 'h'},
        {0, 0, 0, 0}
    };

    int opt;
    int option_index = 0;
    while ((opt = getopt_long(argc, argv, "s:o:h", long_options, &option_index)) != -1) {
        switch (opt) {
            case 's':
                scheduler_type = optarg;
                break;
            case 'o':
                output_file = optarg;
                break;
            case 'h':
                printUsage(argv[0]);
                return 0;
            default:
                printUsage(argv[0]);
                return 1;
        }
    }

    if (scheduler_type.empty()) {
        std::cerr << "错误: 必须指定调度器类型 (-s/--scheduler)" << std::endl;
        printUsage(argv[0]);
        return 1;
    }

    // 运行对应的测试
    try {
        if (scheduler_type == "edf") {
            testEDF(output_file);
        } else if (scheduler_type == "fp") {
            testFP(output_file);
        } else if (scheduler_type == "rm") {
            testRM(output_file);
        } else {
            std::cerr << "错误: 未知的调度器类型 '" << scheduler_type << "'" << std::endl;
            std::cerr << "支持的类型: edf, fp, rm" << std::endl;
            return 1;
        }
    } catch (std::exception& e) {
        std::cerr << "错误: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
