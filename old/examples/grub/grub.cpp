#include <rtsim/grubserver.hpp>
#include <rtsim/jtrace.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/scheduler/fifosched.hpp>
#include <rtsim/texttrace.hpp>

using namespace MetaSim;
using namespace RTSim;

int main() {
    try {
        JavaTrace jtrace("trace.trc");
        TextTrace ttrace("trace.txt");

        // create the scheduler and the kernel
        EDFScheduler sched;
        RTKernel kern(&sched);

        PeriodicTask t2(8, 4, 0, "TaskA");
        t2.insertCode("fixed(2);");
        t2.setAbort(false);

        PeriodicTask t3(6, 6, 0, "TaskB");
        t3.insertCode("fixed(4);");
        t3.setAbort(false);

        ttrace.attachToTask(t2);
        ttrace.attachToTask(t3);

        Grub serv1(2, 4, "HIGH", "fifo");
        serv1.addTask(t2);
        kern.addTask(serv1, "");

        Grub serv2(3, 6, "LOW", "fifo");
        serv2.addTask(t3);
        kern.addTask(serv2, "");

        GrubSupervisor super;

        bool flag1 = super.addGrub(&serv1);
        std::cout << "Server1 added = " << flag1 << std::endl;

        bool flag2 = super.addGrub(&serv2);
        std::cout << "Server2 added = " << flag2 << std::endl;

        SIMUL.dbg.enable(_TASK_DBG_LEV);
        SIMUL.dbg.enable(_KERNEL_DBG_LEV);
        SIMUL.dbg.enable(_SERVER_DBG_LEV);
        SIMUL.run(24);
    } catch (BaseExc &e) {
        std::cout << e.what() << std::endl;
    } catch (parse_util::ParseExc &e2) {
        std::cout << e2.what() << std::endl;
    }
}
