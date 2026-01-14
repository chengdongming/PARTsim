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

using RTSim::Mocks::KernelMock;

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
