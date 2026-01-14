/*
  In this example, a simple system is simulated, to provide
  an evaluation of different workloads running on an big.LITTLE
  Odroid-XU3 embedded board.
*/

#include <cstring>
#include <fstream>
#include <string>

#include "cbserver.hpp"
#include "cpu.hpp"
#include "rrsched.hpp"
#include "rttask.hpp"
#include <assert.h>
#include <rtsim/cpu.hpp>
#include <rtsim/energyMRTKernel.hpp>
#include <rtsim/fileImporter.hpp>
#include <rtsim/instr.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/jtrace.hpp>
#include <rtsim/mrtkernel.hpp>
#include <rtsim/powermodel.hpp>
#include <rtsim/ps_trace.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/system_descriptor.hpp>
#include <rtsim/taskstat.hpp>
#include <rtsim/texttrace.hpp>
#include <rtsim/tracepower.hpp>
#include <unistd.h>

using namespace MetaSim;
using namespace RTSim;

#define REQUIRE assert
#define time() SIMUL.getTime()

static inline CPUMDescriptor
    quick_bp_descriptor(const std::string &wclass_name,
                        const CPUModelBPParams::SpeedModelParams &sp);
static inline void dumpSpeeds(const CPUModelBPParams::SpeedModelParams &params);
static inline void dumpAllSpeeds();
bool isInRange(int, int);
bool isInRange(Tick t1, int t2) {
    return isInRange(int(t1), t2);
}
bool isInRange(Tick t1, Tick t2) {
    return isInRange(int(t1), int(t2));
}
bool isInRangeMinMax(double eval, const double min, const double max);

void getCores(vector<CPU_BL *> &cpus_little, vector<CPU_BL *> &cpus_big,
              Island_BL **island_bl_little, Island_BL **island_bl_big);
int init_suite(EnergyMRTKernel **kern);

int main(int argc, char *argv[]) {
    // unsigned int OPP_little = 0; // Index of OPP in LITTLE cores
    // unsigned int OPP_big = 0;    // Index of OPP in big cores
    string workload = "bzip2";
    int TEST_NO = 25;

    /*if (argc == 4) {
        OPP_little = stoi(argv[1]);
        OPP_big = stoi(argv[2]);
        workload = argv[3];
    }*/
    if (argc == 2) {
        TEST_NO = stoi(argv[1]);
    }

    std::cout << "Performing experiment #" << TEST_NO << std::endl;
    std::cout << "Workload: [" << workload << "]" << std::endl;

    // std::cout << "current OPPs indices: [" << OPP_little << ", " << OPP_big
    // << "]" << std::endl;

    EnergyMRTKernel::EMRTK_LEAVE_LITTLE3_ENABLED = 0;
    EnergyMRTKernel::EMRTK_MIGRATE_ENABLED = 1;
    EnergyMRTKernel::EMRTK_CBS_YIELD_ENABLED = 0;
    EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_VTIME = 0;
    EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_END = 0;

    EnergyMRTKernel::EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED = 1;
    EnergyMRTKernel::EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END = 0;
    EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END = 1;

    try {
        SIMUL.dbg.enable("All");
        SIMUL.dbg.setStream("debug.txt");

        vector<TracePowerConsumption *> ptrace;
        vector<Scheduler *> schedulers;
        vector<RTKernel *> kernels;
        vector<CPU_BL *> cpus_little, cpus_big;
        vector<AbsRTTask *> tasks;
        PeriodicTask *t;
        vector<CBServerCallingEMRTKernel *> ets;
        string task_name;
        EnergyMRTKernel *kern;

        if (TEST_NO != 25) {
            init_suite(&kern);
            REQUIRE(kern != NULL);
            cpus_little = kern->getIslandLittle()->getProcessors();
            cpus_big = kern->getIslandBig()->getProcessors();
            kernels.push_back(kern);
        }

        TextTrace ttrace("trace" + to_string(TEST_NO) + ".txt");
        JSONTrace jtrace("trace" + to_string(TEST_NO) + ".json");
        PSTrace pstrace("trace" + to_string(TEST_NO) + ".pst");

        if (TEST_NO == 0) {
            task_name = "T0_task1";
            std::cout << "Creating task: " << task_name << std::endl;
            PeriodicTask *t0 = new PeriodicTask(500, 500, 0, task_name);
            t0->insertCode("fixed(500," + workload +
                           ");"); // WCET 500 at max frequency on big cores
            CBServerCallingEMRTKernel *et_t0 = kern->addTaskAndEnvelope(t0, "");
            ttrace.attachToTask(*t0);
            jtrace.attachToTask(*t0);
            tasks.push_back(t0);

            SIMUL.initSingleRun();
            SIMUL.run_to(1);
            CPU_BL *c0 = dynamic_cast<CPU_BL *>(
                dynamic_cast<CPU_BL *>(kern->getProcessor(et_t0)));

            REQUIRE(c0->getFrequency() == 2000);
            REQUIRE(c0->getIslandType() == IslandType::BIG);
            REQUIRE(isInRange(int(t0->getWCET(c0->getSpeed())), 497));
            REQUIRE(et_t0->getStatus() == ServerStatus::EXECUTING);
            REQUIRE(et_t0->getPeriod() == 500);
            REQUIRE(isInRange(et_t0->getBudget(), 497));
            REQUIRE(isInRange(et_t0->getEndBandwidthEvent(), 497));
            REQUIRE(isInRange(et_t0->getReplenishmentEvent(), 500));

            SIMUL.run_to(499);
            std::cout << "end of virtualtime event: t="
                      << et_t0->getEndOfVirtualTimeEvent() << std::endl;
            REQUIRE(isInRange(et_t0->getEndOfVirtualTimeEvent(), 500));
            REQUIRE(et_t0->getStatus() == ServerStatus::RELEASING);

            SIMUL.run_to(501);
            REQUIRE(c0->getFrequency() == 2000);
            REQUIRE(c0->getIslandType() == IslandType::BIG);
            REQUIRE(isInRange(int(t0->getWCET(c0->getSpeed())), 497));
            REQUIRE(et_t0->getStatus() == ServerStatus::EXECUTING);
            REQUIRE(et_t0->getPeriod() == 500);
            REQUIRE(isInRange(et_t0->getBudget(), 497));
            REQUIRE(isInRange(et_t0->getEndBandwidthEvent(), 997));
            REQUIRE(isInRange(et_t0->getReplenishmentEvent(), 500));

            SIMUL.run_to(999);
            std::cout << "end of virtualtime event: t="
                      << et_t0->getEndOfVirtualTimeEvent() << std::endl;
            REQUIRE(isInRange(et_t0->getEndOfVirtualTimeEvent(), 1000));

            SIMUL.endSingleRun();
            std::cout << "-------------" << std::endl;
            std::cout << "Simultion finished" << std::endl;
            return 0;

            // only task1 (500,500) => BIG max freq = 2000, with 500 the scaled
            // WCET
        }
        if (TEST_NO == 1) {
            task_name = "T1_task1";
            std::cout << "Creating task: " << task_name << std::endl;
            PeriodicTask *t0 = new PeriodicTask(500, 500, 0, task_name);
            t0->insertCode("fixed(500," + workload +
                           ");"); // WCET 500 at max frequency on big cores
            CBServerCallingEMRTKernel *et_t0 = kern->addTaskAndEnvelope(t0, "");

            task_name = "T1_task2";
            std::cout << "Creating task: " << task_name << std::endl;
            PeriodicTask *t1 = new PeriodicTask(500, 500, 0, task_name);
            t1->insertCode("fixed(500," + workload + ");");
            CBServerCallingEMRTKernel *et_t1 = kern->addTaskAndEnvelope(t1, "");

            SIMUL.initSingleRun();
            SIMUL.run_to(1);

            CPU_BL *c0 = dynamic_cast<CPU_BL *>(kern->getProcessor(et_t0));
            CPU_BL *c1 = dynamic_cast<CPU_BL *>(kern->getProcessor(et_t1));

            REQUIRE(c0->getFrequency() == 2000);
            REQUIRE(c0->getIslandType() == IslandType::BIG);
            REQUIRE(isInRange(int(t0->getWCET(c0->getSpeed())), 497));

            REQUIRE(c1->getFrequency() == 2000);
            REQUIRE(c1->getIslandType() == IslandType::BIG);
            REQUIRE(isInRange(int(t1->getWCET(c1->getSpeed())), 497));

            SIMUL.run_to(1000);
            SIMUL.endSingleRun();

            std::cout << "-------------" << std::endl;
            std::cout << "Simultion finished" << std::endl;
            return 0;
            // task1 (500,500) => BIG_3 max freq, task2 (500,500) => BIG_2 max
            // freq
        }
        if (TEST_NO == 2) {
            task_name = "T2_task1";
            std::cout << "Creating task: " << task_name << std::endl;
            PeriodicTask *t0 = new PeriodicTask(500, 500, 0, task_name);
            t0->insertCode("fixed(500," + workload +
                           ");"); // WCET 500 at max frequency on big cores
            CBServerCallingEMRTKernel *et_t0 = kern->addTaskAndEnvelope(t0, "");

            task_name = "T2_task2";
            std::cout << "Creating task: " << task_name << std::endl;
            PeriodicTask *t1 = new PeriodicTask(500, 500, 0, task_name);
            t1->insertCode("fixed(250," + workload + ");");
            CBServerCallingEMRTKernel *et_t1 = kern->addTaskAndEnvelope(t1, "");

            SIMUL.initSingleRun();
            SIMUL.run_to(1);

            CPU_BL *c0 = dynamic_cast<CPU_BL *>(kern->getProcessor(et_t0));
            CPU_BL *c1 = dynamic_cast<CPU_BL *>(kern->getProcessor(et_t1));

            for (string s : kern->getRunningTasks())
                std::cout << "running :" << s << std::endl;

            REQUIRE(t0->getName() == "T2_task1");
            REQUIRE(c0->getFrequency() == 2000);
            REQUIRE(c0->getIslandType() == IslandType::BIG);
            REQUIRE(isInRange(int(t0->getWCET(c0->getSpeed())), 497));

            REQUIRE(t1->getName() == "T2_task2");
            REQUIRE(c1->getFrequency() == 2000);
            REQUIRE(c1->getIslandType() == IslandType::BIG);
            REQUIRE(isInRange(int(t1->getWCET(c1->getSpeed())), 248));

            SIMUL.run_to(1000);
            SIMUL.endSingleRun();

            // task1 (500,500) => BIG_3 max freq, task2 (250,500) => same
        }
        if (TEST_NO == 3) {
            task_name = "T3_task1";
            std::cout << "Creating task: " << task_name << std::endl;
            PeriodicTask *t0 = new PeriodicTask(500, 500, 0, task_name);
            t0->insertCode("fixed(10," + workload +
                           ");"); // WCET 10 at max frequency on big cores
            CBServerCallingEMRTKernel *et_t0 = kern->addTaskAndEnvelope(t0, "");
            ttrace.attachToTask(*t0);
            // jtrace.attachToTask(*t);
            tasks.push_back(t0);

            SIMUL.initSingleRun();
            SIMUL.run_to(10);

            std::cout << "t=" << SIMUL.getTime() << std::endl;
            CPU_BL *c0 = dynamic_cast<CPU_BL *>(kern->getProcessor(et_t0));

            // REQUIRE (t0->getName() == "T3_task1");
            REQUIRE(c0->getFrequency() == 500);
            REQUIRE(c0->getIslandType() == IslandType::LITTLE);
            std::cout << "qua " << int(t0->getWCET(c0->getSpeed()))
                      << std::endl;
            std::cout << "qua " << int(et_t0->getWCET(c0->getSpeed()))
                      << std::endl;
            std::cout << "qua " << et_t0->toString() << std::endl;
            REQUIRE(isInRange(int(t0->getWCET(c0->getSpeed())), 65));

            SIMUL.endSingleRun();

            // little freq 500
        }
        if (TEST_NO == 4) {
            for (int j = 0; j < 4; j++) {
                task_name = "T4_task_LITTLE_" + std::to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t = new PeriodicTask(500, 500, 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(100, %s);", workload.c_str());
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
            }
        } else if (TEST_NO == 5) {
            for (int j = 0; j < 4; j++) {
                int wcet = 5; //* (j+1);
                task_name = "T5_task" + std::to_string(j);
                std::cout << "Creating task: " << task_name;
                t = new PeriodicTask(500, 500, 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, bzip2);", wcet);
                std::cout << " with abs. WCET " << wcet << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
            }

            // small tasks. They should be scheduled on a single little at
            // frequency with min voltage = 500
        } else if (TEST_NO == 6) {
            /* This experiment shows that other CPUs frequencies are increased
             for the
             entire island after a decision for other tasks has already been
             made */
            vector<CPU *> cpu_task;
            int i, wcet = 300;
            for (int j = 0; j < 5; j++) {
                if (j == 4)
                    wcet = 200;
                task_name = "T6_task" + std::to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t = new PeriodicTask(500, 500, 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcet, workload.c_str());
                std::cout << " with abs. WCET " << wcet << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);

                tasks.push_back(t);
            }

            SIMUL.initSingleRun();
            SIMUL.run_to(1);
            SIMUL.endSingleRun();
            return 0;
        } else if (TEST_NO == 7) {
            vector<CPU_BL *> cpus;
            PeriodicTask *task[5]; // to be cleared after each test
            CPU_BL *cpu_task[5]; // to be cleared after each test
            vector<CBServerCallingEMRTKernel *> ets;

            int wcets[] = {63, 63, 63, 63, 30};
            int i;
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T7_task" + std::to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t = new PeriodicTask(500, 500, 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));

                task[j] = t;
            }

            /* Towards random workloads, but this time alg. first decides to
               schedule all tasks on littles, and then, instead of schedule the
               next one in bigs, it shall increase littles frequency so to make
               space to it too and save energy */

            SIMUL.initSingleRun();
            SIMUL.run_to(1);

            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                cpu_task[j] =
                    dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(j)));
            }

            i = 0;
            PeriodicTask *t = task[i];
            CPU_BL *c = cpu_task[i];
            REQUIRE(t->getName() == "T7_task0");
            REQUIRE(c->getFrequency() == 500);
            REQUIRE(c->getIslandType() == IslandType::LITTLE);
            REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 415));
            printf("aaa %s scheduled on %s freq %u with wcet %f\n",
                   t->getName().c_str(), c->toString().c_str(),
                   c->getFrequency(), t->getWCET(c->getSpeed()));

            i = 1;
            t = task[i];
            c = cpu_task[i];
            REQUIRE(t->getName() == "T7_task1");
            REQUIRE(c->getFrequency() == 500);
            REQUIRE(c->getIslandType() == IslandType::LITTLE);
            REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 415));
            printf("aaa %s scheduled on %s freq %u with wcet %f\n",
                   t->getName().c_str(), c->toString().c_str(),
                   c->getFrequency(), t->getWCET(c->getSpeed()));

            i = 2;
            t = task[i];
            c = cpu_task[i];
            REQUIRE(t->getName() == "T7_task2");
            REQUIRE(c->getFrequency() == 500);
            REQUIRE(c->getIslandType() == IslandType::LITTLE);
            REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 415));
            printf("aaa %s scheduled on %s freq %u with wcet %f\n",
                   t->getName().c_str(), c->toString().c_str(),
                   c->getFrequency(), t->getWCET(c->getSpeed()));

            i = 3;
            t = task[i];
            c = cpu_task[i];
            REQUIRE(t->getName() == "T7_task3");
            REQUIRE(c->getFrequency() == 500);
            REQUIRE(c->getIslandType() == IslandType::LITTLE);
            REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 415));
            printf("aaa %s scheduled on %s freq %u with wcet %f\n",
                   t->getName().c_str(), c->toString().c_str(),
                   c->getFrequency(), t->getWCET(c->getSpeed()));

            i = 4;
            t = task[i];
            c = cpu_task[i];
            REQUIRE(t->getName() == "T7_task4");
            REQUIRE(c->getFrequency() == 700);
            REQUIRE(c->getIslandType() == IslandType::BIG);
            REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 75));
            printf("aaa %s scheduled on %s freq %u with wcet %f\n",
                   t->getName().c_str(), c->toString().c_str(),
                   c->getFrequency(), t->getWCET(c->getSpeed()));

            // SIMUL.run_to(1000);
            SIMUL.endSingleRun();
            return 0;
        }
        if (TEST_NO == 8) {
            int wcets[] = {181, 419, 261, 163, 65, 8, 61, 170, 273};
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T8_task" + std::to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t = new PeriodicTask(500, 500, 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
            }
            // towards random workloads...
        }

        if (TEST_NO == 9) {
            // 100 and 101 will end up in LITTLEs, 500 in BIGs, 101 will end up
            // in big. 100 will finish before, making the task in big (101, the
            // last one in the list) migrate to little

            // needs migrations?
            int wcets[] = {101, 101, 101, 8,   200,
                           500, 500, 500, 101, 1}; // 9 tasks
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T" + to_string(TEST_NO) + "_task" + to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t = new PeriodicTask(500, 500, 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kernels[0]);
            MultiCoresScheds *queues = k->getEnergyMultiCoresScheds();

            k->addForcedDispatch(ets[0], cpus_little[0], 6);
            k->addForcedDispatch(ets[1], cpus_little[1], 6);
            k->addForcedDispatch(ets[2], cpus_little[2], 6);
            k->addForcedDispatch(ets[3], cpus_little[3], 6);

            k->addForcedDispatch(ets[4], cpus_big[0], 18);
            k->addForcedDispatch(ets[5], cpus_big[1], 18);
            k->addForcedDispatch(ets[6], cpus_big[2], 18);
            k->addForcedDispatch(ets[7], cpus_big[3], 18);

            k->addForcedDispatch(ets[8], cpus_big[3], 18);
            k->addForcedDispatch(ets[9], cpus_big[3], 18);

            SIMUL.initSingleRun();
            SIMUL.run_to(1);
            k->printState(true);

            SIMUL.run_to(34);
            k->printState(true);

            // at t=35 task WCET 8 finishes

            SIMUL.run_to(36);
            k->printState(true);
            for (CBServerCallingEMRTKernel *s : ets)
                assert(s->getStatus() == ServerStatus::EXECUTING ||
                       s->getStatus() == ServerStatus::RECHARGING ||
                       s->getStatus() == ServerStatus::READY);
            for (CPU *c : k->getProcessors())
                REQUIRE(queues->getUtilization_active(c) == 0.0);

            REQUIRE(k->getProcessor(ets[0]) == cpus_little[0]);
            REQUIRE(k->getProcessor(ets[1]) == cpus_little[1]);
            REQUIRE(k->getProcessor(ets[2]) == cpus_little[2]);
            // REQUIRE(k->getProcessor(ets[3]) == cpus_little[3]); has ended
            // already

            REQUIRE(k->getProcessor(ets[4]) == cpus_big[0]);
            REQUIRE(k->getProcessor(ets[5]) == cpus_big[1]);
            REQUIRE(k->getProcessor(ets[6]) == cpus_big[2]);
            REQUIRE(k->getProcessor(ets[7]) == cpus_big[3]);

            // task8 comes in place of task3
            REQUIRE(k->getProcessor(ets[8]) == cpus_little[3]);
            std::cout << ets[8]->getBudget() << std::endl;
            REQUIRE(ets[8]->getBudget() > 400);

            SIMUL.run_to(199);
            k->printState(true, true);
            for (CBServerCallingEMRTKernel *s : ets)
                if (s == ets[4])
                    REQUIRE(s->getStatus() == ServerStatus::RELEASING);
                else
                    assert(s->getStatus() == ServerStatus::EXECUTING ||
                           s->getStatus() == ServerStatus::RECHARGING);
            for (CPU *c : k->getProcessors())
                if (c == cpus_big[0] &&
                    ets[4]->getStatus() == ServerStatus::RELEASING)
                    REQUIRE(isInRange(queues->getUtilization_active(c),
                                      200.0 / c->getSpeed() / 500.0));
                else
                    REQUIRE(queues->getUtilization_active(c) == 0.0);

            REQUIRE(k->getProcessor(ets[0]) == cpus_little[0]);
            REQUIRE(k->getProcessor(ets[1]) == cpus_little[1]);
            REQUIRE(k->getProcessor(ets[2]) == cpus_little[2]);
            // REQUIRE(k->getProcessor(ets[3]) == cpus_little[3]); has ended
            // already
            std::cout << "eccomi1 " << ets[3]->toString() << ", "
                      << ets[3]->getEndOfVirtualTimeEvent() << std::endl;

            // REQUIRE(k->getProcessor(ets[4]) == cpus_big[0]); has ended
            // already
            REQUIRE(k->getProcessor(ets[5]) == cpus_big[1]);
            REQUIRE(k->getProcessor(ets[6]) == cpus_big[2]);
            REQUIRE(k->getProcessor(ets[7]) == cpus_big[3]);

            // task9 comes in place of task4
            REQUIRE(k->getProcessor(ets[9]) == cpus_big[0]);
            SIMUL.run_to(202);
            for (CBServerCallingEMRTKernel *s : ets)
                if (s == ets[4])
                    REQUIRE(s->getStatus() == ServerStatus::RELEASING);
                else
                    assert(s->getStatus() == ServerStatus::EXECUTING ||
                           s->getStatus() == ServerStatus::RECHARGING);
            for (CPU *c : k->getProcessors())
                if (c == cpus_big[0]) {
                    c->setWorkload(workload);
                    REQUIRE(isInRange(queues->getUtilization_active(c),
                                      200.0 / c->getSpeed() / 500.0));
                    REQUIRE(ets[4]->getIdleEvent() == 500);
                    c->setWorkload("idle");
                } else
                    REQUIRE(queues->getUtilization_active(c) == 0.0);

            SIMUL.run_to(499);
            std::cout << std::endl
                      << "t=499. all cores should be empty" << std::endl
                      << std::endl;
            k->printState(true);
            for (CBServerCallingEMRTKernel *s : ets)
                if (s == ets[4] || s == ets[5] || s == ets[6] || s == ets[7])
                    REQUIRE(s->getStatus() == ServerStatus::RELEASING);
                else
                    assert(s->getStatus() == ServerStatus::EXECUTING ||
                           s->getStatus() == ServerStatus::RECHARGING ||
                           s->getStatus() == ServerStatus::IDLE);
            for (CPU *c : k->getProcessors()) {
                c->setWorkload(workload);
                if (c == cpus_big[0])
                    REQUIRE(isInRange(queues->getUtilization_active(c),
                                      200.0 / c->getSpeedByOPP(18) / 500.0));
                else if (dynamic_cast<CPU_BL *>(c)->getIslandType() ==
                         IslandType::BIG)
                    REQUIRE(isInRange(queues->getUtilization_active(c),
                                      500.0 / c->getSpeedByOPP(18) / 500.0));
                else
                    REQUIRE(queues->getUtilization_active(c) == 0.0);
                c->setWorkload("idle");
            }

            SIMUL.run_to(500);
            for (CPU *c : k->getProcessors())
                REQUIRE(queues->getUtilization_active(c) == 0.0);
            k->printState(true);

            REQUIRE(k->getProcessor(tasks[0])->getIslandType() ==
                    cpus_little[0]->getIslandType());
            REQUIRE(k->getProcessor(tasks[1])->getIslandType() ==
                    cpus_little[1]->getIslandType());
            REQUIRE(k->getProcessor(tasks[2])->getIslandType() ==
                    cpus_little[2]->getIslandType());
            REQUIRE(k->getProcessor(tasks[3])->getIslandType() ==
                    cpus_little[3]->getIslandType()); // has ended already

            REQUIRE(k->getProcessor(tasks[4])->getIslandType() ==
                    cpus_big[0]->getIslandType()); // has ended already
            REQUIRE(k->getProcessor(tasks[5])->getIslandType() ==
                    cpus_big[1]->getIslandType());
            REQUIRE(k->getProcessor(tasks[6])->getIslandType() ==
                    cpus_big[2]->getIslandType());
            REQUIRE(k->getProcessor(tasks[7])->getIslandType() ==
                    cpus_big[3]->getIslandType());

            SIMUL.run_to(536);

            REQUIRE(k->getProcessor(tasks[8]) == cpus_little[3]);

            SIMUL.run_to(941);

            REQUIRE(k->getProcessor(tasks[9]) == cpus_little[0]);

            // SIMUL.run_to(1000);
            SIMUL.endSingleRun();

            std::cout << "-----------------------" << std::endl;
            std::cout << "Simulation has finished" << std::endl;
            return 0;
        }
        if (TEST_NO == 10) {
            /**
                At time 0, you have a task on a CPU, which is under a context
               switch. Say it lasts 30 ticks. Then, another task, more
               important, arrives at time 6. Would this last task begin its
                context switch at time 30, thus being scheduled at time
               30+30=60? Experiment requires EDF scheduler.
              */
            int wcets[] = {30, 30};
            int deadl[] = {500, 400};
            int activ[] = {0, 6};
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T" + to_string(TEST_NO) + "_task" + to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deadl[j], deadl[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            MultiCoresScheds *queues = k->getEnergyMultiCoresScheds();

            k->setContextSwitchDelay(Tick(8));
            k->addForcedDispatch(ets[0], cpus_big[0], 18);
            k->addForcedDispatch(ets[1], cpus_big[0], 18);

            SIMUL.initSingleRun();
            dynamic_cast<Task *>(tasks[1])->activate(Tick(activ[1]));
            SIMUL.run_to(17);

            REQUIRE(k->getProcessor(ets[1]) == cpus_big[0]);
            REQUIRE(k->getProcessorReady(tasks[0]) == cpus_big[0]);

            SIMUL.run_to(400);
            std::cout << queues->getUtilization_active(cpus_big[0])
                      << std::endl;

            SIMUL.run_to(500);
            REQUIRE(queues->getUtilization_active(cpus_big[0]) == 0.0);

            SIMUL.run_to(1000);
            SIMUL.endSingleRun();
            std::cout << "-----------" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            return 0;
        }
        //    if (TEST_NO == 12) {
        // // does RR work as expected with EMRTK?

        //        schedulers.clear();
        //        kernels.clear();

        //        RRScheduler *rrsched = new RRScheduler(100); // 100 is result
        //        of sysctl kernel.sched_rr_timeslice_ms on my machine, L5.0.2
        //        rrsched->disable();
        //        rrsched->setName("RRScheduler for arrival queue");
        //        for (int i = 0; i < 8; i++) {
        //            delete schedulers[i];
        //            Scheduler *s = new RRScheduler(100);
        //            s->setName("RRScheduler #" + to_string(i));
        //            schedulers.push_back(s);
        //        }
        //        EnergyMRTKernel *kern = new EnergyMRTKernel(schedulers,
        //        rrsched, island_bl_big, island_bl_little, "Round Robin");
        //        kernels.push_back(kern);

        //        int wcets[] = { 30, 30, 30  };
        //        int deadl[] = { 500, 500, 500 };
        //        for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        //            task_name = "T" + to_string(TEST_NO) + "_task" +
        //            to_string(j); std::cout << "Creating task: " << task_name;
        //            PeriodicTask* t = new PeriodicTask(deadl[j], deadl[j], 0,
        //            task_name); char instr[60] = ""; sprintf(instr, "fixed(%d,
        //            %s);", wcets[j], workload.c_str()); t->insertCode(instr);
        //            kern->addTask(*t, "");
        //            ttrace.attachToTask(*t);
        //            tasks.push_back(t);
        //        }
        //        kern->addForcedDispatch(ets[0], cpus_little[0], 6);
        //        kern->addForcedDispatch(ets[1], cpus_little[0], 6);

        //        SIMUL.run(1001);
        //        return 0;
        //    }
        if (TEST_NO == 13) {
            /**
                Demostrating what happens when a task is killed.
                It will come into play again in its next period and hopefully it
               can be scheduled.
              */
            int wcets[] = {450, 450, 450, 450, 1};
            int deads[] = {500, 500, 500, 500, 500};
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T13_task_BIG_" + to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deads[j], deads[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                std::cout << instr << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                tasks.push_back(t);

                t->killOnMiss(true);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            MultiCoresScheds *queues = k->getEnergyMultiCoresScheds();

            k->addForcedDispatch(ets[0], cpus_big[0], 18);
            k->addForcedDispatch(ets[1], cpus_big[1], 18);
            k->addForcedDispatch(ets[2], cpus_big[2], 18);
            k->addForcedDispatch(ets[3], cpus_big[3], 18);

            cpus_little[0]->toggleDisabled();
            cpus_little[1]->toggleDisabled();
            cpus_little[2]->toggleDisabled();
            cpus_little[3]->toggleDisabled();

            SIMUL.initSingleRun();
            SIMUL.run_to(440);
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]) - 1; j++) {
                std::cout << "killing tasks" << std::endl;
                Task *t = dynamic_cast<Task *>(tasks[j]);
                t->killInstance();
                REQUIRE(t->getState() == TSK_IDLE);
            }
            for (CPU *c : k->getProcessors())
                REQUIRE(queues->getUtilization_active(c) == 0.0);
            SIMUL.run_to(501);
            for (CPU *c : k->getProcessors())
                REQUIRE(queues->getUtilization_active(c) == 0.0);
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[0]))
                        ->getIslandType() == IslandType::BIG);
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[1]))
                        ->getIslandType() == IslandType::BIG);
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[2]))
                        ->getIslandType() == IslandType::BIG);
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[3]))
                        ->getIslandType() == IslandType::BIG);
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessorReady(tasks[4]))
                        ->getIslandType() == IslandType::BIG);

            SIMUL.run_to(999);
            k->printState(true);
            for (CPU *c : k->getProcessors())
                REQUIRE(k->getRunningTask(c) == NULL);

            SIMUL.run_to(1000);
            SIMUL.endSingleRun();
            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            return 0;
        }
        if (TEST_NO == 14) {
            /**
                What happens if task is not schedulable?

                @Mr. Cucinotta, Mr. Marinoni:
                This example also demonstrates that a task is either on the
               queue/scheduler of arrived tasks or in the one of the core
               selected for its dispatching. Moreover, if a task cannot be
               scheduled in its current, it has another chance on the next one.
              */
            cpus_little[0]->toggleDisabled();
            cpus_little[1]->toggleDisabled();
            cpus_little[2]->toggleDisabled();
            cpus_little[3]->toggleDisabled();
            cpus_big[1]->toggleDisabled();
            cpus_big[2]->toggleDisabled();
            cpus_big[3]->toggleDisabled();

            int wcets[] = {200, 300, 450, 450};
            int deads[] = {500, 500, 500, 500};
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T14_task_BIG_" + to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deads[j], deads[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                std::cout << instr << std::endl;
                t->insertCode(instr);
                CBServerCallingEMRTKernel *et = kern->addTaskAndEnvelope(t, "");
                ttrace.attachToTask(*t);
                tasks.push_back(t);

                t->killOnMiss(false);
            }

            SIMUL.initSingleRun();

            SIMUL.run_to(1);
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[0])) ==
                    cpus_big[0]);
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessorReady(tasks[1])) ==
                    cpus_big[0]);
            REQUIRE(k->getScheduler()->isInQueue(tasks[0]) ==
                    false); // an executing task
            REQUIRE(k->getScheduler()->isInQueue(tasks[1]) ==
                    false); // a ready task
            REQUIRE(k->getScheduler()->isInQueue(tasks[2]) ==
                    false); // a discarded task
            REQUIRE(k->getScheduler()->isInQueue(tasks[3]) ==
                    false); // a discarded task

            REQUIRE(k->getEnergyMultiCoresScheds()
                        ->getScheduler(k->getProcessor(tasks[0]))
                        ->isInQueue(tasks[0]) == true);
            REQUIRE(k->getEnergyMultiCoresScheds()
                        ->getScheduler(k->getProcessor(tasks[0]))
                        ->isInQueue(tasks[1]) == true);
            REQUIRE(k->getEnergyMultiCoresScheds()->isInAnyQueue(tasks[2]) ==
                    NULL);
            REQUIRE(k->getEnergyMultiCoresScheds()->isInAnyQueue(tasks[3]) ==
                    NULL);

            SIMUL.run_to(501);
            // same tests as above
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[0])) ==
                    cpus_big[0]);
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessorReady(tasks[1])) ==
                    cpus_big[0]);
            REQUIRE(k->getScheduler()->isInQueue(tasks[0]) ==
                    false); // an executing task
            REQUIRE(k->getScheduler()->isInQueue(tasks[1]) ==
                    false); // a ready task
            REQUIRE(k->getScheduler()->isInQueue(tasks[2]) ==
                    false); // a discarded task
            REQUIRE(k->getScheduler()->isInQueue(tasks[3]) ==
                    false); // a discarded task

            REQUIRE(k->getEnergyMultiCoresScheds()
                        ->getScheduler(k->getProcessor(tasks[0]))
                        ->isInQueue(tasks[0]) == true);
            REQUIRE(k->getEnergyMultiCoresScheds()
                        ->getScheduler(k->getProcessor(tasks[0]))
                        ->isInQueue(tasks[1]) == true);
            REQUIRE(k->getEnergyMultiCoresScheds()->isInAnyQueue(tasks[2]) ==
                    NULL);
            REQUIRE(k->getEnergyMultiCoresScheds()->isInAnyQueue(tasks[3]) ==
                    NULL);

            SIMUL.run_to(1000);
            SIMUL.endSingleRun();
            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            return 0;
        }
        if (TEST_NO == 15) {
            /**
                Towards servers...y màs allà!
             */
            PeriodicTask *t2 = new PeriodicTask(15, 15, 0, "TaskA");
            t2->insertCode("fixed(4,bzip2);");
            t2->setAbort(false);
            ttrace.attachToTask(*t2);

            CBServerCallingEMRTKernel *serv = new CBServerCallingEMRTKernel(
                4, 15, 15, "hard", "server1", "fifo");
            serv->setKernel(kern);
            serv->addTask(*t2);
            tasks.push_back(serv);
            CBServerCallingEMRTKernel *et = kern->addTaskAndEnvelope(serv, "");

            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(serv, cpus_big[0], 18, 999);

            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Running simulation!" << std::endl;
            SIMUL.initSingleRun();

            SIMUL.run_to(1);
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[0]))
                        ->getIslandType() == IslandType::BIG);

            SIMUL.run_to(5);
            std::cout << " dasdsad "
                      << dynamic_cast<Task *>(t2)->getStateString()
                      << std::endl;
            REQUIRE(dynamic_cast<Task *>(t2)->getState() == TSK_IDLE);

            SIMUL.run_to(16);
            std::cout << " dasdsad "
                      << dynamic_cast<Task *>(t2)->getStateString()
                      << std::endl;
            REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[0]))
                        ->getIslandType() == IslandType::BIG);

            SIMUL.run_to(21);
            std::cout << " dasdsad "
                      << dynamic_cast<Task *>(t2)->getStateString()
                      << std::endl;
            REQUIRE(dynamic_cast<Task *>(t2)->getState() == TSK_IDLE);

            SIMUL.run_to(26);

            SIMUL.endSingleRun();
            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Simulation finished" << std::endl;

            return 0;
        }
        if (TEST_NO == 16) {
            /**
                Towards servers. Reproducing Mr.Cucinotta's example.
                A server with (Q=2,T=10) and a task arriving at 0 and ending at
               2, period 10. Its active utilization is kept until 10.
             */
            PeriodicTask *t2 = new PeriodicTask(10, 10, 0, "TaskA");
            t2->insertCode("fixed(2,bzip2);");
            t2->setAbort(false);
            ttrace.attachToTask(*t2);

            CBServerCallingEMRTKernel *serv = new CBServerCallingEMRTKernel(
                2, 10, 10, "hard", "server1", "fifo");
            serv->addTask(*t2);
            tasks.push_back(serv);
            CBServerCallingEMRTKernel *et = kern->addTaskAndEnvelope(serv, "");

            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(ets[0], cpus_big[0], 18, 999);

            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Running simulation!" << std::endl;
            SIMUL.initSingleRun();

            SIMUL.run_to(3); // t=2: executing to releasing
            std::cout << "time = " << SIMUL.getTime() << std::endl;
            REQUIRE(k->getUtilization_active(
                        dynamic_cast<CPU_BL *>(cpus_little.at(0))) == 0.0);
            REQUIRE(k->getUtilization_active(
                        dynamic_cast<CPU_BL *>(cpus_big.at(0))) == 0.2);
            k->print();
            REQUIRE(k->getIslandUtilization(1.0, cpus_big[0]->getIslandType(),
                                            NULL) == 0.2);
            REQUIRE(k->getUtilization(cpus_big.at(0), 1.0) ==
                    0.2); // float repr. precision

            SIMUL.run_to(5); // t=4: releasing to idle
            std::cout << "time = " << SIMUL.getTime() << std::endl;
            for (CPU_BL *c : cpus_big) {
                if (c == cpus_big.at(0))
                    continue;
                REQUIRE(k->getUtilization_active(dynamic_cast<CPU_BL *>(c)) ==
                        0.0);
                REQUIRE(k->getUtilization(c, 1.0) == 0.0);
            }

            SIMUL.endSingleRun();
            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Simulation finished" << std::endl;

            return 0;
        }
        if (TEST_NO == 17) {
            /**
                The objective is to understand if utilizations are
                computed correctly.

                Some tasks are placed on all available cores.
                Let's see where the CBS is placed according to utilizations.
              */
            cpus_little[1]->toggleDisabled();
            cpus_little[2]->toggleDisabled();
            cpus_little[3]->toggleDisabled();
            cpus_big[2]->toggleDisabled();
            cpus_big[3]->toggleDisabled();

            cpus_big[0]->setOPP(18);
            cpus_big[1]->setOPP(18);

            // Already-there tasks
            PeriodicTask *t0_little =
                new PeriodicTask(20, 20, 0, "Task1_little0");
            t0_little->insertCode("fixed(5,bzip2);");
            t0_little->setAbort(false);
            ttrace.attachToTask(*t0_little);
            pstrace.attachToTask(*t0_little);
            tasks.push_back(t0_little);
            CBServerCallingEMRTKernel *et_t0_little =
                kern->addTaskAndEnvelope(t0_little, "");

            PeriodicTask *t0_big0 = new PeriodicTask(10, 10, 0, "Task2_Big0");
            t0_big0->insertCode("fixed(5,bzip2);");
            t0_big0->setAbort(false);
            ttrace.attachToTask(*t0_big0);
            pstrace.attachToTask(*t0_big0);
            tasks.push_back(t0_big0);
            CBServerCallingEMRTKernel *et_t0_big0 =
                kern->addTaskAndEnvelope(t0_big0, "");

            PeriodicTask *t0_big1 = new PeriodicTask(10, 10, 0, "Task3_Big1");
            t0_big1->insertCode("fixed(5,bzip2);");
            t0_big1->setAbort(false);
            ttrace.attachToTask(*t0_big1);
            pstrace.attachToTask(*t0_big1);
            tasks.push_back(t0_big1);
            CBServerCallingEMRTKernel *et_t0_big1 =
                kern->addTaskAndEnvelope(t0_big1, "");

            // CBS server tasks
            PeriodicTask *t2 = new PeriodicTask(10, 10, 0, "TaskOnServer");
            t2->insertCode(
                "fixed(2,bzip2);"); // => its releasing_idle will be at t=4
            t2->setAbort(false);
            ttrace.attachToTask(*t2);
            pstrace.attachToTask(*t2);

            CBServerCallingEMRTKernel *serv = new CBServerCallingEMRTKernel(
                2, 10, 10, "hard", "server1", "fifo");
            serv->addTask(*t2);
            tasks.push_back(serv);
            CBServerCallingEMRTKernel *et_serv =
                kern->addTaskAndEnvelope(serv, "");

            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(
                ets[0], cpus_little[0], 11,
                1); // note: normally they wouldn't fit this way
            k->addForcedDispatch(ets[1], cpus_big[0], 18, 1);
            k->addForcedDispatch(ets[2], cpus_big[1], 18, 1);
            // server's free to go wherever.

            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Running simulation!" << std::endl;
            SIMUL.initSingleRun();

            SIMUL.run_to(50);

            SIMUL.endSingleRun();
            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Simulation finished" << std::endl;

            return 0;
        }
        if (TEST_NO == 18) {
            /**
                The objective here is to find the time at which the task in the
               server goes from releasing to idle (time_t). Also, I want to see
               how cores utilization is computed (=> leave 2 bigs and 1 little
               enabled).

                Given that time, 3 experiments are performed. In the first one,
               another task comes before the server task's over. In the second
               one, it comes before time_t. In the third one, it comes
               afterwards. You should observe that, in the first case, the core
               utilization considers server task; in the second one, the
               releasing task; in the other, the idle task is not considered.

                In all cases, the server task remains in the core queue until
               time_t. Later, it goes back to the general scheduler.

                Server and tasks run on the same core, a big max freq for
               convenience.
              */
            cpus_little[0]->toggleDisabled();
            cpus_little[1]->toggleDisabled();
            cpus_little[2]->toggleDisabled();
            cpus_little[3]->toggleDisabled();
            cpus_big[2]->toggleDisabled();
            cpus_big[3]->toggleDisabled();

            cpus_big[0]->setOPP(18);
            cpus_big[1]->setOPP(18);

            vector<int> activ = {6, 8, 12};

            // Already-there tasks
            PeriodicTask *t0_little =
                new PeriodicTask(20, 20, 0, "Task1_little0");
            t0_little->insertCode("fixed(8,bzip2);");
            t0_little->setAbort(false);
            ttrace.attachToTask(*t0_little);
            jtrace.attachToTask(*t0_little);
            pstrace.attachToTask(*t0_little);
            tasks.push_back(t0_little);
            CBServerCallingEMRTKernel *et =
                kern->addTaskAndEnvelope(t0_little, "");

            NonPeriodicTask *t0_big0 =
                new NonPeriodicTask(10, 10, 0, "Task2_Big0");
            t0_big0->insertCode("fixed(5,bzip2);");
            t0_big0->setAbort(false);
            ttrace.attachToTask(*t0_big0);
            jtrace.attachToTask(*t0_big0);
            pstrace.attachToTask(*t0_big0);
            tasks.push_back(t0_big0);
            CBServerCallingEMRTKernel *et_t0_big0 =
                kern->addTaskAndEnvelope(t0_big0, "");

            PeriodicTask *t0_big1 = new PeriodicTask(30, 30, 0, "Task3_Big1");
            t0_big1->insertCode("fixed(15,bzip2);");
            t0_big1->setAbort(false);
            ttrace.attachToTask(*t0_big1);
            jtrace.attachToTask(*t0_big1);
            pstrace.attachToTask(*t0_big1);
            tasks.push_back(t0_big1);
            CBServerCallingEMRTKernel *et_t0_big1 =
                kern->addTaskAndEnvelope(t0_big1, "");

            PeriodicTask *t1_big1 =
                new PeriodicTask(31, 31, 0, "TaskReady_Big1");
            t1_big1->insertCode("fixed(1,bzip2);");
            t1_big1->setAbort(false);
            ttrace.attachToTask(*t1_big1);
            jtrace.attachToTask(*t1_big1);
            pstrace.attachToTask(*t0_big1);
            tasks.push_back(t1_big1);
            CBServerCallingEMRTKernel *et_t1_big1 =
                kern->addTaskAndEnvelope(t1_big1, "");

            // CBS server tasks
            NonPeriodicTask *tos =
                new NonPeriodicTask(10, 10, 0, "TaskOnServer");
            tos->insertCode(
                "fixed(2,bzip2);"); // => its releasing_idle will be at t=4
            tos->setAbort(false);
            ttrace.attachToTask(*tos);
            jtrace.attachToTask(*tos);
            pstrace.attachToTask(*tos);

            NonPeriodicTask *tos2 =
                new NonPeriodicTask(10, 10, 0, "AfterTaskOnServer");
            tos2->insertCode("fixed(2,bzip2);");
            tos2->setAbort(false);
            ttrace.attachToTask(*tos2);
            jtrace.attachToTask(*tos2);
            pstrace.attachToTask(*tos2);

            CBServerCallingEMRTKernel *serv = new CBServerCallingEMRTKernel(
                2, 10, 10, "hard", "server1", "fifo");
            serv->addTask(*tos);
            serv->addTask(*tos2);
            tasks.push_back(serv);
            CBServerCallingEMRTKernel *et_serv =
                kern->addTaskAndEnvelope(serv, "");

            // Tasks coming freely: the dynamic situations
            PeriodicTask *t5 = new PeriodicTask(30, 30, 0, "TaskDuring");
            t5->insertCode("fixed(2,bzip2);");
            t5->setAbort(false);
            ttrace.attachToTask(*t5);
            tasks.push_back(t5);
            jtrace.attachToTask(*t5);
            pstrace.attachToTask(*t5);
            CBServerCallingEMRTKernel *et_t5 = kern->addTaskAndEnvelope(t5, "");

            PeriodicTask *t3 = new PeriodicTask(30, 30, 0, "TaskBefore");
            t3->insertCode("fixed(1,bzip2);");
            t3->setAbort(false);
            ttrace.attachToTask(*t3);
            jtrace.attachToTask(*t3);
            pstrace.attachToTask(*t3);
            tasks.push_back(t3);
            CBServerCallingEMRTKernel *et_t3 = kern->addTaskAndEnvelope(t3, "");

            PeriodicTask *t4 = new PeriodicTask(30, 30, 0, "TaskAfter");
            t4->insertCode("fixed(1,bzip2);");
            t4->setAbort(false);
            ttrace.attachToTask(*t4);
            jtrace.attachToTask(*t4);
            pstrace.attachToTask(*t4);
            tasks.push_back(t4);
            CBServerCallingEMRTKernel *et_t4 = kern->addTaskAndEnvelope(t4, "");

            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(ets[0], cpus_little[0], 12,
                                 1); // note: normally it wouldn't fit this way
            k->addForcedDispatch(ets[1], cpus_big[0], 18, 1);
            k->addForcedDispatch(ets[2], cpus_big[1], 18, 1);
            k->addForcedDispatch(
                t1_big1, cpus_big[1], 18,
                1); // it should preempt the executing task on big 0
            // server's free to go wherever.

            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Running simulation!" << std::endl;
            SIMUL.initSingleRun();

            double init_util = 0.0;

            t5->activate(Tick(activ[0]));
            t3->activate(Tick(activ[1]));
            t4->activate(Tick(activ[2]));
            tos2->activate(Tick(11));

            SIMUL.run_to(1); // init setup
            REQUIRE(k->getProcessorRunning(t0_little) == cpus_little[0]);
            REQUIRE(k->getProcessorRunning(t0_big0) == cpus_big[0]);
            REQUIRE(k->getProcessorReady(serv) == cpus_big[0]);
            REQUIRE(k->getProcessorRunning(t0_big1) == cpus_big[1]);
            REQUIRE(k->getProcessorReady(t1_big1) == cpus_big[1]);
            // REQUIRE (double(t0_big0->deadEvt.getTime()) == 5.0); todo

            SIMUL.run_to(5); // t0_big0 ends and tos (serv) starts
            REQUIRE(k->getProcessorRunning(t0_little) == cpus_little[0]);
            REQUIRE(k->getProcessorRunning(serv) == cpus_big[0]);
            REQUIRE(k->getProcessorRunning(t0_big1) == cpus_big[1]);
            REQUIRE(k->getProcessorReady(t1_big1) == cpus_big[1]);
            REQUIRE(serv->getTasks().size() == 1);
            REQUIRE(serv->getTasks().at(0) == tos);
            std::cout << " wcet " << tos->getWCET(1.0) << std::endl;
            REQUIRE((tos->getWCET(1.0) + double(SIMUL.getTime())) == 7.0);
            REQUIRE(k->getCBServer_Utilization(
                serv, init_util, 1.0)); // is 0.2 server util or core active
                                        // util? exp. server util
            REQUIRE(k->getIslandUtilization(1.0, IslandType::BIG, NULL) ==
                    0.2 + 10.0 / 30.0 + 1.0 / 31.0);

            SIMUL.run_to(6); // taskDuring comes and goes ready on big0. Island
                             // util considers server util and tasks on big1
            REQUIRE(k->getProcessorRunning(serv) == cpus_big[0]);
            REQUIRE(k->getProcessorReady(t5) == cpus_big[0]);

            SIMUL.run_to(
                7); // tos ends, yields, goes ready and taskDuring starts
            REQUIRE(serv->isEmpty());
            REQUIRE(k->getProcessorReady(serv) == cpus_big[0]);
            REQUIRE(k->getProcessorRunning(t5) == cpus_big[0]);
            REQUIRE((t5->getWCET(1.0) + double(SIMUL.getTime())) == 9.0);
            REQUIRE(k->getUtilization_active(
                        dynamic_cast<CPU_BL *>(cpus_big[0])) == 0.2);
            REQUIRE(k->getCBServer_Utilization(
                serv, init_util, 1.0)); // is 0.2 server util or core active
                                        // util? exp. core active util

            SIMUL.run_to(
                8); // taskBefore (the server DL) comes and goes ready on big0.
                    // Island util considers u_active of big0 and tasks on big1
            REQUIRE(k->getProcessorRunning(t5) == cpus_big[0]);
            REQUIRE(k->getProcessorReady(serv) == cpus_big[0]);
            REQUIRE(k->getProcessorReady(t3) == cpus_big[0]);

            SIMUL.run_to(9); // taskDuring ends, serv yields and is ready,
                             // taskBefore starts
            REQUIRE(serv->isEmpty());
            REQUIRE(k->getProcessorReady(serv) == cpus_big[0]);
            REQUIRE(k->getProcessorRunning(t3) == cpus_big[0]);
            REQUIRE((t3->getWCET(1.0) + double(SIMUL.getTime())) == 10.0);

            SIMUL.run_to(10); // server deadline, it recharges itself
            REQUIRE(serv->isEmpty());
            for (CPU *c : cpus_big)
                REQUIRE(k->getUtilization_active(dynamic_cast<CPU_BL *>(c)) ==
                        0.0);

            SIMUL.run_to(11); // tos2 comes, server gets running
            REQUIRE(serv->getTasks().size() == 1);
            REQUIRE(serv->getTasks().at(0) == tos2);
            REQUIRE(serv->getStatus() == ServerStatus::EXECUTING);
            REQUIRE(k->getIslandUtilization(1.0, IslandType::BIG, NULL) ==
                    2.0 / 21.0 + 4.0 / 30.0 + 1.0 / 31.0);
            REQUIRE(k->getCBServer_Utilization(
                serv, init_util, 1.0)); // is 0.2 server util or core active
                                        // util? exp. server util
            REQUIRE((tos2->getWCET(1.0) + double(SIMUL.getTime())) == 13.0);

            SIMUL.run_to(12); // taskAfter comes. Island utilization considers
                              // server and tasks on big1
            REQUIRE(k->getProcessorRunning(serv) == cpus_big[0]);
            REQUIRE(k->getProcessorReady(t4) == cpus_big[0]);

            SIMUL.run_to(13); // tos2 ends, task after starts
            REQUIRE(k->getProcessorRunning(t4) == cpus_big[0]);
            REQUIRE(k->getProcessorReady(serv) == cpus_big[0]);
            REQUIRE(k->getUtilization_active(
                        dynamic_cast<CPU_BL *>(cpus_big[0])) == 0.2);
            REQUIRE(k->getProcessorRunning(t0_little) == cpus_little[0]);
            REQUIRE(k->getProcessorRunning(t0_big1) == cpus_big[1]);
            REQUIRE(k->getProcessorReady(t1_big1) == cpus_big[1]);
            REQUIRE((t4->getWCET(1.0) + double(SIMUL.getTime())) == 14.0);

            SIMUL.endSingleRun();
            std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
            std::cout << "Simulation finished" << std::endl;

            return 0;
        }

        EnergyMRTKernel::EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END = 1;
        EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END = 0;

        if (TEST_NO == 20) {
            /**
                2nd example. End of virtual time of task t. Kernel pulls
               (migrates) a task into the ending core. It has WCET > DL_t (t is
               now idle). When t arrives again, it can be scheduled on its
               previous core.
              */
            string names[] = {"B0_killed", "B1", "B2", "B3_running",
                              "B3_ready"};
            int wcets[] = {160, 450, 450, 400, 60};
            int deads[] = {200, 500, 500, 500, 500};
            vector<CBServerCallingEMRTKernel *> ets;
            MissCount mc("miss");
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T" + to_string(TEST_NO) + "_task_BIG_" + names[j];
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deads[j], deads[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                std::cout << instr << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
                mc.attachToTask(t);
                pstrace.attachToTask(*t);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(ets[0], cpus_big[0], 18);
            k->addForcedDispatch(ets[1], cpus_big[1], 18);
            k->addForcedDispatch(ets[2], cpus_big[2], 18);
            k->addForcedDispatch(ets[3], cpus_big[3], 18);
            k->addForcedDispatch(ets[4], cpus_big[3], 18); // ready

            for (CPU_BL *c : cpus_little)
                c->toggleDisabled();

            SIMUL.initSingleRun();

            SIMUL.run_to(
                150); // kill task on big0, task ready on big0 gets running
            std::cout << k->getScheduler()->toString() << std::endl;
            // std::cout <<
            // dynamic_cast<RTKernel*>(ets[0]->getKernel())->getScheduler()->toString()
            // << std::endl;
            ets[0]->killInstance();
            // std::cout << k->getScheduler()->toString() << std::endl;
            // std::cout <<
            // dynamic_cast<RTKernel*>(ets[0]->getKernel())->getScheduler()->toString()
            // << std::endl;exit(0);
            SIMUL.sim_step(); // t=150, but all events have been processed
            std::cout << "u active big 0 "
                      << k->getUtilization_active(cpus_big[0]) << std::endl;
            std::cout << "idle evt " << ets[0]->getIdleEvent() << std::endl;
            std::cout << "server status " << ets[0]->getStatusString()
                      << std::endl;
            REQUIRE(k->getUtilization_active(cpus_big[0]) >
                    0.75); // shall be 0.8
            REQUIRE((double) ets[0]->getIdleEvent() >= 185);
            REQUIRE(ets[0]->getStatus() == ServerStatus::RELEASING);

            SIMUL.run_to(151);
            k->printState(true);
            // no migration, only schedule ready tasks
            REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
            REQUIRE(k->getReadyTasks(cpus_big[0]).size() == 0);

            SIMUL.run_to(189); // end vtime => migration
            REQUIRE(k->getRunningTask(cpus_big[0]) == ets[4]);
            REQUIRE(k->getUtilization_active(cpus_big[0]) == 0.0);

            std::cout << std::endl << "Scheduler state t=189:" << std::endl;
            std::cout << k->getScheduler()->toString() << std::endl
                      << std::endl;

            SIMUL.run_to(201);
            k->printState(true);
            REQUIRE(k->getRunningTask(cpus_big[0]) == ets[0]);
            REQUIRE(k->getReadyTasks(cpus_big[0]).at(0) == ets[4]);

            SIMUL.run_to(501); // all tasks are over, usual dispatch
            k->printState(true);
            for (CBServerCallingEMRTKernel *e : ets)
                REQUIRE(k->getProcessor(e) != NULL);

            REQUIRE(mc.getLastValue() == 0);

            SIMUL.endSingleRun();

            std::cout << "--------------" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            for (AbsRTTask *t : tasks)
                delete t;
            for (CBServerCallingEMRTKernel *cbs : ets)
                delete cbs;
            delete k;
            return 0;
        }
        if (TEST_NO == 21) {
            /**
                3rd example. End of virtual time of task t. Kernel pulls
               (migrates) a task into the ending core. It has WCET > DL_t (t is
               now idle). When t arrives again, it cannot be scheduled on its
               previous core, because U + U_t > 1 (EDF).
              */
            string names[] = {"B0_killed", "B1", "B2", "B3_running",
                              "B3_ready"};
            int wcets[] = {450, 450, 450, 400, 343};
            int deads[] = {500, 500, 500, 500, 500};
            vector<CBServerCallingEMRTKernel *> ets;
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T" + to_string(TEST_NO) + "_task_BIG_" + names[j];
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deads[j], deads[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                std::cout << instr << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(ets[0], cpus_big[0], 18);
            k->addForcedDispatch(ets[1], cpus_big[1], 18);
            k->addForcedDispatch(ets[2], cpus_big[2], 18);
            k->addForcedDispatch(ets[3], cpus_big[3], 18);
            k->addForcedDispatch(ets[4], cpus_big[3], 18); // ready

            for (CPU_BL *c : cpus_little)
                c->toggleDisabled();

            SIMUL.initSingleRun();

            SIMUL.run_to(
                150); // kill task on big0, task ready on big0 gets running
            ets[0]->killInstance();
            SIMUL.sim_step(); // t=150, but all events have been processed
            std::cout << "u active big 0 "
                      << k->getUtilization_active(cpus_big[0]) << std::endl;
            std::cout << "idle evt " << ets[0]->getIdleEvent() << std::endl;
            std::cout << "server status " << ets[0]->getStatusString()
                      << std::endl;
            REQUIRE(k->getUtilization_active(cpus_big[0]) >
                    0.85); // shall be 0.9
            REQUIRE((double) ets[0]->getIdleEvent() > 165);
            REQUIRE(ets[0]->getStatus() == ServerStatus::RELEASING);

            SIMUL.run_to(151);
            k->printState(true);
            // no migration, only schedule ready tasks
            REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
            REQUIRE(k->getReadyTasks(cpus_big[0]).size() == 0);

            SIMUL.run_to(169); // end vtime => migration
            REQUIRE(k->getRunningTask(cpus_big[0]) == ets[4]);
            REQUIRE(k->getUtilization_active(cpus_big[0]) == 0.0);

            std::cout << std::endl << "Scheduler state t=189:" << std::endl;
            std::cout << k->getScheduler()->toString() << std::endl
                      << std::endl;

            SIMUL.run_to(201);
            k->printState(true);

            SIMUL.run_to(451);
            k->printState(true);
            REQUIRE(k->getRunningTask(cpus_big[0]) == ets[4]);
            REQUIRE(k->getRunningTask(cpus_big[1]) == NULL);
            REQUIRE(k->getRunningTask(cpus_big[2]) == NULL);
            REQUIRE(k->getRunningTask(cpus_big[3]) == NULL);

            SIMUL.run_to(501); // all tasks are over, usual dispatch
            k->printState(true);
            for (CPU *c : k->getProcessors(IslandType::BIG))
                REQUIRE(k->getRunningTask(c) != NULL);
            REQUIRE(k->getProcessor(ets[0]) != NULL);
            ets[0]->endRun(); // since it goes scheduled somewhere
            REQUIRE(k->isDispatchable(
                ets[0], cpus_big[0])); // because task WCET 343 has stolen its
                                       // utilization

            SIMUL.endSingleRun();

            std::cout << "--------------" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            for (AbsRTTask *t : tasks)
                delete t;
            for (CBServerCallingEMRTKernel *cbs : ets)
                delete cbs;
            delete k;
            return 0;
        }
        if (TEST_NO == 22) {
            /**
                4th example. Is it true that, when tasks finish or are killed,
               next ready tasks are scheduled or, if no ready task is available
               on the core, there is a migration?
              */
            int wcets[] = {10, 450, 450, 450, 20, 10};
            int deads[] = {500, 500, 500, 500, 500, 500};
            vector<CBServerCallingEMRTKernel *> ets;
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name =
                    "T" + to_string(TEST_NO) + "_task_BIG_" + to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deads[j], deads[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                std::cout << instr << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(ets[0], cpus_big[0], 18);
            k->addForcedDispatch(ets[1], cpus_big[1], 18);
            k->addForcedDispatch(ets[2], cpus_big[2], 18);
            k->addForcedDispatch(ets[3], cpus_big[3], 18);
            k->addForcedDispatch(ets[4], cpus_big[0], 18); // ready
            k->addForcedDispatch(ets[5], cpus_big[3], 18); // ready

            for (CPU_BL *c : cpus_little)
                c->toggleDisabled();

            SIMUL.initSingleRun();

            SIMUL.run_to(11);
            std::cout << "==================" << std::endl;
            std::cout << "t=" << time() << std::endl;
            std::cout << "state of kernel:" << std::endl;
            k->printState(true);
            std::cout << "server status " << ets[0]->getStatusString()
                      << std::endl;
            // recharging VS releasing: if task reaches its WCET (=> budget
            // ends), then recharging, else releasing?
            REQUIRE(ets[0]->getStatus() == ServerStatus::RECHARGING);
            REQUIRE(ets[4] == k->getRunningTask(cpus_big[0]));
            REQUIRE(k->getProcessorReady(ets[5]) == cpus_big[3]);

            SIMUL.run_to(31);
            std::cout << "==================" << std::endl;
            std::cout << "t=" << time() << std::endl;
            std::cout << "state of kernel:" << std::endl;
            k->printState(true);
            std::cout << "server status " << ets[0]->getStatusString()
                      << std::endl;
            REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
            REQUIRE(k->getProcessorReady(ets[5]) == cpus_big[3]);

            SIMUL.run_to(501); // all tasks are over, usual dispatch
            k->printState(true);
            for (CBServerCallingEMRTKernel *e : ets)
                REQUIRE(k->getProcessor(e) != NULL);

            SIMUL.endSingleRun();

            std::cout << "--------------" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            for (AbsRTTask *t : tasks)
                delete t;
            for (CBServerCallingEMRTKernel *cbs : ets)
                delete cbs;
            delete k;
            return 0;
        }

        else if (TEST_NO == 23) {
            /// Does killInstance() on CBS server enveloping periodic tasks
            /// work?
            int wcets[] = {10};
            int deads[] = {200};
            vector<CBServerCallingEMRTKernel *> ets;
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name =
                    "T" + to_string(TEST_NO) + "_task_BIG_" + to_string(j);
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deads[j], deads[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                std::cout << instr << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(ets[0], cpus_big[0], 18);

            for (CPU_BL *c : cpus_little)
                c->toggleDisabled();

            SIMUL.initSingleRun();

            SIMUL.run_to(1);
            std::cout << "==================" << std::endl;
            ets[0]->killInstance();
            k->printState(true);
            REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
            REQUIRE(k->getReadyTasks(cpus_big[0]).empty());
            REQUIRE(k->getUtilization_active(cpus_big[0]) > 0.0);
            SIMUL.run_to(201);
            std::cout << "==================" << std::endl;
            std::cout << "t=" << time() << std::endl;
            std::cout << "state of kernel:" << std::endl;
            k->printState(true);
            REQUIRE(k->getProcessorRunning(ets[0])->getIslandType() ==
                    IslandType::BIG);

            SIMUL.endSingleRun();

            std::cout << "--------------" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            for (AbsRTTask *t : tasks)
                delete t;
            for (CBServerCallingEMRTKernel *cbs : ets)
                delete cbs;
            delete k;
            return 0;
        }

        // end of tests requiring EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END

        // Tests requiring EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END_ADV_CHK

        EnergyMRTKernel::EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END = 1;

        if (TEST_NO == 24) {
            /**
                2nd example repeated, but this time it works as it should. End
               of virtual time of task t. Kernel tries to pull (migrate) a task
               into the ending core, but advanced check fails. It has WCET >
               DL_t (t is now idle).

                Note the advanced check is always needed, in the test above
               migration didn't fail but task missed its deadline.
              */
            string names[] = {"B0_running", "B0_migr", "B1_killed"};
            int wcets[] = {25, 49, 38};
            int deads[] = {100, 100, 60};
            MissCount ms("miss");
            vector<CBServerCallingEMRTKernel *> ets;
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T" + to_string(TEST_NO) + names[j];
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deads[j], deads[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                std::cout << instr << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
                ms.attachToTask(t);
                pstrace.attachToTask(*t);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(ets[0], cpus_big[0], 18);
            k->addForcedDispatch(ets[1], cpus_big[0], 18);
            k->addForcedDispatch(ets[2], cpus_big[1], 18, 2);

            for (CPU_BL *c : cpus_little)
                c->toggleDisabled();
            cpus_big[2]->toggleDisabled();

            SIMUL.initSingleRun();

            SIMUL.run_to(15); // kill task on big1
            ets[2]->killInstance();
            SIMUL.sim_step(); // t=15, but all events have been processed
            std::cout << "u active big 1 "
                      << k->getUtilization_active(cpus_big[1]) << std::endl;
            std::cout << "idle evt " << ets[2]->getIdleEvent() << std::endl;
            std::cout << "server status " << ets[2]->getStatusString()
                      << std::endl;
            REQUIRE(k->getUtilization_active(cpus_big[1]) >
                    0.6); // shall be 0.63
            REQUIRE((double) ets[2]->getIdleEvent() >= 20);
            REQUIRE(ets[2]->getStatus() == ServerStatus::RELEASING);

            SIMUL.run_to(26); // end vtime, migration task ready core 0 to 1
            k->printState(true);
            REQUIRE(k->getUtilization_active(cpus_big[1]) == 0.0);
            REQUIRE(k->getRunningTask(cpus_big[1]) == ets[1]);
            REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
            REQUIRE(k->getReadyTasks(cpus_big[0]).empty());

            SIMUL.run_to(75); // end task migr
            k->printState(true);
            REQUIRE(k->getRunningTask(cpus_big[1]) == ets[2]);
            REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
            REQUIRE(k->getReadyTasks(cpus_big[0]).empty());

            SIMUL.run_to(121);

            REQUIRE(ms.getLastValue() == 0);

            SIMUL.endSingleRun();

            std::cout << "--------------" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            for (AbsRTTask *t : tasks)
                delete t;
            for (CBServerCallingEMRTKernel *cbs : ets)
                delete cbs;
            delete k;
            return 0;
        }

        if (TEST_NO == 25) {
            // Moving towards 3 tasks per core, seing if system crashes.
            // Such task should be schedulable with EDF (sum of utilizations <=
            // 1).
            MissCount mc("miss count");
            bool ONLY_LAST_ONE = 1;
            int task_period = 500;
            unsigned int taskNumber =
                cpus_big.size() * 3 + cpus_little.size() * 3;

            EnergyMRTKernel::EMRTK_BALANCE_ENABLED =
                0; /* Can't imagine disabling it, but so policy is in the list
                      :) */
            EnergyMRTKernel::EMRTK_LEAVE_LITTLE3_ENABLED = 0;
            EnergyMRTKernel::EMRTK_MIGRATE_ENABLED = 1;
            EnergyMRTKernel::EMRTK_CBS_YIELD_ENABLED = 0;
            EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_VTIME = 0;
            EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_END = 0;

            EnergyMRTKernel::EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED = 1;
            EnergyMRTKernel::EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END = 1;
            EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END = 1;

            for (int i = 0; i < 10; i++) {
                EnergyMRTKernel *kern;
                init_suite(&kern);
                REQUIRE(kern != NULL);
                vector<CPU_BL *> cpus_little =
                    kern->getIslandLittle()->getProcessors();
                vector<CPU_BL *> cpus_big =
                    kern->getIslandBig()->getProcessors();
                vector<AbsRTTask *> tasks;

                string filename = "";
                if (ONLY_LAST_ONE)
                    filename = StaffordImporter::getLastGenerated();
                else
                    filename = StaffordImporter::generate(
                        500, 0.6); // periodo, utilizzazione. Why not util=1.0?
                                   // because littles will scale up WCETs => no
                                   // 3 tasks per core
                try {
                    ets = StaffordImporter::getEnvelopedPeriodcTasks(filename,
                                                                     kern, i);
                } catch (std::exception &e) {
                    std::cerr << "Error: " << e.what() << std::endl;
                    return 0;
                }
                assert(ets.size() > 0);

                std::cout << " there are tasks: " << ets.size() << std::endl;
                for (CBServerCallingEMRTKernel *t : ets)
                    std::cout << t->toString() << std::endl;

                for (CBServerCallingEMRTKernel *t : ets) {
                    tasks.push_back(t->getAllTasks().at(0));
                    ttrace.attachToTask(*t->getAllTasks().at(0));
                    mc.attachToTask(
                        dynamic_cast<Task *>(t->getAllTasks().at(0)));
                    pstrace.attachToTask(
                        dynamic_cast<Task &>(*t->getAllTasks().at(0)));
                }

                EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);

                SIMUL.initSingleRun();
                SIMUL.run_to(1);
                std::cout << "t=1, first task scheduling decided" << std::endl;
                k->printState(true);
                SIMUL.run_to(499);
                SIMUL.sim_step();
                k->printState(true);
                for (CPU_BL *cpu : k->getProcessors()) {
                    // assert (k->getRunningTask(cpu) == NULL);
                    assert(k->getReadyTasks(cpu).empty());
                }
                // return 0;
                // usleep(5500000);
                SIMUL.run_to(500);
                int missing = mc.getLastValue();
                assert(missing == 0);
                k->printState(true);
                // return 0;
                SIMUL.run_to(999);
                SIMUL.sim_step();
                k->printState(true);
                for (CPU_BL *cpu : k->getProcessors()) {
                    // assert (k->getRunningTask(cpu) == NULL);
                    assert(k->getReadyTasks(cpu).empty());
                }
                missing = mc.getLastValue();
                assert(missing == 0);
                SIMUL.run_to(1000);
                SIMUL.endSingleRun();

                std::cout << "--------------" << std::endl;
                std::cout << "missing tasks: " << missing << std::endl;
                std::cout << "i = " << i << std::endl;
                std::cout << "Simulation finished, filename=" << filename
                          << std::endl;

                for (AbsRTTask *t : tasks)
                    delete t;
                for (CBServerCallingEMRTKernel *cbs : ets)
                    delete cbs;
                delete k;

                // if (ONLY_LAST_ONE)
                return 0;
            }
            return 0;
        }

        EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END = 1;
        EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_VTIME = 1;
        EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_END = 1;

        if (TEST_NO == 26) {
            /**
                24th example repeated, but this time when task is killed,
               processor goes idles since there is no ready task and no task can
               be migrated definitevely via the migration test. However, it is
               possible to temporarily migrate task 49 into BIG1. When t=60
               (period task 38) arrives, the task 49 goes back to BIG0 and
                continues there for 4 ticks; in the meanwhile, task 38 runs on
               BIG1.

                Only temporary migrations should be used here.
              */
            string names[] = {"B0_running", "B0_temp_migr", "B1_killed"};
            int wcets[] = {25, 75, 38};
            int deads[] = {100, 100, 60};
            MissCount ms("miss");
            vector<CBServerCallingEMRTKernel *> ets;
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T" + to_string(TEST_NO) + names[j];
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deads[j], deads[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                std::cout << instr << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
                ms.attachToTask(t);
                pstrace.attachToTask(*t);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(ets[0], cpus_big[0], 18);
            k->addForcedDispatch(ets[1], cpus_big[0], 18);
            k->addForcedDispatch(ets[2], cpus_big[1], 18);

            for (CPU_BL *c : cpus_little)
                c->toggleDisabled();
            cpus_big[2]->toggleDisabled();
            cpus_big[3]->toggleDisabled();

            SIMUL.initSingleRun();

            SIMUL.run_to(15); // kill task on big1
            ets[2]->killInstance();
            SIMUL.sim_step(); // t=15, but all events have been processed
            std::cout << "u active big 1 "
                      << k->getUtilization_active(cpus_big[1]) << std::endl;
            std::cout << "idle evt " << ets[2]->getIdleEvent() << std::endl;
            std::cout << "server status " << ets[2]->getStatusString()
                      << std::endl;
            REQUIRE(k->getUtilization_active(cpus_big[1]) >
                    0.6); // shall be 0.63
            REQUIRE((double) ets[2]->getIdleEvent() >= 20);
            REQUIRE(ets[2]->getStatus() == ServerStatus::RELEASING);

            SIMUL.run_to(16); // because of temporary migrations, task 49 is
                              // moved to core BIG1
            k->printState(true);
            REQUIRE(k->getRunningTask(cpus_big[1]) == ets[1]);
            REQUIRE(k->getRunningTask(cpus_big[0]) == ets[0]);
            REQUIRE(k->isTaskTemporarilyMigrated(ets[1], cpus_big[1]));

            SIMUL.run_to(26); // end vtime, nothing happens on BIG1 because task
                              // 49 running; BIG0 gets idle
            k->printState(true);
            REQUIRE(k->getUtilization_active(cpus_big[1]) == 0.0);
            REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
            REQUIRE(k->getReadyTasks(cpus_big[0]).empty());
            REQUIRE(k->getRunningTask(cpus_big[1]) == ets[1]);
            REQUIRE(k->isTaskTemporarilyMigrated(ets[1], cpus_big[1]));

            SIMUL.run_to(61); // temporary task moved back to its core
            k->printState(true);
            REQUIRE(k->getRunningTask(cpus_big[0]) == ets[1]);
            REQUIRE(k->getReadyTasks(cpus_big[0]).empty());
            REQUIRE(k->getRunningTask(cpus_big[1]) == ets[2]);
            REQUIRE(k->isTaskTemporarilyMigrated(ets[1], cpus_big[0]) == false);
            REQUIRE(k->isTaskTemporarilyMigrated(ets[1], cpus_big[1]) == false);

            SIMUL.run_to(95);
            k->printState(true);
            REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
            REQUIRE(k->getRunningTask(cpus_big[1]) == ets[2]);

            REQUIRE(ms.getLastValue() == 0);

            SIMUL.endSingleRun();

            std::cout << "--------------" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            for (AbsRTTask *t : tasks)
                delete t;
            for (CBServerCallingEMRTKernel *cbs : ets)
                delete cbs;
            delete k;
            return 0;
        }

        if (TEST_NO == 27) {
            /**
                26th example repeated, but this time I want to be sure
                that, when virtual time ends, if a migration is possible,
                there is temporary migration.
              */

            EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END = 0;
            EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_END = 0;

            string names[] = {"B0_running", "B0_migr", "B1_killed"};
            int wcets[] = {35, 36, 25};
            int deads[] = {100, 100, 60};
            MissCount ms("miss");
            vector<CBServerCallingEMRTKernel *> ets;
            for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
                task_name = "T" + to_string(TEST_NO) + names[j];
                std::cout << "Creating task: " << task_name;
                PeriodicTask *t =
                    new PeriodicTask(deads[j], deads[j], 0, task_name);
                char instr[60] = "";
                sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
                std::cout << instr << std::endl;
                t->insertCode(instr);
                ets.push_back(kern->addTaskAndEnvelope(t, ""));
                ttrace.attachToTask(*t);
                tasks.push_back(t);
                ms.attachToTask(t);
                pstrace.attachToTask(*t);
            }
            EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
            k->addForcedDispatch(ets[0], cpus_big[0], 18);
            k->addForcedDispatch(ets[1], cpus_big[0], 18);
            k->addForcedDispatch(ets[2], cpus_big[1], 18);

            for (CPU_BL *c : cpus_little)
                c->toggleDisabled();
            cpus_big[2]->toggleDisabled();
            cpus_big[3]->toggleDisabled();

            SIMUL.initSingleRun();

            SIMUL.run_to(10); // kill task on big1
            ets[2]->killInstance();
            SIMUL.sim_step(); // t=10, but all events have been processed
            std::cout << "u active big 1 "
                      << k->getUtilization_active(cpus_big[1]) << std::endl;
            std::cout << "idle evt " << ets[2]->getIdleEvent() << std::endl;
            std::cout << "server status " << ets[2]->getStatusString()
                      << std::endl;
            REQUIRE(k->getUtilization_active(cpus_big[1]) >
                    0.4); // shall be 0.41
            REQUIRE((double) ets[2]->getIdleEvent() >= 20);
            REQUIRE(ets[2]->getStatus() == ServerStatus::RELEASING);

            SIMUL.run_to(26); // end vtime, migration of t36 to BIG1
            k->printState(true);
            REQUIRE(k->getUtilization_active(cpus_big[1]) == 0.0);
            REQUIRE(k->getRunningTask(cpus_big[0]) == ets[0]);
            REQUIRE(k->getReadyTasks(cpus_big[0]).empty());
            REQUIRE(k->getRunningTask(cpus_big[1]) == ets[1]);
            REQUIRE(false == k->isTaskTemporarilyMigrated(ets[1], cpus_big[1]));

            SIMUL.run_to(36); // end of t35; t36 remains on BIG1 (thus migration
                              // is not temporary)
            k->printState(true);
            REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
            REQUIRE(k->getReadyTasks(cpus_big[0]).empty());
            REQUIRE(k->getRunningTask(cpus_big[1]) == ets[1]);

            REQUIRE(ms.getLastValue() == 0);

            SIMUL.endSingleRun();

            std::cout << "--------------" << std::endl;
            std::cout << "Simulation finished" << std::endl;
            for (AbsRTTask *t : tasks)
                delete t;
            for (CBServerCallingEMRTKernel *cbs : ets)
                delete cbs;
            delete k;
            return 0;
        }

        SIMUL.run(1000); // 5000
        std::cout << "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~" << std::endl;
        std::cout << "Simulation finished" << std::endl;
    } catch (BaseExc &e) {
        std::cout << e.what() << std::endl;
    }

    return 0;
}

CPUMDescriptor
    quick_bp_descriptor(const std::string &wclass_name,
                        const CPUModelBPParams::SpeedModelParams &sp) {
    std::unique_ptr<CPUModelBPParams> bpp;
    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = wclass_name;
    bpp->params.power = {0, 0, 0, 0};
    bpp->params.speed = sp;

    CPUMDescriptor dummy_desc;
    dummy_desc.name = "dummy_model";
    dummy_desc.type = CPUModelBPParams::key;
    dummy_desc.params.push_back(std::move(bpp));

    return dummy_desc;
}

void dumpSpeeds(const CPUModelBPParams::SpeedModelParams &params) {
    CPUMDescriptor cpumodel_params = quick_bp_descriptor("dummy", params);
    std::unique_ptr<CPUModel> dummy_model =
        CPUModel::create(cpumodel_params, cpumodel_params, OPP{}, 2000.000);

    for (unsigned int f = 200.000; f <= 2000.000; f += 100.000) {
        std::cout << "Slowness of " << f << " is "
                  << 1.0 / dummy_model->lookupSpeed({f, 0}, "dummy")
                  << std::endl;
    }
}

void dumpAllSpeeds() {
    CPUModelBPParams::SpeedModelParams bzip2_cp;

    std::cout << "LITTLE:" << std::endl;
    bzip2_cp = {0.0256054, 2.9809e+6, 0.602631, 8.13712e+9};
    dumpSpeeds(bzip2_cp);

    std::cout << "BIG:" << std::endl;
    bzip2_cp = {0.17833, 1.63265e+6, 1.62033, 118803};
    dumpSpeeds(bzip2_cp);
}

/// Returns true if the value 'eval' and 'expected' are distant 'error'%
bool isInRange(int eval, int expected) {
    const unsigned int error = 5;

    int min = int(eval - eval * error / 100);
    int max = int(eval + eval * error / 100);

    return expected >= min && expected <= max;
}

/// True if min <= eval <= max
bool isInRangeMinMax(double eval, const double min, const double max) {
    return min <= eval <= max;
}

void getCores(vector<CPU_BL *> &cpus_little, vector<CPU_BL *> &cpus_big,
              Island_BL **island_bl_little, Island_BL **island_bl_big) {
    unsigned int OPP_little = 0; // Index of OPP in LITTLE cores
    unsigned int OPP_big = 0; // Index of OPP in big cores

    vector<volt_type> V_little = {
        0.92,   0.919643, 0.919357, 0.918924, 0.95625, 0.9925, 1.02993,
        1.0475, 1.08445,  1.12125,  1.15779,  1.2075,  1.25625};
    vector<freq_type> F_little = {200, 300,  400,  500,  600,  700, 800,
                                  900, 1000, 1100, 1200, 1300, 1400};

    vector<volt_type> V_big = {0.916319, 0.915475, 0.915102, 0.91498, 0.91502,
                               0.90375,  0.916562, 0.942543, 0.96877, 0.994941,
                               1.02094,  1.04648,  1.05995,  1.08583, 1.12384,
                               1.16325,  1.20235,  1.2538,   1.33287};
    vector<freq_type> F_big = {200,  300,  400,  500,  600,  700,  800,
                               900,  1000, 1100, 1200, 1300, 1400, 1500,
                               1600, 1700, 1800, 1900, 2000};

    if (OPP_little >= V_little.size() || OPP_big >= V_big.size())
        exit(-1);

    unsigned long int max_frequency =
        max(F_little[F_little.size() - 1], F_big[F_big.size() - 1]);

    /* ---------------------- Creating CPU Models ----------------------- */

    CPUMDescriptor big_desc;
    CPUMDescriptor little_desc;

    big_desc.name = "balsini_pannocchi_big";
    little_desc.name = "balsini_pannocchi_little";

    big_desc.type = CPUModelBPParams::key;
    little_desc.type = CPUModelBPParams::key;

    /* --------------------- Creating LITTLE Models --------------------- */

    std::unique_ptr<CPUModelBPParams> bpp;

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "idle";
    bpp->params.power = {0.00134845, 1.76307e-5, 124.535, 1.00399e-10};
    bpp->params.speed = {1, 0, 0, 0};
    little_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "idle";
    bpp->params.power = {0.00134845, 1.76307e-5, 124.535, 1.00399e-10};
    bpp->params.speed = {1, 0, 0, 0};
    little_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "bzip2";
    bpp->params.power = {0.00775587, 33.376, 1.54585, 9.53439e-10};
    bpp->params.speed = {0.0256054, 2.9809e+6, 0.602631, 8.13712e+9};
    little_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "hash";
    bpp->params.power = {0.00624673, 176.315, 1.72836, 1.77362e-10};
    bpp->params.speed = {0.00645628, 3.37134e+6, 7.83177, 93459};
    little_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "encrypt";
    bpp->params.power = {0.00676544, 26.2243, 5.6071, 5.34216e-10};
    bpp->params.speed = {6.11496e-78, 3.32246e+6, 6.5652, 115759};
    little_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "decrypt";
    bpp->params.power = {0.00629664, 87.1519, 2.93286, 2.80871e-10};
    bpp->params.speed = {5.0154e-68, 3.31791e+6, 7.154, 112163};
    little_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "cachekiller";
    bpp->params.power = {0.0126737, 67.9915, 1.63949, 3.66185e-10};
    bpp->params.speed = {1.20262, 352597, 2.03511, 169523};
    little_desc.params.push_back(std::move(bpp));

    /* ---------------------- Creating BIG Models ----------------------- */

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "idle";
    bpp->params.power = {0.0162881, 0.00100737, 55.8491, 1.00494e-9};
    bpp->params.speed = {1, 0, 0, 0};
    big_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "bzip2";
    bpp->params.power = {0.0407739, 12.022, 3.33367, 7.4577e-9};
    bpp->params.speed = {0.17833, 1.63265e+6, 1.62033, 118803};
    big_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "hash";
    bpp->params.power = {0.0388215, 16.3205, 4.3418, 5.07039e-9};
    bpp->params.speed = {0.017478, 1.93925e+6, 4.22469, 83048.3};
    big_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "encrypt";
    bpp->params.power = {0.0348728, 8.14399, 5.64344, 7.69915e-9};
    bpp->params.speed = {8.39417e-34, 1.99222e+6, 3.33002, 96949.4};
    big_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "decrypt";
    bpp->params.power = {0.0320508, 25.8727, 3.27135, 4.11773e-9};
    bpp->params.speed = {9.49471e-35, 1.98761e+6, 2.65652, 109497};
    big_desc.params.push_back(std::move(bpp));

    bpp = std::make_unique<CPUModelBPParams>();
    bpp->workload = "cachekiller";
    bpp->params.power = {0.086908, 9.17989, 2.5828, 7.64943e-9};
    bpp->params.speed = {0.825212, 235044, 786.368, 25622.1};
    big_desc.params.push_back(std::move(bpp));

    /* ------------------- Instantiating actual CPUs -------------------- */

    for (unsigned int i = 0; i < 4; ++i) {
        /* Create LITTLE CPUs */
        string cpu_name = "LITTLE_" + to_string(i);
        std::cout << "Creating CPU: " << cpu_name << std::endl;

        CPUModel *pm = CPUModel::create(little_desc, little_desc,
                                        OPP{F_little[F_little.size() - 1],
                                            V_little[V_little.size() - 1]},
                                        max_frequency)
                           .release();

        CPU_BL *c = new CPU_BL(cpu_name, "idle", pm);
        c->setOPP(OPP_little);
        c->setWorkload("idle");
        c->setIndex(4 + i);
        cpus_little.push_back(c);
    }

    for (unsigned int i = 0; i < 4; ++i) {
        /* Create big CPUs */
        string cpu_name = "BIG_" + to_string(i);
        std::cout << "Creating CPU: " << cpu_name << std::endl;

        CPUModel *pm = CPUModel::create(big_desc, big_desc,
                                        OPP{F_big[F_big.size() - 1],
                                            V_big[V_big.size() - 1]},
                                        max_frequency)
                           .release();

        CPU_BL *c = new CPU_BL(cpu_name, "idle", pm);
        c->setOPP(OPP_big);
        c->setWorkload("idle");
        c->setIndex(4 + i);
        cpus_big.push_back(c);
    }

    vector<struct OPP> opps_little = Island_BL::buildOPPs(V_little, F_little);
    vector<struct OPP> opps_big = Island_BL::buildOPPs(V_big, F_big);
    *island_bl_little = new Island_BL("little island", IslandType::LITTLE,
                                      cpus_little, opps_little);
    *island_bl_big =
        new Island_BL("big island", IslandType::BIG, cpus_big, opps_big);
}

int init_suite(EnergyMRTKernel **kern) {
    std::cout << "init_suite" << std::endl;

#if LEAVE_LITTLE3_ENABLED
    std::cout << "Error: tests thought for LEAVE_LITTLE3_ENABLED disabled"
              << std::endl;
    abort();
#endif

    Island_BL *island_bl_big = NULL, *island_bl_little = NULL;
    vector<CPU_BL *> cpus_little, cpus_big;
    vector<Scheduler *> schedulers;
    vector<RTKernel *> kernels;

    getCores(cpus_little, cpus_big, &island_bl_little, &island_bl_big);
    REQUIRE(island_bl_big != NULL);
    REQUIRE(island_bl_little != NULL);
    REQUIRE(cpus_big.size() == 4);
    REQUIRE(cpus_little.size() == 4);

    EDFScheduler *edfsched = new EDFScheduler;
    for (int i = 0; i < 8; i++)
        schedulers.push_back(new EDFScheduler());

    *kern = new EnergyMRTKernel(schedulers, edfsched, island_bl_big,
                                island_bl_little, "The sole kernel");
    kernels.push_back(*kern);

    CPU_BL::referenceFrequency = 2000; // BIG_3 frequency

    std::cout << "end init_suite" << std::endl;
    return 0;
}
