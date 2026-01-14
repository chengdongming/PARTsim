#include <memory>

#include <gtest/gtest.h>

#include <metasim/simul.hpp>

#include <rtsim/scheduler/truefifo.hpp>
#include <rtsim/task.hpp>

#include "../mocks/kernel.hpp"

using MetaSim::Simulation;
using RTSim::Scheduler;
using RTSim::Task;
using RTSim::TrueFIFOScheduler;

using RTSim::Mocks::KernelMock;

TEST(Scheduler, TrueFIFO) {
    // Hide all output from the simulator
    // testing::internal::CaptureStdout();

    auto &simulation = Simulation::getInstance();
    auto kernel = KernelMock();

    std::unique_ptr<Scheduler> sched = std::make_unique<TrueFIFOScheduler>();

    auto tasks = std::vector<std::unique_ptr<Task>>();

    // Create 4 tasks; do not care about deadlines in this simulation
    for (int i = 0; i < 4; ++i) {
        tasks.emplace_back(std::make_unique<Task>(nullptr, 100));
        tasks.back()->insertCode("fixed(10,bzip2);");
        kernel.addTask(*tasks.back(), "");
    }

    // This operation only creates a model for the task and
    // it does not enqueue it!
    for (const auto &t : tasks) {
        sched->addTask(t.get(), "");
    }

    // This operation resets the scheduler
    simulation.initSingleRun();

    // Timing of the tasks used to test the fifo queue (in expected order):
    //
    // | Task  | Insertion Time |
    // | :---: | :------------: |
    // |   0   |       5        |
    // |   1   |       11       |
    // |   2   |       12       |
    // |   3   |       12*      |
    //
    // * inserted before at the same time but before 2, 2 will take precedence
    // because it has a lower task id

    // Inserting tasks into the queue

    // 0
    simulation.run_to(0);
    EXPECT_CALL(kernel, onArrival(tasks[0].get()));
    tasks[0]->activate(simulation.getTime());
    simulation.run_to(5);
    sched->insert(tasks[0].get());

    // 1,2,3
    simulation.run_to(10);
    EXPECT_CALL(kernel, onArrival(tasks[1].get()));
    EXPECT_CALL(kernel, onArrival(tasks[2].get()));
    EXPECT_CALL(kernel, onArrival(tasks[3].get()));
    tasks[1]->activate(simulation.getTime());
    tasks[2]->activate(simulation.getTime());
    tasks[3]->activate(simulation.getTime());

    // 1
    simulation.run_to(11);
    sched->insert(tasks[1].get());

    // 2,3 (inverted insertion order, but at the same time)
    simulation.run_to(12);
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

    // Ok, now let's try to remove and reinsert a task at a later time
    sched->extract(tasks[0].get());
    sched->extract(tasks[2].get());

    // These two must be at the end of the queue!
    simulation.run_to(20);
    sched->insert(tasks[2].get());
    simulation.run_to(21);
    sched->insert(tasks[0].get());

    auto task_3rd = dynamic_cast<Task *>(sched->getTaskN(2));
    auto task_4th = dynamic_cast<Task *>(sched->getTaskN(3));

    // Last two must be 2 and 0, in that order
    EXPECT_EQ(task_3rd, tasks[2].get())
        << "After reinsertion, task 2 is out of place!";
    EXPECT_EQ(task_4th, tasks[0].get())
        << "After reinsertion, task 0 is out of place!";

    sched->extract(tasks[2].get());
    sched->insert(tasks[2].get());
    task_4th = dynamic_cast<Task *>(sched->getTaskN(3));
    EXPECT_EQ(task_4th, tasks[2].get())
        << "After 2nd reinsertion, task 2 is out of place!";

    stasks = sched->getTasks();
    stask_it = stasks.begin();
    EXPECT_EQ((dynamic_cast<Task *>(*(stask_it++))), tasks[1].get())
        << "Wrong final ordering in iterator (" << 1 << ")!";

    EXPECT_EQ((dynamic_cast<Task *>(*(stask_it++))), tasks[3].get())
        << "Wrong final ordering in iterator (" << 3 << ")!";

    EXPECT_EQ((dynamic_cast<Task *>(*(stask_it++))), tasks[0].get())
        << "Wrong final ordering in iterator (" << 0 << ")!";

    EXPECT_EQ((dynamic_cast<Task *>(*(stask_it++))), tasks[2].get())
        << "Wrong final ordering in iterator (" << 2 << ")!";
}
