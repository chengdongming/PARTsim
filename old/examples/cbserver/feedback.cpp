#include <cstdlib>
#include <rtsim/feedbackarsim.hpp>
#include <rtsim/feedbacktest.hpp>
#include <rtsim/jtrace.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/scheduler/rrsched.hpp>
#include <rtsim/supercbs.hpp>
#include <rtsim/taskstat.hpp>
#include <rtsim/texttrace.hpp>

using namespace MetaSim;
using namespace RTSim;

class System {
public:
    EDFScheduler sched;
    RTKernel kern;

    PeriodicTask mm1;
    PeriodicTask mm2;

    CBServer ss1;
    CBServer ss2;

    MissPercentage miss1;
    MissPercentage miss2;

    System(Tick b1, Tick b2, const string &trace1, const string &trace2);
};

System::System(Tick b1, Tick b2, const string &trace1, const string &trace2) :
    sched(),
    kern(&sched),

    mm1(40000, 40000, 0, "MM1"),
    mm2(100000, 100000, 0, "MM2"),
    ss1(b1, 40000, 40000, false, "SS1", "fifo"),
    ss2(b2, 100000, 100000, false, "SS2", "fifo"),
    miss1("miss1"),
    miss2("miss2") {
    mm1.insertCode("delay(trace(" + trace1 + "));");
    mm2.insertCode("delay(trace(" + trace2 + "));");

    ss1.addTask(mm1);
    ss2.addTask(mm2);

    kern.addTask(ss1, "");
    kern.addTask(ss2, "");

    miss1.attachToTask(&mm1);
    miss2.attachToTask(&mm2);
}

void prepare_cbserver(System &sys) {}

void prepare_feedback(System &sys) {}

int main(int argc, char *argv[]) {
    if (argc < 6) {
        std::cout << "Usage: " << argv[0] << " <fix|fback> "
                  << "<budget 1> <budget 2> <tracefile 1> <tracefile 2>"
                  << std::endl;
        exit(0);
    }
    try {
        Tick b1, b2;
        string tracefile1, tracefile2;

        b1 = atoi(argv[2]);
        b2 = atoi(argv[3]);

        tracefile1 = string(argv[4]);
        tracefile2 = string(argv[5]);

        JavaTrace jtrace("spare.trc");

        SIMUL.dbg.enable(_EVENT_DBG_LEV);
        SIMUL.dbg.enable(_TASK_DBG_LEV);
        SIMUL.dbg.enable(_KERNEL_DBG_LEV);
        SIMUL.dbg.enable(_SERVER_DBG_LEV);

        System sys(b1, b2, tracefile1, tracefile2);

        sys.mm1.setTrace(&jtrace);
        sys.mm2.setTrace(&jtrace);

        if (string(argv[1]) == "fix") {
            prepare_cbserver(sys);
        } else if (string(argv[1]) == "fback") {
            prepare_feedback(sys);
        } else {
            std::cout << "Usage: " << argv[0] << " <fix|fback> "
                      << "<budget 1> <budget 2> <tracefile 1> <tracefile 2>"
                      << std::endl;
            exit(0);
        }

        //  SparePot sp("SparePot");

        SuperCBS feedback("CBS");
        // sp.setSpare(5000, 40000);
        // SparePot::row_t myrow;
        // myrow.push_back(1);
        // myrow.push_back(1);

        feedback.addServer(&sys.ss1); // , myrow);
        // myrow.push_back(1);
        feedback.addServer(&sys.ss2); //, myrow);

        // sp.compute_matrix(4000, 39000);

        FeedbackModuleARSim ftm1("FTM1");
        ftm1.setSupervisor(&feedback, &sys.ss1);
        ftm1.setTask(&sys.mm1);
        sys.mm1.setFeedbackModule(&ftm1);

        FeedbackModuleARSim ftm2("FTM2");
        ftm2.setSupervisor(&feedback, &sys.ss2);
        ftm2.setTask(&sys.mm2);
        sys.mm2.setFeedbackModule(&ftm2);

        ftm1.setControllerParams("-s sdb -T 40000 -B 0.5");
        ftm2.setControllerParams("-s sdb -T 100000 -B 0.5");

        SIMUL.run(3000000);

        std::cout << "Percentage of deadline misses for task mm1: "
                  << sys.miss1.getLastValue() << " - " << sys.miss1.getValue()
                  << std::endl;
        std::cout << "Percentage of deadline misses for task mm2: "
                  << sys.miss2.getLastValue() << std::endl;
    } catch (BaseExc &e) {
        std::cout << e.what() << std::endl; // 47447166 Tecnave sa
    } catch (parse_util::ParseExc &e2) {
        std::cout << e2.what() << std::endl;
    }
}
