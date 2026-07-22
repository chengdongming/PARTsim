// NOTE: the RMScheduler is a Deadline Monotonic Scheduler

#include <memory>

#include <gtest/gtest.h>

#include <metasim/simul.hpp>

#include <rtsim/scheduler/rmsched.hpp>
#include <rtsim/task.hpp>

#include "../mocks/kernel.hpp"

using MetaSim::Simulation;
using RTSim::RMScheduler;
using RTSim::Scheduler;
using RTSim::Task;
using RTSim::TaskModel;

using RTSim::Mocks::KernelMock;

namespace {
    class InspectableRMScheduler : public RMScheduler {
    public:
        TaskModel *modelFor(RTSim::AbsRTTask *task) {
            return find(task);
        }

        MetaSim::Tick directModelPriority(RTSim::AbsRTTask *task) {
            auto *model = static_cast<RMModel *>(find(task));
            return model->RMModel::getPriority();
        }

        void changeModelPriority(RTSim::AbsRTTask *task,
                                 MetaSim::Tick priority) {
            find(task)->changePriority(priority);
        }
    };
}

TEST(Scheduler, RM) {
    auto &simulation = Simulation::getInstance();
    auto kernel = KernelMock();

    std::unique_ptr<Scheduler> sched = std::make_unique<RMScheduler>();

    auto deadlines = std::vector<int>{100, 200, 200, 200};
    auto tasks = std::vector<std::unique_ptr<Task>>();

    // Create 4 tasks; do not care about deadlines in this simulation
    for (int i = 0; i < 4; ++i) {
        tasks.emplace_back(std::make_unique<Task>(nullptr, deadlines[i]));
        tasks.back()->insertCode("fixed(10,bzip2);");
        kernel.addTask(*tasks.back(), "");
    }

    // This operation only creates a model for the task and
    // it does not enqueue it!
    for (auto &t : tasks) {
        sched->addTask(t.get(), "");
    }

    // This operation resets the scheduler
    simulation.initSingleRun();

    // Timing of the tasks used to test the fifo queue (in expected order):
    //
    // | Task  | Relative Deadline | Insertion Time |
    // | :---: | :---------------: | :------------: |
    // |   0   |        100        |        5       |
    // |   1   |        200        |        6       |
    // |   2   |        200        |        7       |
    // |   3   |        200        |        7*      |
    //
    // * inserted before at the same time but before 2, 2 will take precedence
    // because it has a lower task id

    // All tasks arrive at the same time
    simulation.run_to(0);
    for (auto &t : tasks) {
        EXPECT_CALL(kernel, onArrival(t.get()));
        t->activate(simulation.getTime());
    }

    // Inserting tasks into the queue

    // 0
    simulation.run_to(5);
    sched->insert(tasks[0].get());

    // 1
    simulation.run_to(6);
    sched->insert(tasks[1].get());

    // 2,3 (inverted insertion order, but at the same time)
    simulation.run_to(7);
    sched->insert(tasks[3].get());
    sched->insert(tasks[2].get());

    // Now iterate the list of tasks and the scheduler list,
    // they must have the same order!
    auto stasks = sched->getTasks();
    decltype(tasks.begin()) task_it;
    decltype(stasks.begin()) stask_it;
    int i;
    for (task_it = tasks.begin(), stask_it = stasks.begin(), i = 0;
         task_it != tasks.end(); ++task_it, ++stask_it, ++i) {
        EXPECT_EQ((*task_it).get(), (*stask_it))
            << "Task" << i << "is out of place!";
    }

    // Also, the scheduler queue must be the right size!
    ASSERT_EQ(stask_it, stasks.end()) << "Too many tasks in schedule!";
}

TEST(RMRegression, DeadlineWinsWhenInsertionOrderIsOpposite) {
    auto &simulation = Simulation::getInstance();
    InspectableRMScheduler sched;
    Task earlier_insert(nullptr, 200);
    Task later_insert(nullptr, 100);
    earlier_insert.insertCode("fixed(1,bzip2);");
    later_insert.insertCode("fixed(1,bzip2);");
    sched.addTask(&earlier_insert, "");
    sched.addTask(&later_insert, "");
    simulation.initSingleRun();

    simulation.run_to(1);
    sched.insert(&earlier_insert);
    simulation.run_to(2);
    sched.insert(&later_insert);

    EXPECT_EQ(sched.getTaskN(0), &later_insert);
    EXPECT_EQ(sched.getTaskN(1), &earlier_insert);
}

TEST(RMRegression, SameDeadlineUsesEarlierInsertionTime) {
    auto &simulation = Simulation::getInstance();
    InspectableRMScheduler sched;
    Task inserted_later(nullptr, 100);
    Task inserted_earlier(nullptr, 100);
    inserted_later.insertCode("fixed(1,bzip2);");
    inserted_earlier.insertCode("fixed(1,bzip2);");
    sched.addTask(&inserted_later, "");
    sched.addTask(&inserted_earlier, "");
    simulation.initSingleRun();

    simulation.run_to(1);
    sched.insert(&inserted_earlier);
    simulation.run_to(2);
    sched.insert(&inserted_later);

    EXPECT_EQ(sched.getTaskN(0), &inserted_earlier);
    EXPECT_EQ(sched.getTaskN(1), &inserted_later);
}

TEST(RMRegression, SameDeadlineAndTimeUsesTaskNumber) {
    auto &simulation = Simulation::getInstance();
    InspectableRMScheduler sched;
    Task lower_number(nullptr, 100);
    Task higher_number(nullptr, 100);
    lower_number.insertCode("fixed(1,bzip2);");
    higher_number.insertCode("fixed(1,bzip2);");
    sched.addTask(&lower_number, "");
    sched.addTask(&higher_number, "");
    simulation.initSingleRun();

    simulation.run_to(1);
    sched.insert(&higher_number);
    sched.insert(&lower_number);

    EXPECT_EQ(sched.getTaskN(0), &lower_number);
    EXPECT_EQ(sched.getTaskN(1), &higher_number);
    EXPECT_EQ(sched.getSize(), 2);
}

TEST(RMRegression, ExternalPriorityOverridesRelativeDeadline) {
    auto &simulation = Simulation::getInstance();
    InspectableRMScheduler sched;
    Task externally_raised(nullptr, 200);
    Task default_priority(nullptr, 100);
    externally_raised.insertCode("fixed(1,bzip2);");
    default_priority.insertCode("fixed(1,bzip2);");
    sched.addTask(&externally_raised, "");
    sched.addTask(&default_priority, "");
    sched.changeModelPriority(&externally_raised, 50);
    simulation.initSingleRun();

    simulation.run_to(1);
    sched.insert(&default_priority);
    sched.insert(&externally_raised);

    EXPECT_EQ(sched.getPriority(&externally_raised), 50);
    EXPECT_EQ(sched.directModelPriority(&externally_raised), 50);
    EXPECT_EQ(sched.getTaskN(0), &externally_raised);
}

TEST(RMRegression, RelativeDeadlineValueRestoresDefaultPriority) {
    InspectableRMScheduler sched;
    Task task(nullptr, 200);
    sched.addTask(&task, "");

    sched.changeModelPriority(&task, 50);
    ASSERT_EQ(sched.getPriority(&task), 50);
    sched.changeModelPriority(&task, task.getRelDline());

    EXPECT_EQ(sched.getPriority(&task), task.getRelDline());
    EXPECT_EQ(sched.directModelPriority(&task), task.getRelDline());
}

TEST(RMRegression, DirectAndBaseVirtualPriorityCallsAgree) {
    InspectableRMScheduler sched;
    Task task(nullptr, 137);
    sched.addTask(&task, "");

    EXPECT_EQ(sched.directModelPriority(&task), 137);
    EXPECT_EQ(sched.modelFor(&task)->getPriority(), 137);
    EXPECT_EQ(sched.getPriority(&task), 137);

    sched.changeModelPriority(&task, 42);
    EXPECT_EQ(sched.directModelPriority(&task), 42);
    EXPECT_EQ(sched.modelFor(&task)->getPriority(), 42);
    EXPECT_EQ(sched.getPriority(&task), 42);
}
