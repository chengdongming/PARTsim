#include <memory>

#include <gtest/gtest.h>

#include <metasim/simul.hpp>

#include <rtsim/scheduler/fifosched.hpp>
#include <rtsim/task.hpp>

#include "../mocks/kernel.hpp"

using MetaSim::Simulation;
using RTSim::FIFOScheduler;
using RTSim::Scheduler;
using RTSim::Task;

using RTSim::Mocks::KernelMock;

TEST(Scheduler, FIFO) {
    // Hide all output from the simulator
    // testing::internal::CaptureStdout();

    auto &simulation = Simulation::getInstance();
    auto kernel = KernelMock();

    std::unique_ptr<Scheduler> sched = std::make_unique<FIFOScheduler>();

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
    // | Task  | Activation Time | Insertion Time |
    // | :---: | :-------------: | :------------: |
    // |   0   |        0        |       5        |
    // |   1   |       10        |       11       |
    // |   2   |       10        |       12       |
    // |   3   |       10        |       12*      |
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
}

TEST(FIFORegression, ArrivalOrderWinsWhenInsertionOrderIsReversed) {
    auto &simulation = Simulation::getInstance();
    KernelMock kernel;
    FIFOScheduler sched;
    Task earlier_arrival(nullptr, 100);
    Task later_arrival(nullptr, 100);
    earlier_arrival.insertCode("fixed(1,bzip2);");
    later_arrival.insertCode("fixed(1,bzip2);");
    kernel.addTask(earlier_arrival, "");
    kernel.addTask(later_arrival, "");
    sched.addTask(&earlier_arrival, "");
    sched.addTask(&later_arrival, "");
    simulation.initSingleRun();

    simulation.run_to(0);
    EXPECT_CALL(kernel, onArrival(&earlier_arrival));
    earlier_arrival.activate(simulation.getTime());
    simulation.run_to(10);
    EXPECT_CALL(kernel, onArrival(&later_arrival));
    later_arrival.activate(simulation.getTime());

    simulation.run_to(11);
    sched.insert(&later_arrival);
    simulation.run_to(12);
    sched.insert(&earlier_arrival);

    EXPECT_EQ(sched.getTaskN(0), &earlier_arrival);
    EXPECT_EQ(sched.getTaskN(1), &later_arrival);
}

TEST(FIFORegression, SameArrivalUsesEarlierInsertionTime) {
    auto &simulation = Simulation::getInstance();
    KernelMock kernel;
    FIFOScheduler sched;
    Task inserted_later(nullptr, 100);
    Task inserted_earlier(nullptr, 100);
    inserted_later.insertCode("fixed(1,bzip2);");
    inserted_earlier.insertCode("fixed(1,bzip2);");
    kernel.addTask(inserted_later, "");
    kernel.addTask(inserted_earlier, "");
    sched.addTask(&inserted_later, "");
    sched.addTask(&inserted_earlier, "");
    simulation.initSingleRun();

    simulation.run_to(0);
    EXPECT_CALL(kernel, onArrival(&inserted_later));
    EXPECT_CALL(kernel, onArrival(&inserted_earlier));
    inserted_later.activate(simulation.getTime());
    inserted_earlier.activate(simulation.getTime());

    simulation.run_to(1);
    sched.insert(&inserted_earlier);
    simulation.run_to(2);
    sched.insert(&inserted_later);

    EXPECT_EQ(sched.getTaskN(0), &inserted_earlier);
    EXPECT_EQ(sched.getTaskN(1), &inserted_later);
}

TEST(FIFORegression, SameArrivalAndInsertionUsesTaskNumber) {
    auto &simulation = Simulation::getInstance();
    KernelMock kernel;
    FIFOScheduler sched;
    Task lower_number(nullptr, 100);
    Task higher_number(nullptr, 100);
    lower_number.insertCode("fixed(1,bzip2);");
    higher_number.insertCode("fixed(1,bzip2);");
    kernel.addTask(lower_number, "");
    kernel.addTask(higher_number, "");
    sched.addTask(&lower_number, "");
    sched.addTask(&higher_number, "");
    simulation.initSingleRun();

    simulation.run_to(0);
    EXPECT_CALL(kernel, onArrival(&lower_number));
    EXPECT_CALL(kernel, onArrival(&higher_number));
    lower_number.activate(simulation.getTime());
    higher_number.activate(simulation.getTime());

    simulation.run_to(1);
    sched.insert(&higher_number);
    sched.insert(&lower_number);

    EXPECT_EQ(sched.getTaskN(0), &lower_number);
    EXPECT_EQ(sched.getTaskN(1), &higher_number);
    EXPECT_EQ(sched.getSize(), 2);
}
