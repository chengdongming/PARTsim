/*
  In this example, a simple system is simulated, consisting of two
  real-time tasks scheduled by EDF on a single processor.
*/
#include <rtsim/instr.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/jtrace.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/texttrace.hpp>

using namespace MetaSim;
using namespace RTSim;

int main() {
    try {
        SIMUL.dbg.enable("All");
        SIMUL.dbg.setStream("debug.txt");
        // set the trace file. This can be visualized by the
        // rttracer tool
        // JavaTrace jtrace("trace.trc");

        TextTrace ttrace("trace.txt");
        JSONTrace jtrace("trace.json");

        std::cout << "Creating Scheduler and kernel" << std::endl;
        EDFScheduler edfsched;
        RTKernel kern(&edfsched);

        std::cout << "Creating the first task" << std::endl;
        PeriodicTask t1(4, 4, 0, "Task0");

        std::cout << "Inserting code" << std::endl;
        t1.insertCode("fixed(2);");
        // t1.setAbort(false);

        std::cout << "Creating the second task" << std::endl;
        PeriodicTask t2(5, 5, 0, "Task1");

        std::cout << "Inserting code" << std::endl;
        t2.insertCode("fixed(2);");
        // t2.setAbort(false);

        std::cout << "Creating the third task" << std::endl;
        PeriodicTask t3(6, 6, 0, "Task2");
        std::cout << "Inserting code" << std::endl;
        t3.insertCode("fixed(2);");
        // t3.setAbort(false);

        std::cout << "Setting up traces" << std::endl;

        // new way
        ttrace.attachToTask(t1);
        ttrace.attachToTask(t2);
        ttrace.attachToTask(t3);

        jtrace.attachToTask(t1);
        jtrace.attachToTask(t2);
        jtrace.attachToTask(t3);

        std::cout << "Adding tasks to schedulers" << std::endl;

        kern.addTask(t1, "");
        kern.addTask(t2, "");
        kern.addTask(t3, "");
        //        kern.addTask(t4, "");

        std::cout << "Ready to run!" << std::endl;
        // run the simulation for 500 units of time
        SIMUL.run(500);
    } catch (BaseExc &e) {
        std::cout << e.what() << std::endl;
    }
}
