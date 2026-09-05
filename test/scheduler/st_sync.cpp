#include <algorithm>
#include <cmath>
#include <functional>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <gtest/gtest.h>

#include <metasim/simul.hpp>

#include <rtsim/cpu.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/task.hpp>

#define private public
#define protected public
#include <rtsim/scheduler/gpfp_st_sync_scheduler.hpp>
#undef protected
#undef private

#include <rtsim/mrtkernel.hpp>

namespace RTSim {

class TestSTSyncScheduler : public STSyncScheduler {
public:
    using Scheduler::enqueueModel;
    std::size_t baseQueueSize() const { return _queue.size(); }
    TaskModel *baseModel(AbsRTTask *task) const { return find(task); }
};

class FakeSTSyncTask : public Task {
private:
    int _task_number;
    Tick _period;
    Tick _relative_deadline;
    double _remaining;
    int _schedule_count;

public:
    FakeSTSyncTask(int task_number,
                   int period,
                   int relative_deadline,
                   double remaining,
                   int arrival = 0)
        : Task(nullptr,
               Tick(relative_deadline),
               Tick(arrival),
               "FakeSTSyncTask" + std::to_string(task_number),
               1000,
               Tick(static_cast<Tick::impl_t>(std::ceil(remaining)))),
          _task_number(task_number),
          _period(period),
          _relative_deadline(relative_deadline),
          _remaining(remaining),
          _schedule_count(0) {
        insertCode("fixed(1,control);");
    }

    void schedule() override {
        state = TSK_EXEC;
        ++_schedule_count;
    }

    void deschedule() override { state = TSK_READY; }

    Tick getDeadline() const override { return arrival + _relative_deadline; }
    Tick getRelDline() const override { return _relative_deadline; }
    Tick getPeriod() const override { return _period; }
    int getTaskNumber() const override { return _task_number; }
    double getRemainingWCET(double = 1.0) const override {
        return _remaining;
    }

    int getScheduleCount() const { return _schedule_count; }
    void setRemaining(double remaining) { _remaining = remaining; }

    void releaseAt(Tick tick) {
        arrival = tick;
        lastArrival = tick;
        state = TSK_READY;
    }

    void markRunningWithoutScheduleCount() { state = TSK_EXEC; }
};

class STSyncTestActionEvent : public MetaSim::Event {
private:
    std::function<void()> _action;

public:
    explicit STSyncTestActionEvent(std::function<void()> action)
        : MetaSim::Event("STSyncTestAction"),
          _action(std::move(action)) {}

    void doit() override { _action(); }
};

class TestSTSyncMRTKernel : public MRTKernel {
public:
    TestSTSyncMRTKernel(Scheduler *scheduler, const std::set<CPU *> &cpus)
        : MRTKernel(scheduler, cpus) {}

    void setRunning(CPU *cpu, AbsRTTask *task) {
        _m_currExe[cpu] = task;
    }
};

class STSyncSchedulerTestPeer {
public:
    static void addTaskModel(TestSTSyncScheduler &scheduler,
                             AbsRTTask *task,
                             int period,
                             int wcet,
                             double unit_energy) {
        auto *model = new STSyncTaskModel(task, period, wcet, "control");
        model->_unit_energy = unit_energy;
        model->_total_energy = unit_energy * wcet;
        scheduler.enqueueModel(model);
        scheduler._task_models[task] = model;
    }

    static void enqueue(STSyncScheduler &scheduler, AbsRTTask *task) {
        scheduler.addToReadyQueue(task);
    }

    static void arrive(STSyncScheduler &scheduler, AbsRTTask *task) {
        scheduler.onTaskArrival(task);
    }

    static void setEnergy(STSyncScheduler &scheduler,
                          double current_energy) {
        scheduler._initial_energy = current_energy;
        scheduler._current_energy = current_energy;
        scheduler._max_energy = 100.0;
        scheduler._energy_depleted = false;
        scheduler._deep_charging = false;
        scheduler._is_charging_sleep = false;
        scheduler._st_group_required_energy = 0.0;
        scheduler._st_group_slack_at_begin = 0;
        scheduler._dispatching_tasks_total_energy = 0.0;
        scheduler._counted_tasks_in_dispatch.clear();
        scheduler._current_batch_tasks.clear();
        scheduler._preempt_batch_tasks.clear();
        scheduler._batch_scheduled_this_tick = false;
        scheduler._current_batch_size = 0;
        scheduler._waiting_queue.clear();
        scheduler._deferred_arrivals.clear();
        scheduler._running_tasks.clear();
        scheduler._tasks_completed_wcet.clear();
        scheduler._energy_accounts.clear();
        scheduler._v108_batch_energy_checked = false;
        scheduler._v108_batch_energy_sufficient = false;
        scheduler._v108_batch_k_approved = 0;
        scheduler._v108_batch_start_energy = current_energy;
        scheduler._v108_batch_total_energy = 0.0;
        scheduler._stats.total_energy_consumed = 0.0;
        scheduler._stats.total_energy_harvested = 0.0;
        scheduler._stats.total_batch_schedules = 0;
        scheduler._stats.total_batch_skipped = 0;
        if (scheduler._group_wake_event) {
            scheduler._group_wake_event->drop();
        }
    }

    static void tick(STSyncScheduler &scheduler) {
        scheduler.performTickScheduling();
    }

    static void setHarvestConfig(STSyncScheduler &scheduler,
                                 const HarvestSourceConfig &config) {
        scheduler._harvest_runtime.beginRun(config);
    }

    static Tick slack(STSyncScheduler &scheduler, AbsRTTask *task) {
        return scheduler.calculateSlackForTask(task);
    }

    static void cleanup(STSyncScheduler &scheduler) {
        scheduler.cleanupExpiredTasks();
    }

    static int deadlineMisses(STSyncScheduler &scheduler) {
        return scheduler._stats.total_deadline_misses;
    }

    static AbsRTTask *selectSlot(STSyncScheduler &scheduler,
                                 unsigned int slot) {
        return scheduler.getTaskN(slot);
    }

    static bool isInReadyQueue(STSyncScheduler &scheduler,
                               AbsRTTask *task) {
        return scheduler.isInReadyQueue(task);
    }

    static bool isInWaitingQueue(STSyncScheduler &scheduler,
                                 AbsRTTask *task) {
        return scheduler.isInWaitingQueue(task);
    }

    static void cancelAutomaticTick(STSyncScheduler &scheduler) {
        scheduler._tick_event->drop();
        scheduler._first_tick_scheduled = false;
        if (scheduler._group_wake_event) {
            scheduler._group_wake_event->drop();
        }
    }

    static int batchSize(STSyncScheduler &scheduler) {
        return scheduler.calculateBatchSize();
    }

    static Tick groupWakeTime(STSyncScheduler &scheduler) {
        return scheduler._group_wake_event
            ? scheduler._group_wake_event->getWakeTime()
            : Tick(-1);
    }

    static Tick estimateGroupWakeTime(STSyncScheduler &scheduler,
                                      Tick slack) {
        return scheduler.calculateGroupWakeTime(slack);
    }
};

class ScopedSTSyncBaseHarvestRate {
public:
    explicit ScopedSTSyncBaseHarvestRate(double rate)
        : _original_source(ConfigManager::getInstance().getHarvestSourceConfig()),
          _original(ConfigManager::getInstance().getBaseHarvestRate()) {
        ConfigManager::getInstance().setBaseHarvestRate(rate);
        LegacySolarConfig zero_harvest;
        zero_harvest.base_harvesting_power_w = 0.0;
        ConfigManager::getInstance()._harvest_source_config = zero_harvest;
    }

    ~ScopedSTSyncBaseHarvestRate() {
        ConfigManager::getInstance()._harvest_source_config = _original_source;
        ConfigManager::getInstance().setBaseHarvestRate(_original);
    }

private:
    HarvestSourceConfig _original_source;
    double _original;
};

static ScaledPiecewiseConfig STSyncDelayedSurgeConfig() {
    ScaledPiecewiseConfig config;
    config.scale_w = 100000.0;
    config.segments = {
        {0, 2, 0.0},
        {2, 4, 1.0},
    };
    return config;
}

TEST(STSyncScheduler, GroupWakeIsSlackBoundAndIgnoresLegacyRate) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    {
        ScopedSTSyncBaseHarvestRate high_rate(1.0e9);
        EXPECT_EQ(STSyncSchedulerTestPeer::estimateGroupWakeTime(
                      scheduler, Tick(5000)),
                  Tick(5000));
    }
    {
        ScopedSTSyncBaseHarvestRate zero_rate(0.0);
        EXPECT_EQ(STSyncSchedulerTestPeer::estimateGroupWakeTime(
                      scheduler, Tick(5000)),
                  Tick(5000));
    }

    simulation.endSingleRun();
}

static bool ContainsTask(const std::vector<AbsRTTask *> &tasks,
                         AbsRTTask *task) {
    return std::find(tasks.begin(), tasks.end(), task) != tasks.end();
}

TEST(STSyncScheduler, EnergyAvailableRunsBeforeSlackZero) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-asap-positive-slack-cpu0", nullptr);
    CPU cpu1("st-sync-asap-positive-slack-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask first(1, 100, 100, 1.0);
    FakeSTSyncTask second(2, 120, 120, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 100, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 120, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &first);
    STSyncSchedulerTestPeer::enqueue(scheduler, &second);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 1);
    EXPECT_EQ(second.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 3.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, BatchInsufficientEnergyWithSlackWaits) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-wait-cpu0", nullptr);
    CPU cpu1("st-sync-wait-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask first(1, 20, 20, 1.0);
    FakeSTSyncTask second(2, 30, 30, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 20, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 30, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &first);
    STSyncSchedulerTestPeer::enqueue(scheduler, &second);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_TRUE(scheduler.isChargingSleepActive());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, UrgentNewTopMExecutesSameTickWhenChargingSlackExpires) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-urgent-top-m-cpu0", nullptr);
    CPU cpu1("st-sync-urgent-top-m-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask h(1, 11, 1, 1.0, 2);
    FakeSTSyncTask a(2, 17, 10, 3.0);
    FakeSTSyncTask b(3, 19, 12, 2.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &h, 11, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &a, 17, 3, 2.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &b, 19, 2, 2.0);

    ScaledPiecewiseConfig harvest;
    harvest.scale_w = 2000.0;
    harvest.segments = {{0, 1, 0.0}, {1, 2, 1.0}};

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setHarvestConfig(
        scheduler, HarvestSourceConfig{harvest});
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    a.releaseAt(Tick(0));
    b.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &a);
    STSyncSchedulerTestPeer::enqueue(scheduler, &b);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));
    ASSERT_TRUE(scheduler.isChargingSleepActive());
    ASSERT_TRUE(scheduler.getCurrentBatchTasks().empty());
    ASSERT_EQ(scheduler._waiting_queue.size(), 2u);
    ASSERT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    STSyncTestActionEvent continue_hold([&]() {
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    continue_hold.post(Tick(1));
    simulation.run_to(Tick(1));

    STSyncTestActionEvent urgent_arrival([&]() {
        h.releaseAt(Tick(2));
        STSyncSchedulerTestPeer::arrive(scheduler, &h);
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    urgent_arrival.post(Tick(2));
    testing::internal::CaptureStdout();
    simulation.run_to(Tick(2));
    const std::string tick_output = testing::internal::GetCapturedStdout();

    EXPECT_EQ(scheduler.getCurrentBatchTasks().size(), 2u);
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &h));
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &a));
    EXPECT_FALSE(ContainsTask(scheduler.getCurrentBatchTasks(), &b));
    EXPECT_EQ(h.getScheduleCount(), 1);
    EXPECT_EQ(a.getScheduleCount(), 1);
    EXPECT_EQ(b.getScheduleCount(), 0);
    EXPECT_FALSE(scheduler.isChargingSleepActive());
    EXPECT_TRUE(scheduler._waiting_queue.empty());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 3.0);
    EXPECT_LE(STSyncSchedulerTestPeer::slack(scheduler, &h), Tick(0));
    EXPECT_EQ(STSyncSchedulerTestPeer::deadlineMisses(scheduler), 0);
    EXPECT_EQ(tick_output.find("实际同步组原子验资失败"), std::string::npos);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, ChargingBatchHoldsUntilBatteryFullOrSlackExhausted) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ScopedSTSyncBaseHarvestRate zero_harvest(0.0);
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-recharge-cpu0", nullptr);
    CPU cpu1("st-sync-recharge-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask first(1, 20, 20, 1.0);
    FakeSTSyncTask second(2, 30, 30, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 20, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 30, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &first);
    STSyncSchedulerTestPeer::enqueue(scheduler, &second);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));
    ASSERT_TRUE(scheduler.isChargingSleepActive());
    ASSERT_EQ(first.getScheduleCount(), 0);
    ASSERT_EQ(second.getScheduleCount(), 0);

    STSyncTestActionEvent recharge([&]() {
        scheduler._current_energy = 2.0;
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    recharge.post(Tick(1));
    simulation.run_to(Tick(1));

    EXPECT_EQ(first.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.isChargingSleepActive());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 2.0);

    STSyncTestActionEvent full([&]() {
        scheduler._current_energy = 100.0;
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    full.post(Tick(2));
    simulation.run_to(Tick(2));

    EXPECT_EQ(first.getScheduleCount(), 1);
    EXPECT_EQ(second.getScheduleCount(), 1);
    EXPECT_FALSE(scheduler.isChargingSleepActive());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 98.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler,
     ReportingCountsOnlyCurrentlyUnaffordableHeadDuringChargeHold) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ScopedSTSyncBaseHarvestRate zero_harvest(0.0);
    TestSTSyncScheduler scheduler;
    CPU cpu("st-sync-reporting-head-cpu", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTSyncTask task(1, 5, 10, 1.0);
    JSONTrace trace(
        "/tmp/partsim_st_sync_reporting_head.json", Tick(2));

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &task, 5, 1, 2.0);
    trace.enableObservabilitySummaries(Tick(2));
    scheduler.setTraceLogger(&trace);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &task);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    ASSERT_EQ(trace.mechanismSummary().st_charging_opportunity_ticks, 1u);
    ASSERT_EQ(trace.mechanismSummary().st_slack_charging_wait_ticks, 1u);
    ASSERT_EQ(task.getScheduleCount(), 0);
    ASSERT_TRUE(scheduler.isChargingSleepActive());
    ASSERT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);
    ASSERT_EQ(STSyncSchedulerTestPeer::deadlineMisses(scheduler), 0);

    STSyncTestActionEvent exactly_affordable([&]() {
        scheduler._current_energy = 2.0;
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    exactly_affordable.post(Tick(1));
    simulation.run_to(Tick(1));

    EXPECT_EQ(trace.mechanismSummary().st_charging_opportunity_ticks, 1u);
    EXPECT_EQ(trace.mechanismSummary().st_slack_charging_wait_ticks, 1u);
    EXPECT_EQ(task.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.isChargingSleepActive());
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 2.0);
    EXPECT_DOUBLE_EQ(task.getRemainingWCET(), 1.0);
    EXPECT_EQ(STSyncSchedulerTestPeer::deadlineMisses(scheduler), 0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler,
     ReportingSeparatesAffordableHeadFromUnaffordableBatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-reporting-batch-cpu0", nullptr);
    CPU cpu1("st-sync-reporting-batch-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask head(1, 5, 10, 1.0);
    FakeSTSyncTask second(2, 10, 10, 1.0);
    JSONTrace trace(
        "/tmp/partsim_st_sync_reporting_batch.json", Tick(1));

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &head, 5, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &second, 10, 1, 1.0);
    trace.enableObservabilitySummaries(Tick(1));
    scheduler.setTraceLogger(&trace);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    head.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &head);
    STSyncSchedulerTestPeer::enqueue(scheduler, &second);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(trace.mechanismSummary().sync_batch_evaluation_ticks, 1u);
    EXPECT_EQ(trace.mechanismSummary().sync_batch_reject_ticks, 1u);
    EXPECT_EQ(trace.mechanismSummary().st_charging_opportunity_ticks, 0u);
    EXPECT_EQ(trace.mechanismSummary().st_slack_charging_wait_ticks, 0u);
    EXPECT_EQ(head.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);
    EXPECT_EQ(STSyncSchedulerTestPeer::deadlineMisses(scheduler), 0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler,
     ReportingCanObserveOpportunityWithoutGroupWait) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-reporting-no-wait-cpu0", nullptr);
    CPU cpu1("st-sync-reporting-no-wait-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask head(1, 5, 10, 1.0);
    FakeSTSyncTask zero_slack(2, 10, 1, 1.0);
    JSONTrace trace(
        "/tmp/partsim_st_sync_reporting_no_wait.json", Tick(1));

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &head, 5, 1, 2.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &zero_slack, 10, 1, 1.0);
    trace.enableObservabilitySummaries(Tick(1));
    scheduler.setTraceLogger(&trace);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    head.releaseAt(Tick(0));
    zero_slack.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &head);
    STSyncSchedulerTestPeer::enqueue(scheduler, &zero_slack);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(trace.mechanismSummary().sync_batch_evaluation_ticks, 1u);
    EXPECT_EQ(trace.mechanismSummary().sync_batch_reject_ticks, 1u);
    EXPECT_EQ(trace.mechanismSummary().st_charging_opportunity_ticks, 1u);
    EXPECT_EQ(trace.mechanismSummary().st_slack_charging_wait_ticks, 0u);
    EXPECT_EQ(head.getScheduleCount(), 0);
    EXPECT_EQ(zero_slack.getScheduleCount(), 0);
    EXPECT_FALSE(scheduler.isChargingSleepActive());
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);
    EXPECT_EQ(STSyncSchedulerTestPeer::deadlineMisses(scheduler), 0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler,
     ReportingDoesNotCountUnaffordableHeadWithExhaustedSlack) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu("st-sync-reporting-zero-slack-cpu", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTSyncTask task(1, 5, 1, 1.0);
    JSONTrace trace(
        "/tmp/partsim_st_sync_reporting_zero_slack.json", Tick(1));

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &task, 5, 1, 2.0);
    trace.enableObservabilitySummaries(Tick(1));
    scheduler.setTraceLogger(&trace);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &task);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(trace.mechanismSummary().st_charging_opportunity_ticks, 0u);
    EXPECT_EQ(trace.mechanismSummary().st_slack_charging_wait_ticks, 0u);
    EXPECT_EQ(task.getScheduleCount(), 0);
    EXPECT_FALSE(scheduler.isChargingSleepActive());
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);
    EXPECT_DOUBLE_EQ(task.getRemainingWCET(), 1.0);
    EXPECT_EQ(STSyncSchedulerTestPeer::deadlineMisses(scheduler), 0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler,
     PiecewiseSurgeReleasesWholeBatchDespiteZeroLegacyRate) {
    ScopedSTSyncBaseHarvestRate misleading_rate(0.0);
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-piecewise-wake-cpu0", nullptr);
    CPU cpu1("st-sync-piecewise-wake-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask first(1, 20, 20, 1.0);
    FakeSTSyncTask second(2, 30, 30, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &first, 20, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &second, 30, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    STSyncSchedulerTestPeer::setHarvestConfig(
        scheduler, HarvestSourceConfig{STSyncDelayedSurgeConfig()});
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &first);
    STSyncSchedulerTestPeer::enqueue(scheduler, &second);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));
    ASSERT_TRUE(scheduler.isChargingSleepActive());
    EXPECT_EQ(first.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);

    for (int time_ms = 1; time_ms <= 2; ++time_ms) {
        STSyncTestActionEvent zero_power_tick([&]() {
            STSyncSchedulerTestPeer::tick(scheduler);
        });
        zero_power_tick.post(Tick(time_ms));
        simulation.run_to(Tick(time_ms));
        EXPECT_TRUE(scheduler.isChargingSleepActive())
            << "time_ms=" << time_ms;
        EXPECT_EQ(first.getScheduleCount(), 0)
            << "time_ms=" << time_ms;
        EXPECT_EQ(second.getScheduleCount(), 0)
            << "time_ms=" << time_ms;
        EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty())
            << "time_ms=" << time_ms;
        EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0)
            << "time_ms=" << time_ms;
    }

    STSyncTestActionEvent surge_tick([&]() {
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    surge_tick.post(Tick(3));
    simulation.run_to(Tick(3));

    EXPECT_FALSE(scheduler.isChargingSleepActive());
    EXPECT_EQ(first.getScheduleCount(), 1);
    EXPECT_EQ(second.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 98.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, BatchInsufficientEnergyNoSlackStillNoPartialExecution) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-no-slack-cpu0", nullptr);
    CPU cpu1("st-sync-no-slack-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask first(1, 5, 1, 1.0);
    FakeSTSyncTask second(2, 10, 1, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 5, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 10, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &first);
    STSyncSchedulerTestPeer::enqueue(scheduler, &second);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_FALSE(scheduler.isChargingSleepActive());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, GroupSlackUsesMinimumSlack) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-min-slack-cpu0", nullptr);
    CPU cpu1("st-sync-min-slack-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask positive_slack(1, 5, 10, 1.0);
    FakeSTSyncTask urgent(2, 20, 1, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &positive_slack, 5, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &urgent, 20, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    positive_slack.releaseAt(Tick(0));
    urgent.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &positive_slack);
    STSyncSchedulerTestPeer::enqueue(scheduler, &urgent);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(positive_slack.getScheduleCount(), 0);
    EXPECT_EQ(urgent.getScheduleCount(), 0);
    EXPECT_FALSE(scheduler.isChargingSleepActive());

    simulation.endSingleRun();
}

TEST(STSyncScheduler, EnergyInsufficientBatchWaitsByMinimumSlack) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-min-wake-cpu0", nullptr);
    CPU cpu1("st-sync-min-wake-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask min_slack(1, 5, 5, 1.0);
    FakeSTSyncTask larger_slack(2, 20, 10, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &min_slack, 5, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &larger_slack, 20, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    min_slack.releaseAt(Tick(0));
    larger_slack.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &min_slack);
    STSyncSchedulerTestPeer::enqueue(scheduler, &larger_slack);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(min_slack.getScheduleCount(), 0);
    EXPECT_EQ(larger_slack.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_TRUE(scheduler.isChargingSleepActive());
    EXPECT_EQ(STSyncSchedulerTestPeer::groupWakeTime(scheduler), Tick(4));
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, ExactBatchEnergyChargedOnce) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-exact-cpu0", nullptr);
    CPU cpu1("st-sync-exact-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask first(1, 10, 10, 1.0);
    FakeSTSyncTask second(2, 20, 20, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 10, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 2.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &first);
    STSyncSchedulerTestPeer::enqueue(scheduler, &second);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(first.getScheduleCount(), 1);
    EXPECT_EQ(second.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 2.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, ChargingBatchReleasesWhenGroupSlackExhausted) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ScopedSTSyncBaseHarvestRate zero_harvest(0.0);
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-slack-release-cpu0", nullptr);
    CPU cpu1("st-sync-slack-release-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask first(1, 5, 2, 1.0);
    FakeSTSyncTask second(2, 10, 2, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 5, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 10, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &first);
    STSyncSchedulerTestPeer::enqueue(scheduler, &second);

    STSyncSchedulerTestPeer::tick(scheduler);
    ASSERT_TRUE(scheduler.isChargingSleepActive());
    ASSERT_TRUE(scheduler.getCurrentBatchTasks().empty());

    STSyncTestActionEvent slack_exhausted([&]() {
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    slack_exhausted.post(Tick(1));
    simulation.run_to(Tick(1));

    EXPECT_FALSE(scheduler.isChargingSleepActive());
    EXPECT_EQ(first.getScheduleCount(), 0);
    EXPECT_EQ(second.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, NoFreeFirstTick) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu("st-sync-no-free-cpu", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTSyncTask task(1, 10, 10, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 10, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &task);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(scheduler.getTotalEnergyConsumed(), 1.0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 0.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, WholeTopMAtomicityRejectsAffordableContinuation) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-atomic-cpu0", nullptr);
    CPU cpu1("st-sync-atomic-cpu1", nullptr);
    CPU cpu2("st-sync-atomic-cpu2", nullptr);
    CPU cpu3("st-sync-atomic-cpu3", nullptr);
    TestSTSyncMRTKernel kernel(
        &scheduler, std::set<CPU *>{&cpu0, &cpu1, &cpu2, &cpu3});
    FakeSTSyncTask continuation1(1, 10, 1, 1.0);
    FakeSTSyncTask continuation2(2, 20, 1, 1.0);
    FakeSTSyncTask ready3(3, 30, 1, 1.0);
    FakeSTSyncTask ready4(4, 40, 1, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &continuation1, 10, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &continuation2, 20, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &ready3, 30, 1, 2.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &ready4, 40, 1, 2.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::setEnergy(scheduler, 3.0);
    continuation1.releaseAt(Tick(0));
    continuation2.releaseAt(Tick(0));
    ready3.releaseAt(Tick(0));
    ready4.releaseAt(Tick(0));
    continuation1.markRunningWithoutScheduleCount();
    continuation2.markRunningWithoutScheduleCount();
    kernel.setRunning(&cpu0, &continuation1);
    kernel.setRunning(&cpu1, &continuation2);
    STSyncSchedulerTestPeer::enqueue(scheduler, &ready3);
    STSyncSchedulerTestPeer::enqueue(scheduler, &ready4);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_EQ(ready3.getScheduleCount(), 0);
    EXPECT_EQ(ready4.getScheduleCount(), 0);
    EXPECT_FALSE(continuation1.isExecuting());
    EXPECT_FALSE(continuation2.isExecuting());
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 3.0);
    EXPECT_DOUBLE_EQ(scheduler._stats.total_energy_consumed, 0.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, NoPartialBatchExecution) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-all-or-none-cpu0", nullptr);
    CPU cpu1("st-sync-all-or-none-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask first(1, 10, 10, 1.0);
    FakeSTSyncTask second(2, 20, 20, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &first, 10, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &second, 20, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &first);
    STSyncSchedulerTestPeer::enqueue(scheduler, &second);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    const bool first_ran = first.getScheduleCount() > 0;
    const bool second_ran = second.getScheduleCount() > 0;
    EXPECT_EQ(first_ran, second_ran);
    EXPECT_TRUE(first_ran);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, BatchBuiltFromActiveCandidates) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-active-batch-cpu0", nullptr);
    CPU cpu1("st-sync-active-batch-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask low_running(3, 30, 30, 1.0);
    FakeSTSyncTask high_a(1, 5, 5, 1.0);
    FakeSTSyncTask high_b(2, 10, 10, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &low_running, 30, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &high_a, 5, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &high_b, 10, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    low_running.releaseAt(Tick(0));
    high_a.releaseAt(Tick(0));
    high_b.releaseAt(Tick(0));
    low_running.markRunningWithoutScheduleCount();
    kernel.setRunning(&cpu0, &low_running);
    STSyncSchedulerTestPeer::enqueue(scheduler, &high_a);
    STSyncSchedulerTestPeer::enqueue(scheduler, &high_b);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_FALSE(low_running.isExecuting());
    EXPECT_EQ(high_a.getScheduleCount(), 1);
    EXPECT_EQ(high_b.getScheduleCount(), 1);
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &high_a));
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &high_b));
    EXPECT_FALSE(ContainsTask(scheduler.getCurrentBatchTasks(), &low_running));

    simulation.endSingleRun();
}

TEST(STSyncScheduler, RunningTaskMustBeInFrozenBatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu("st-sync-running-selected-cpu", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTSyncTask low(2, 20, 20, 1.0);
    FakeSTSyncTask high(1, 5, 5, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    low.releaseAt(Tick(0));
    high.releaseAt(Tick(0));
    low.markRunningWithoutScheduleCount();
    kernel.setRunning(&cpu, &low);
    STSyncSchedulerTestPeer::enqueue(scheduler, &high);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_FALSE(low.isExecuting());
    EXPECT_EQ(high.getScheduleCount(), 1);
    EXPECT_EQ(kernel.getTask(&cpu), &high);

    simulation.endSingleRun();
}

TEST(STSyncScheduler,
     InsufficientIdleCoreBatchDoesNotRunPartialContinuationDuringCharge) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-running-partial-cpu0", nullptr);
    CPU cpu1("st-sync-running-partial-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask running(2, 20, 20, 1.0);
    FakeSTSyncTask ready(1, 5, 5, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &running, 20, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &ready, 5, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.5);
    running.releaseAt(Tick(0));
    ready.releaseAt(Tick(0));
    running.markRunningWithoutScheduleCount();
    kernel.setRunning(&cpu0, &running);
    STSyncSchedulerTestPeer::enqueue(scheduler, &ready);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_FALSE(running.isExecuting());
    EXPECT_EQ(ready.getScheduleCount(), 0);
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_TRUE(STSyncSchedulerTestPeer::isInWaitingQueue(scheduler, &running));
    EXPECT_TRUE(STSyncSchedulerTestPeer::isInWaitingQueue(scheduler, &ready));
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.5);

    simulation.endSingleRun();
}

TEST(STSyncScheduler,
     ChargingSleepDoesNotLetContinuationRunForFreeAcrossTicks) {
    auto &simulation = MetaSim::Simulation::getInstance();
    ScopedSTSyncBaseHarvestRate zero_harvest(0.0);
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-charging-continuation-cpu0", nullptr);
    CPU cpu1("st-sync-charging-continuation-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask continuation(1, 5, 10, 2.0);
    FakeSTSyncTask blocked_new_job(2, 20, 20, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &continuation, 5, 2, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &blocked_new_job, 20, 1, 2.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 2.5);
    continuation.releaseAt(Tick(0));
    blocked_new_job.releaseAt(Tick(0));
    continuation.markRunningWithoutScheduleCount();
    kernel.setRunning(&cpu0, &continuation);
    STSyncSchedulerTestPeer::enqueue(scheduler, &blocked_new_job);

    STSyncSchedulerTestPeer::tick(scheduler);
    EXPECT_FALSE(continuation.isExecuting());
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_TRUE(STSyncSchedulerTestPeer::isInWaitingQueue(
        scheduler, &blocked_new_job));
    EXPECT_TRUE(STSyncSchedulerTestPeer::isInWaitingQueue(
        scheduler, &continuation));
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 2.5);

    STSyncTestActionEvent next_tick([&]() {
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    next_tick.post(Tick(1));
    simulation.run_to(Tick(1));

    EXPECT_FALSE(continuation.isExecuting());
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_TRUE(scheduler.isChargingSleepActive());
    EXPECT_EQ(blocked_new_job.getScheduleCount(), 0);
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 2.5);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, BlockedBatchMinSlackUsesActualBlockedGroup) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-blocked-slack-cpu0", nullptr);
    CPU cpu1("st-sync-blocked-slack-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask blocked_continuation(1, 5, 6, 1.0);
    FakeSTSyncTask blocked_new_job(2, 20, 10, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &blocked_continuation, 5, 1, 2.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &blocked_new_job, 20, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    blocked_continuation.releaseAt(Tick(0));
    blocked_new_job.releaseAt(Tick(0));
    blocked_continuation.markRunningWithoutScheduleCount();
    kernel.setRunning(&cpu0, &blocked_continuation);
    STSyncSchedulerTestPeer::enqueue(scheduler, &blocked_new_job);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_FALSE(blocked_continuation.isExecuting());
    EXPECT_EQ(blocked_new_job.getScheduleCount(), 0);
    EXPECT_TRUE(STSyncSchedulerTestPeer::isInWaitingQueue(
        scheduler, &blocked_continuation));
    EXPECT_TRUE(STSyncSchedulerTestPeer::isInWaitingQueue(
        scheduler, &blocked_new_job));
    EXPECT_TRUE(scheduler.getCurrentBatchTasks().empty());
    EXPECT_EQ(STSyncSchedulerTestPeer::groupWakeTime(scheduler), Tick(5));
    EXPECT_DOUBLE_EQ(scheduler.getCurrentEnergy(), 1.0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, BatchSizeUsesIdleCoresNotTotalCores) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-idle-size-cpu0", nullptr);
    CPU cpu1("st-sync-idle-size-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask running(1, 5, 10, 2.0);
    FakeSTSyncTask next(2, 10, 10, 1.0);
    FakeSTSyncTask waiting(3, 20, 20, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &running, 5, 2, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &next, 10, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &waiting, 20, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 2.0);
    running.releaseAt(Tick(0));
    next.releaseAt(Tick(0));
    waiting.releaseAt(Tick(0));
    running.markRunningWithoutScheduleCount();
    kernel.setRunning(&cpu0, &running);
    STSyncSchedulerTestPeer::enqueue(scheduler, &next);
    STSyncSchedulerTestPeer::enqueue(scheduler, &waiting);

    EXPECT_EQ(STSyncSchedulerTestPeer::batchSize(scheduler), 1);
    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_TRUE(running.isExecuting());
    EXPECT_EQ(next.getScheduleCount(), 1);
    EXPECT_EQ(waiting.getScheduleCount(), 0);
    EXPECT_EQ(scheduler.getCurrentBatchSize(), 2);
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &running));
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &next));

    simulation.endSingleRun();
}

TEST(STSyncScheduler, UsesRelativeDeadlineForGroupSlack) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    FakeSTSyncTask task(1, 20, 10, 3.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 20, 3, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    task.releaseAt(Tick(0));
    simulation.run_to(Tick(7));

    EXPECT_EQ(
        static_cast<int64_t>(STSyncSchedulerTestPeer::slack(scheduler, &task)),
        0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, UsesCeilRemainingExecution) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    FakeSTSyncTask task(1, 10, 10, 1.2);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 10, 2, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    task.releaseAt(Tick(0));
    simulation.run_to(Tick(8));

    // deadline 10 - current 8 - ceil(1.2) == 0.
    EXPECT_EQ(
        static_cast<int64_t>(STSyncSchedulerTestPeer::slack(scheduler, &task)),
        0);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, DeadlineMissKeepsJobAndUsesRelativeDeadline) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    FakeSTSyncTask task(1, 20, 5, 10.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &task, 20, 10, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    task.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &task);
    simulation.run_to(Tick(6));

    STSyncSchedulerTestPeer::cleanup(scheduler);
    STSyncSchedulerTestPeer::cleanup(scheduler);

    EXPECT_TRUE(STSyncSchedulerTestPeer::isInReadyQueue(scheduler, &task));
    EXPECT_EQ(STSyncSchedulerTestPeer::deadlineMisses(scheduler), 1);
    EXPECT_DOUBLE_EQ(task.getRemainingWCET(), 10.0);
    EXPECT_EQ(task.getDeadline(), Tick(5));

    simulation.endSingleRun();
}

TEST(STSyncScheduler, NoStaleEndDispatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu("st-sync-stale-dispatch-cpu", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTSyncTask low(2, 20, 20, 1.0);
    FakeSTSyncTask high(1, 5, 5, 1.0, 1);

    kernel.setContextSwitchDelay(Tick(1));
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 3.0);
    low.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &low);
    STSyncSchedulerTestPeer::tick(scheduler);

    STSyncTestActionEvent release_high([&]() {
        high.releaseAt(Tick(1));
        STSyncSchedulerTestPeer::arrive(scheduler, &high);
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    release_high.post(Tick(1));

    simulation.run_to(Tick(2));

    EXPECT_EQ(low.getScheduleCount(), 0);
    EXPECT_FALSE(low.isExecuting());
    EXPECT_EQ(high.getScheduleCount(), 1);
    EXPECT_TRUE(high.isExecuting());
    EXPECT_EQ(kernel.getTask(&cpu), &high);

    simulation.endSingleRun();
}

TEST(STSyncScheduler,
     PreemptedJobRequeuesAndResumesWhenEnergySufficient) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu("st-sync-resume-cpu", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTSyncTask low(2, 20, 20, 5.0);
    FakeSTSyncTask high(1, 5, 5, 1.0, 1);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 5, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 10.0);
    low.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &low);
    STSyncSchedulerTestPeer::tick(scheduler);

    STSyncTestActionEvent preempt([&]() {
        low.setRemaining(4.0);
        high.releaseAt(Tick(1));
        STSyncSchedulerTestPeer::arrive(scheduler, &high);
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    preempt.post(Tick(1));
    STSyncTestActionEvent resume([&]() {
        high.setRemaining(0.0);
        kernel.suspend(&high);
        STSyncSchedulerTestPeer::tick(scheduler);
    });
    resume.post(Tick(2));

    simulation.run_to(Tick(2));

    EXPECT_EQ(low.getScheduleCount(), 2);
    EXPECT_TRUE(low.isExecuting());
    EXPECT_EQ(kernel.getTask(&cpu), &low);
    EXPECT_DOUBLE_EQ(low.getRemainingWCET(), 4.0);
    EXPECT_EQ(low.getDeadline(), Tick(20));
    EXPECT_EQ(low.getArrival(), Tick(0));

    simulation.endSingleRun();
}

TEST(STSyncScheduler, StableRmTieBreak) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu("st-sync-tie-cpu", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTSyncTask task2(2, 10, 10, 1.0);
    FakeSTSyncTask task1(1, 10, 10, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &task2, 10, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &task1, 10, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.0);
    task2.releaseAt(Tick(0));
    task1.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &task2);
    STSyncSchedulerTestPeer::enqueue(scheduler, &task1);

    STSyncSchedulerTestPeer::tick(scheduler);
    simulation.run_to(Tick(0));

    EXPECT_EQ(task1.getScheduleCount(), 1);
    EXPECT_EQ(task2.getScheduleCount(), 0);
    EXPECT_EQ(kernel.getTask(&cpu), &task1);

    simulation.endSingleRun();
}

TEST(STSyncScheduler, NoMidTickArrivalRebuildBypassesFrozenBatch) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu("st-sync-mid-tick-cpu", nullptr);
    TestSTSyncMRTKernel kernel(&scheduler, std::set<CPU *>{&cpu});
    FakeSTSyncTask low(2, 20, 20, 1.0);
    FakeSTSyncTask high(1, 5, 5, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(scheduler, &low, 20, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(scheduler, &high, 5, 1, 1.0);

    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);
    STSyncSchedulerTestPeer::setEnergy(scheduler, 3.0);
    low.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::enqueue(scheduler, &low);
    STSyncSchedulerTestPeer::tick(scheduler);

    high.releaseAt(Tick(0));
    STSyncSchedulerTestPeer::arrive(scheduler, &high);
    simulation.run_to(Tick(0));

    EXPECT_EQ(low.getScheduleCount(), 1);
    EXPECT_EQ(high.getScheduleCount(), 0);
    EXPECT_TRUE(ContainsTask(scheduler.getCurrentBatchTasks(), &low));
    EXPECT_FALSE(ContainsTask(scheduler.getCurrentBatchTasks(), &high));

    simulation.endSingleRun();
}

TEST(STSyncScheduler, NewRunClearsWaitingGroupWakeAndCommittedState) {
    auto &simulation = MetaSim::Simulation::getInstance();
    TestSTSyncScheduler scheduler;
    CPU cpu0("st-sync-new-run-cpu0", nullptr);
    CPU cpu1("st-sync-new-run-cpu1", nullptr);
    TestSTSyncMRTKernel kernel(
        &scheduler, std::set<CPU *>{&cpu0, &cpu1});
    FakeSTSyncTask first(1, 100, 100, 1.0);
    FakeSTSyncTask second(2, 120, 120, 1.0);

    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &first, 100, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(
        scheduler, &second, 120, 1, 1.0);
    simulation.initSingleRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);

    STSyncSchedulerTestPeer::setEnergy(scheduler, 1.5);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    scheduler.insert(&first);
    scheduler.insert(&second);
    ASSERT_EQ(scheduler.baseQueueSize(), 2u);
    ASSERT_TRUE(scheduler.baseModel(&first)->isActive());
    ASSERT_TRUE(scheduler.baseModel(&second)->isActive());
    STSyncSchedulerTestPeer::tick(scheduler);
    ASSERT_TRUE(scheduler._is_charging_sleep);
    ASSERT_FALSE(scheduler._waiting_queue.empty());
    ASSERT_NE(scheduler._group_wake_event, nullptr);

    scheduler._selection_frozen = true;
    scheduler._energy_commit_valid = true;
    scheduler.newRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(scheduler);

    EXPECT_FALSE(scheduler._is_charging_sleep);
    EXPECT_FALSE(scheduler._deep_charging);
    EXPECT_FALSE(scheduler._energy_depleted);
    EXPECT_TRUE(scheduler._waiting_queue.empty());
    EXPECT_TRUE(scheduler._current_batch_tasks.empty());
    EXPECT_EQ(scheduler._group_wake_event, nullptr);
    EXPECT_DOUBLE_EQ(scheduler._st_group_required_energy, 0.0);
    EXPECT_EQ(scheduler._st_group_slack_at_begin, Tick(0));
    EXPECT_FALSE(scheduler._selection_frozen);
    EXPECT_FALSE(scheduler._energy_commit_valid);
    EXPECT_EQ(scheduler.baseQueueSize(), 0u);
    ASSERT_NE(scheduler.baseModel(&first), nullptr);
    ASSERT_NE(scheduler.baseModel(&second), nullptr);
    EXPECT_FALSE(scheduler.baseModel(&first)->isActive());
    EXPECT_FALSE(scheduler.baseModel(&second)->isActive());

    TestSTSyncScheduler fresh;
    CPU fresh_cpu0("st-sync-fresh-run-cpu0", nullptr);
    CPU fresh_cpu1("st-sync-fresh-run-cpu1", nullptr);
    TestSTSyncMRTKernel fresh_kernel(
        &fresh, std::set<CPU *>{&fresh_cpu0, &fresh_cpu1});
    FakeSTSyncTask fresh_first(3, 100, 100, 1.0);
    FakeSTSyncTask fresh_second(4, 120, 120, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(
        fresh, &fresh_first, 100, 1, 1.0);
    STSyncSchedulerTestPeer::addTaskModel(
        fresh, &fresh_second, 120, 1, 1.0);
    fresh.newRun();
    STSyncSchedulerTestPeer::cancelAutomaticTick(fresh);

    STSyncSchedulerTestPeer::setEnergy(scheduler, 5.0);
    STSyncSchedulerTestPeer::setEnergy(fresh, 5.0);
    first.releaseAt(Tick(0));
    second.releaseAt(Tick(0));
    fresh_first.releaseAt(Tick(0));
    fresh_second.releaseAt(Tick(0));
    scheduler.insert(&first);
    scheduler.insert(&second);
    fresh.insert(&fresh_first);
    fresh.insert(&fresh_second);
    STSyncSchedulerTestPeer::tick(scheduler);
    STSyncSchedulerTestPeer::tick(fresh);
    simulation.run_to(Tick(0));
    EXPECT_EQ(first.getScheduleCount(), 1);
    EXPECT_EQ(second.getScheduleCount(), 1);
    EXPECT_EQ(fresh_first.getScheduleCount(), 1);
    EXPECT_EQ(fresh_second.getScheduleCount(), 1);
    EXPECT_DOUBLE_EQ(
        scheduler.getCurrentEnergy(), fresh.getCurrentEnergy());
    EXPECT_EQ(scheduler.baseQueueSize(), fresh.baseQueueSize());

    simulation.endSingleRun();
}

} // namespace RTSim
