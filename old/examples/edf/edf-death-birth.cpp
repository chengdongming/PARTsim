/*
  In this example, a simple system is simulated, consisting of a few
  random real-time tasks scheduled by EDF on a single processor, where one
  task is killed at a random time, and another one with a random period and
  maximum possible budget is created, to check whether any deadline miss occurs
*/
#include <metasim/plist.hpp>
#include <rtsim/instr.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/jtrace.hpp>
#include <rtsim/kernel.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/texttrace.hpp>

#include <experimental/random>
#include <sys/time.h>

using namespace MetaSim;
using namespace RTSim;

static deque<PeriodicTask *> tasks;
static int tid = 0;
static double ustart = 0.9;
static int nmax = 10;
static int tokill = 1;
static double sat_factor = 0.0;

static double getUtot() {
    double usum = 0.0;
    for (auto t : tasks)
        usum += double(t->getWCET()) / double(t->getPeriod());
    return usum;
}

static Tick getMaxPeriod() {
    Tick period = 0;
    for (auto t : tasks)
        if (t->getPeriod() > period)
            period = t->getPeriod();
    return period;
}

// compute the area as sum of horizontal rects (time on X axis)
static Tick
    computeMaxRuntime(Tick now, Tick period, double uavail,
                      const std::vector<pair<Tick, double>> &expiring_uacts) {
    Tick ref_end = now + period;
    double max_runtime = uavail * double(ref_end - now);
    std::cout << "computeMaxRuntime: max_runtime=" << max_runtime
              << ", uavail=" << uavail << std::endl;
    for (auto it = expiring_uacts.begin();
         it != expiring_uacts.end() && it->first <= ref_end; ++it) {
        max_runtime += it->second * double(ref_end - it->first);
        std::cout << "computeMaxRuntime: max_runtime=" << max_runtime
                  << std::endl;
    }
    return Tick::floor(max_runtime);
}

int main(int argc, char *argv[]) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    long seed = ts.tv_sec * 1000000000L + ts.tv_nsec;
    --argc;
    ++argv;
    while (argc > 0) {
        if (strcmp(*argv, "-h") == 0) {
            printf("Usage: edf-death-birth [-s seed][-n nmax_tasks][-k "
                   "tokill][-u umax][-f sat_factor]\n");
            exit(0);
        } else if (strcmp(*argv, "-s") == 0) {
            --argc;
            ++argv;
            assert(argc > 0);
            seed = atoi(*argv);
        } else if (strcmp(*argv, "-n") == 0) {
            --argc;
            ++argv;
            assert(argc > 0);
            nmax = atoi(*argv);
        } else if (strcmp(*argv, "-u") == 0) {
            --argc;
            ++argv;
            assert(argc > 0);
            ustart = atof(*argv);
            assert(ustart > 0.0 && ustart <= 1.0);
        } else if (strcmp(*argv, "-f") == 0) {
            --argc;
            ++argv;
            assert(argc > 0);
            sat_factor = atof(*argv);
            assert(sat_factor >= 0.0 && sat_factor <= 1.0);
        } else if (strcmp(*argv, "-k") == 0) {
            --argc;
            ++argv;
            assert(argc > 0);
            tokill = atoi(*argv);
        }
        --argc;
        ++argv;
    }

    std::cout << "Running with: seed=" << seed << ", nmax=" << nmax
              << ", tokill=" << tokill << ", utot=" << ustart
              << ", sat_factor=" << sat_factor << std::endl;

    assert(nmax > tokill);

    try {
        SIMUL.dbg.enable("All");
        // SIMUL.dbg.setStream("debug.txt");
        // set the trace file. This can be visualized by the
        // rttracer tool

        TextTrace ttrace("trace.txt");

        std::cout << "Creating Scheduler and kernel" << std::endl;
        EDFScheduler edfsched;
        RTKernel kern(&edfsched);

        std::cout << "Simulation seed: " << seed << std::endl;
        std::experimental::reseed(seed);

        int n = std::experimental::randint(tokill + 1, nmax);
        vector<pair<Tick, Tick>> tset(n);
        double usum = 0.0;
        for (int i = 0; i < n; i++) {
            Tick period = std::experimental::randint(10, 20) * 100;
            Tick runtime =
                std::experimental::randint(int(period / 4), int(period / 2));
            std::cout << "generated " << runtime << ", " << period << std::endl;
            tset[i] = pair<Tick, Tick>(runtime, period);
            usum += double(runtime) / double(period);
        }
        std::cout << "usum: " << usum << std::endl;

        for (int i = 0; i < n; i++) {
            Tick period = tset[i].second;
            Tick runtime = Tick(double(tset[i].first) * (ustart / usum));
            std::cout << "creating (normalized) runtime: " << runtime
                      << ", period: " << period << std::endl;
            PeriodicTask *t = new PeriodicTask(period, period, 0,
                                               string("T") + to_string(tid++));
            t->insertCode(string("fixed(") + to_string((int) runtime) + ");");
            ttrace.attachToTask(*t);
            kern.addTask(*t, "");
            tasks.push_back(t);
        }

        double utot = getUtot();
        std::cout << "ustart=" << ustart << ", Utot=" << utot << std::endl;

        SIMUL.initSingleRun();

        int killable;
        Tick now;
        Tick maxvtime_now;
        do {
            Tick next = std::experimental::randint(int(getMaxPeriod() * 2),
                                                   int(getMaxPeriod() * 10));
            std::cout << "Running till time " << next << std::endl;

            SIMUL.run_to(next);
            now = SIMUL.getTime();
            std::cout << "sim paused at: " << now << std::endl;

            killable = 0;
            maxvtime_now = 0;
            for (auto t : tasks) {
                Tick vtime_now =
                    t->getArrival() +
                    t->getExecTime() * t->getPeriod() / t->getWCET() - now;
                if (vtime_now > 10) {
                    killable++;
                    if (vtime_now > maxvtime_now)
                        maxvtime_now = vtime_now;
                }
            }
        } while (killable < tokill || maxvtime_now < 100);

        std::vector<pair<Tick, double>> expiring_uacts;
        int nkilled = 0;
        for (int i = 0; i < tokill; i++) {
            for (auto it = tasks.begin(); it != tasks.end(); ++it) {
                auto t = *it;
                double ukill = double(t->getWCET()) / double(t->getPeriod());
                Tick vtime = t->getArrival() +
                             t->getExecTime() * t->getPeriod() / t->getWCET();
                if (vtime <= now + 10)
                    continue;
                expiring_uacts.push_back(make_pair(
                    vtime, double(t->getWCET()) / double(t->getPeriod())));
                tasks.erase(it);
                t->killInstance();
                ++nkilled;
                break;
            }
        }

        assert(nkilled == tokill);

        sort(expiring_uacts.begin(), expiring_uacts.end(),
             [](const pair<Tick, double> &p1, const pair<Tick, double> &p2) {
                 return p1.first < p2.first;
             });

        std::cout << "expiring_uacts: ";
        for (auto p : expiring_uacts) {
            std::cout << "(" << p.first << ", " << p.second << "), ";
        }
        std::cout << std::endl;

        Tick a = expiring_uacts.front().first - now;
        Tick b = (expiring_uacts.back().first - now) * 2;
        std::cout << "a=" << a << ", b=" << b << std::endl;
        assert(a > 0 && b > 0);
        Tick period = std::experimental::randint((int) a, (int) b);
        double Uavail = 1.0 - utot;
        Tick min_runtime = Tick::floor(Uavail * double(period));
        Tick max_runtime =
            computeMaxRuntime(now, period, Uavail, expiring_uacts);
        Tick min_runtime_sat =
            min_runtime + Tick(sat_factor * double(max_runtime - min_runtime));
        Tick runtime = std::experimental::randint((int) min_runtime_sat,
                                                  (int) max_runtime);
        std::cout << std::endl;
        std::cout << "New task: runtime=" << runtime << ", period=" << period
                  << ", max_runtime=" << max_runtime
                  << ", min_runtime_sat=" << min_runtime_sat
                  << ", min_runtime=" << min_runtime << std::endl
                  << std::endl;
        std::cout << "creating " << runtime << ", " << period << std::endl;
        PeriodicTask *t =
            new PeriodicTask(period, period, 0, string("T") + to_string(tid++));
        t->insertCode(string("fixed(") + to_string((int) runtime) + ");");
        ttrace.attachToTask(*t);
        kern.addTask(*t, "");
        tasks.push_back(t);
        utot = getUtot();

        SIMUL.run_to(now + getMaxPeriod() * 10);
        SIMUL.endSingleRun();
    } catch (BaseExc &e) {
        std::cout << e.what() << std::endl;
    }
}
