#include <fstream>
#include <iostream>
#include <sstream>
#include <vector>

#include <rtsim/grubserver.hpp>
#include <rtsim/jtrace.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/load.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/scheduler/fifosched.hpp>
#include <rtsim/taskstat.hpp>
#include <rtsim/texttrace.hpp>

using namespace MetaSim;
using namespace RTSim;

using namespace std;

struct Stats {
    MissCount miss_count;
    MissPercentage miss_perc;
    TardinessStat<StatMean> tard;

    void dump(double par, const std::string &filename) {
        string fname = filename + "_miss_count.txt";
        ofstream m;
        m.open(fname.c_str(), ios::app);
        m << par << ", " << miss_count.getMean() << ", "
          << miss_count.getConfInterval() << std::endl;
        m.close();

        fname = filename + "_miss_perc.txt";
        ofstream p;
        p.open(fname.c_str(), ios::app);
        p << par << ", " << miss_perc.getMean() << ", "
          << miss_perc.getConfInterval() << std::endl;
        p.close();

        fname = filename + "_tard.txt";
        ofstream t;
        t.open(fname.c_str(), ios::app);
        t << par << ", " << tard.getMean() << ", " << tard.getConfInterval()
          << std::endl;
        t.close();
    }
    void newStatRun() {
        miss_count.newRun();
        miss_perc.newRun();
        tard.newRun();
    }
};

class MySim {
public:
    EDFScheduler sched;
    RTKernel kern;
    GrubSupervisor super;
    TextTrace ttrace;

    vector<Grub *> servers;
    Grub *LowServer;
    double hl_ratio;
    double HighUtil;
    double HighLowUtil;
    double LowUtil;
    double upper_limit;

    MySim(double u, double hl, double ul = 0.99) :
        sched(),
        kern(&sched),
        super(),
        ttrace("trace.txt"),
        servers(),
        LowServer(0),
        hl_ratio(hl),
        HighUtil(u),
        upper_limit(ul) {
        HighLowUtil = HighUtil * hl_ratio;
        LowUtil = upper_limit - HighLowUtil;
    }

    void add_to_sim(Grub *s, Task &task) {
        s->addTask(task);
        servers.push_back(s);
        bool flag = super.addGrub(s);
        cout << "Grub added: " << flag << std::endl;
        kern.addTask(*s, "");
        ttrace.attachToTask(task);
    }

    void create_high_server(Task &task) {
        Grub *s =
            new Grub(task.getWCET(), Tick(double(task.getMinIAT()) * hl_ratio),
                     "", "fifo");
        cout << "Created a server with period : "
             << Tick(double(task.getMinIAT()) * hl_ratio) << std::endl;
        add_to_sim(s, task);
    }

    void create_low_server(Task &task, double lu) {
        double ratio = (upper_limit - HighUtil) / lu;
        if (ratio > 1)
            ratio = 1;
        Grub *s = new Grub(Tick(double(task.getWCET()) * ratio),
                           task.getMinIAT(), "", "fifo");
        cout << "Created a LOW server with budget : "
             << Tick(double(task.getWCET()) * ratio) << std::endl;
        add_to_sim(s, task);
    }

    void create_single_server(Task &task, Tick period) {
        if (LowServer == 0) {
            Tick cap = Tick(double(period) * (upper_limit - HighUtil));
            LowServer = new Grub(cap, period, "", "edf");
            bool flag = super.addGrub(LowServer);
            std::cout << "Single Grub added: " << flag << std::endl;
            kern.addTask(*LowServer, "");
            servers.push_back(LowServer);
        }
        LowServer->addTask(task);
        ttrace.attachToTask(task);
    }

    ~MySim() {
        cleanVector(servers);
    }
};
