/*
  In this example, a simple system is simulated, consisting of two
  real-time tasks scheduled by EDF on a single processor.
*/
#include <rtsim/instr.hpp>
#include <rtsim/jtrace.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/taskstat.hpp>
#include <rtsim/texttrace.hpp>

using namespace MetaSim;
using namespace RTSim;

#define NRUNS 10000

int main() {
    try {
        SIMUL.dbg.enable(_TASK_DBG_LEV);
        SIMUL.dbg.enable(_INSTR_DBG_LEV);
        SIMUL.dbg.enable(_KERNEL_DBG_LEV);

        // TextTrace ttrace("trace.txt");

        std::cout << "Creating Scheduler and kernel" << std::endl;
        EDFScheduler edfsched;
        RTKernel kern(&edfsched);

        MissCount mc("miss");

        std::cout << "Creating the first task" << std::endl;
        PeriodicTask t1(7, 5, 0, "TaskA");

        try {
            std::cout << "Inserting code" << std::endl;
            t1.insertCode("delay(PDF(c1.txt));");
            t1.setAbort(false);
        } catch (std::string s) {
            std::cout << s << std::endl;
        }

        std::cout << "Creating the second task" << std::endl;
        PeriodicTask t2(11, 7, 0, "TaskB");

        std::cout << "Inserting code" << std::endl;
        t2.insertCode("delay(PDF(c2.txt));");
        t2.setAbort(false);

        std::cout << "Creating the third task" << std::endl;
        PeriodicTask t3(13, 10, 0, "TaskC");
        std::cout << "Inserting code" << std::endl;
        t3.insertCode("delay(PDF(c3.txt));");
        t3.setAbort(false);

        std::cout << "Setting up traces" << std::endl;

        // new way
        // ttrace.attachToTask(&t1);
        // ttrace.attachToTask(&t2);
        // ttrace.attachToTask(&t3);

        mc.attachToTask(&t1);
        mc.attachToTask(&t2);
        mc.attachToTask(&t3);

        std::cout << "Adding tasks to schedulers" << std::endl;

        kern.addTask(t1, "");
        kern.addTask(t2, "");
        kern.addTask(t3, "");

        std::cout << "Ready to run!" << std::endl;

        int count = 0;
        for (int i = 0; i < 10000; i++) {
            // run the simulation for 500 units of time
            SIMUL.run(1001, 1);

            std::cout << "Number of Deadline misses:" << mc.getLastValue()
                      << std::endl;
            if (mc.getLastValue() > 0)
                count++;
        }
        cout << "Total count = " << count << std::endl;
        cout << "DM Perc     = " << double(count) / NRUNS * 100 << std::endl;
        cout << "Corr.       = " << (1 - double(count) / NRUNS) * 100
             << std::endl;
    } catch (BaseExc &e) {
        std::cout << e.what() << std::endl;
    }
}
