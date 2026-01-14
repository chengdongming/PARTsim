#include <rtsim/cbserver.hpp>
#include <rtsim/energyMRTKernel.hpp>
#include <rtsim/metasim.hpp>
#include <rtsim/mrtkernel.hpp>
#include <rtsim/rttask.hpp>
#include <rtsim/scheduler/edfsched.hpp>
#include <rtsim/taskstat.hpp>

#include "catch.hpp"

using namespace MetaSim;
using namespace RTSim;
using namespace std;

#define time() SIMUL.getTime()
#define FORCE_REQUISITES 1 /* 1 if you want to try all experiments */
#define TEST_NO init_sequence

string workload = "bzip2";
string task_name = "";
int init_sequence = 0;

class Requisite {
public:
    bool satisfied;
    bool EMRTK_leave_little3, EMRTK_migrate, EMRTK_cbs_yield,
        EMRTK_temporarily_migrate_vtime, EMRTK_temporarily_migrate_end,
        EMRTK_cbs_enveloping_per_task_enabled,
        EMRTK_cbs_enveloping_migrate_after_vtime_end,
        EMRTK_cbs_migrate_after_end;

    // in the default case, you don't want neither leave_little3 and migrate and
    // CBS servers yielding
    Requisite(bool EMRTK_leave_little3 = false, bool EMRTK_migrate = true,
              bool EMRTK_cbs_yield = false,
              bool EMRTK_cbs_enveloping_per_task_enabled = true,
              bool EMRTK_cbs_enveloping_migrate_after_vtime_end = false,
              bool EMRTK_cbs_migrate_after_end = true,
              bool EMRTK_temporarily_migrate_vtime = false,
              bool EMRTK_temporarily_migrate_end = false) :
        satisfied(false) {
        this->EMRTK_leave_little3 = EMRTK_leave_little3;
        this->EMRTK_migrate = EMRTK_migrate;
        this->EMRTK_cbs_yield = EMRTK_cbs_yield;
        this->EMRTK_cbs_enveloping_per_task_enabled =
            EMRTK_cbs_enveloping_per_task_enabled;
        this->EMRTK_cbs_enveloping_migrate_after_vtime_end =
            EMRTK_cbs_enveloping_migrate_after_vtime_end;
        this->EMRTK_cbs_migrate_after_end = EMRTK_cbs_migrate_after_end;
        this->EMRTK_temporarily_migrate_vtime = EMRTK_temporarily_migrate_vtime;
        this->EMRTK_temporarily_migrate_end = EMRTK_temporarily_migrate_end;
    }

    string toString() const {
        string s =
            "Requisite with leave_little_3: " + to_string(EMRTK_leave_little3) +
            ", migrate: " + to_string(EMRTK_migrate) +
            ", cbs yielding: " + to_string(EMRTK_cbs_yield) +
            ", cbs_enveloping_per_task_enabled: " +
            to_string(EMRTK_cbs_enveloping_per_task_enabled) +
            ", cbs_enveloping_migrate_after_vtime_end: " +
            to_string(EMRTK_cbs_enveloping_migrate_after_vtime_end) +
            ", cbs_migrate_after_end: " +
            to_string(EMRTK_cbs_migrate_after_end) +
            ", EMRTK_temporarily_migrate_vtime: " +
            to_string(EMRTK_temporarily_migrate_vtime) +
            ", EMRTK_temporarily_migrate_end: " +
            to_string(EMRTK_temporarily_migrate_end);
        return s;
    }
};

map<int, Requisite> performedTests;

void getCores(vector<CPU_BL *> &big, vector<CPU_BL *> &little,
              Island_BL **island_bl_little, Island_BL **island_bl_big);
int init_suite(EnergyMRTKernel **kern);
bool isInRange(int, int);
bool isInRange(Tick t1, int t2) {
    return isInRange(int(t1), t2);
}
bool isInRange(Tick t1, Tick t2) {
    return isInRange(int(t1), int(t2));
}
bool isInRangeMinMax(double eval, const double min, const double max);
bool checkRequisites(Requisite reqs);
bool fixDependencies(Requisite reqs, bool abortOnFix = false);

TEST_CASE("exp0") {
    init_sequence = 0;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);

    task_name = "T0_task1";
    std::cout << "Creating task: " << task_name << std::endl;
    PeriodicTask *t0 = new PeriodicTask(500, 500, 0, task_name);
    t0->insertCode("fixed(500," + workload +
                   ");"); // WCET 500 at max frequency on big cores
    CBServerCallingEMRTKernel *et_t0 = kern->addTaskAndEnvelope(t0, "");

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
    delete t0;
    delete kern;
    delete et_t0;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp1") {
    init_sequence = 1;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);

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
    delete t1;
    delete t0;
    delete kern;
    delete et_t0;
    delete et_t1;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp2") {
    init_sequence = 2;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);

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
        std::cout << "running :" << s << endl;

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
    delete t0;
    delete t1;
    delete kern;
    delete et_t0;
    delete et_t1;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp3") {
    init_sequence = 3;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);

    task_name = "T3_task1";
    std::cout << "Creating task: " << task_name << std::endl;
    PeriodicTask *t0 = new PeriodicTask(500, 500, 0, task_name);
    t0->insertCode("fixed(10," + workload +
                   ");"); // WCET 10 at max frequency on big cores
    CBServerCallingEMRTKernel *et_t0 = kern->addTaskAndEnvelope(t0, "");

    SIMUL.initSingleRun();
    SIMUL.run_to(10);

    std::cout << "t=" << SIMUL.getTime() << std::endl;
    CPU_BL *c0 = dynamic_cast<CPU_BL *>(kern->getProcessor(et_t0));

    REQUIRE(t0->getName() == "T3_task1");
    REQUIRE(c0->getFrequency() == 500);
    REQUIRE(c0->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(t0->getWCET(c0->getSpeed())), 65));

    SIMUL.endSingleRun();
    delete t0;
    delete kern;
    delete et_t0;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp4") {
    init_sequence = 4;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    PeriodicTask *task[5]; // to be cleared after each test
    vector<CBServerCallingEMRTKernel *> ets;
    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);

    for (int j = 0; j < 4; j++) {
        task_name = "T4_Task_LITTLE_" + std::to_string(j);
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(500, 500, 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(100, %s);", workload.c_str());
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));

        task[j] = t;
    }

    SIMUL.initSingleRun();
    SIMUL.run_to(1);

    CPU_BL *c0 = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(0)));
    CPU_BL *c1 = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(1)));
    CPU_BL *c2 = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(2)));
    CPU_BL *c3 = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(3)));

    REQUIRE(task[0]->getName() == "T4_Task_LITTLE_0");
    REQUIRE(c0->getFrequency() == 700);
    REQUIRE(c0->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(task[0]->getWCET(c0->getSpeed())), 488));

    REQUIRE(task[1]->getName() == "T4_Task_LITTLE_1");
    REQUIRE(c1->getFrequency() == 700);
    REQUIRE(c1->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(task[1]->getWCET(c1->getSpeed())), 488));

    REQUIRE(task[2]->getName() == "T4_Task_LITTLE_2");
    REQUIRE(c2->getFrequency() == 700);
    REQUIRE(c2->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(task[2]->getWCET(c2->getSpeed())), 488));

    REQUIRE(task[3]->getName() == "T4_Task_LITTLE_3");
    REQUIRE(c3->getFrequency() == 700);
    REQUIRE(c3->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(task[3]->getWCET(c3->getSpeed())), 488));

    SIMUL.endSingleRun();
    for (int j = 0; j < 4; j++) {
        delete task[j];
        delete ets.at(j);
    }
    delete kern;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp5") {
    init_sequence = 5;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    vector<CPU_BL *> cpus;
    PeriodicTask *task[5]; // to be cleared after each test
    vector<CBServerCallingEMRTKernel *> ets;
    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);

    for (int j = 0; j < 4; j++) {
        int wcet = 5; //* (j+1);
        task_name = "T5_task" + std::to_string(j);
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(500, 500, 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcet, workload.c_str());
        std::cout << " with abs. WCET " << wcet << std::endl;
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));

        task[j] = t;
        // LITTLE_0, _1, _2, _3 freq 1400.
    }

    SIMUL.initSingleRun();
    SIMUL.run_to(1);

    CPU_BL *c0 = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(0)));
    CPU_BL *c1 = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(1)));
    CPU_BL *c2 = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(2)));
    CPU_BL *c3 = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(3)));
    std::cout << c0->toString() << std::endl;
    std::cout << c1->toString() << std::endl;
    std::cout << c2->toString() << std::endl;
    std::cout << c3->toString() << std::endl;

    PeriodicTask *t = task[0];
    REQUIRE(t->getName() == "T5_task0");
    REQUIRE(c0->getFrequency() == 500);
    REQUIRE(c0->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(t->getWCET(c0->getSpeed())), 32));
    std::cout << t->toString() << " on " << c0->toString() << endl;

    t = task[1];
    REQUIRE(t->getName() == "T5_task1");
    REQUIRE(c1->getFrequency() == 500);
    REQUIRE(c1->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(t->getWCET(c1->getSpeed())), 32));
    std::cout << t->toString() << " on " << c1->toString() << endl;

    t = task[2];
    REQUIRE(t->getName() == "T5_task2");
    REQUIRE(c2->getFrequency() == 500);
    REQUIRE(c2->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(t->getWCET(c2->getSpeed())), 32));
    std::cout << t->toString() << " on " << c2->toString() << endl;

    t = task[3];
    REQUIRE(t->getName() == "T5_task3");
    REQUIRE(c3->getFrequency() == 500);
    REQUIRE(c3->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(t->getWCET(c3->getSpeed())), 32));
    std::cout << t->toString() << " on " << c3->toString() << endl;

    SIMUL.endSingleRun();
    for (int j = 0; j < 4; j++) {
        delete task[j];
        delete ets.at(j);
    }
    delete kern;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

// test showing that frequency of little/big island may be raised
TEST_CASE("exp6") {
    /* This experiment shows that other CPUs frequencies are increased for the
        entire island after a decision for other tasks has already been made*/
    init_sequence = 6;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    vector<CPU_BL *> cpus;
    CPU_BL *cpu_task[5]; // to be cleared after each test
    PeriodicTask *task[5]; // to be cleared after each test
    vector<CBServerCallingEMRTKernel *> ets;
    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);

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

        task[j] = t;
    }

    SIMUL.initSingleRun();
    SIMUL.run_to(1);

    for (int j = 0; j < 5; j++) {
        cout << j << std::endl;
        cpu_task[j] = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(j)));
        if (j == 4)
            cpu_task[j] = kern->getProcessorReady(ets.at(j));
    }

    i = 0;
    PeriodicTask *t = task[i];
    CPU_BL *c = cpu_task[i];
    REQUIRE(t->getName() == "T6_task0");
    REQUIRE(c->getFrequency() == 2000);
    REQUIRE(c->getIslandType() == IslandType::BIG);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 299));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    i = 1;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T6_task1");
    REQUIRE(c->getFrequency() == 2000);
    REQUIRE(c->getIslandType() == IslandType::BIG);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 299));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    i = 2;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T6_task2");
    REQUIRE(c->getFrequency() == 2000);
    REQUIRE(c->getIslandType() == IslandType::BIG);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 299));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    i = 3;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T6_task3");
    REQUIRE(c->getFrequency() == 2000);
    REQUIRE(c->getIslandType() == IslandType::BIG);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 299));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    i = 4;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T6_task4");
    REQUIRE(c->getFrequency() == 2000);
    REQUIRE(c->getIslandType() == IslandType::BIG);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 199));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    SIMUL.endSingleRun();
    for (int j = 0; j < 5; j++) {
        delete task[j];
        delete ets.at(j);
    }
    delete kern;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp7") {
    init_sequence = 7;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    vector<CPU_BL *> cpus;
    PeriodicTask *task[5]; // to be cleared after each test
    CPU_BL *cpu_task[5]; // to be cleared after each test
    vector<CBServerCallingEMRTKernel *> ets;
    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);

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
        cpu_task[j] = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(j)));
    }

    i = 0;
    PeriodicTask *t = task[i];
    CPU_BL *c = cpu_task[i];
    REQUIRE(t->getName() == "T7_task0");
    REQUIRE(c->getFrequency() == 500);
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 415));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    i = 1;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T7_task1");
    REQUIRE(c->getFrequency() == 500);
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 415));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    i = 2;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T7_task2");
    REQUIRE(c->getFrequency() == 500);
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 415));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    i = 3;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T7_task3");
    REQUIRE(c->getFrequency() == 500);
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 415));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    i = 4;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T7_task4");
    REQUIRE(c->getFrequency() == 700);
    REQUIRE(c->getIslandType() == IslandType::BIG);
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 75));
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));

    SIMUL.run_to(1000);
    SIMUL.endSingleRun();
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        delete task[j];
        delete ets.at(j);
    }
    delete kern;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp8") {
    init_sequence = 8;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    vector<CPU_BL *> cpus;
    PeriodicTask *task[9]; // to be cleared after each test
    CPU_BL *cpu_task[9]; // to be cleared after each test
    vector<CBServerCallingEMRTKernel *> ets;
    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);

    int wcets[] = {181, 419, 261, 163, 65, 8, 61, 170, 273};
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name = "T8_task" + std::to_string(j);
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(500, 500, 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
        task[j] = t;
    }
    // towards random workloads...

    SIMUL.initSingleRun();
    SIMUL.run_to(1);

    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        cpu_task[j] = dynamic_cast<CPU_BL *>(kern->getProcessor(ets.at(j)));
    }

    int i = 0;
    PeriodicTask *t = task[i];
    CPU_BL *c = cpu_task[i];
    REQUIRE(t->getName() == "T8_task" + to_string(i));
    REQUIRE(c->getFrequency() == 1400);
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 499));

    i = 1;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T8_task" + to_string(i));
    REQUIRE(c->getFrequency() == 1700);
    REQUIRE(c->getIslandType() == IslandType::BIG);
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 477));

    i = 2;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T8_task" + to_string(i));
    REQUIRE(c->getFrequency() == 1700);
    REQUIRE(c->getIslandType() == IslandType::BIG);
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 297));

    i = 3;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T8_task" + to_string(i));
    REQUIRE(c->getFrequency() == 1400);
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 449));

    i = 4;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T8_task" + to_string(i));
    REQUIRE(c->getFrequency() == 1400);
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 179));

    i = 5;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T8_task" + to_string(i));
    REQUIRE(c->getFrequency() == 1400);
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 22));

    i = 6;
    t = task[i];
    c = kern->getProcessorReady(t);
    REQUIRE(t->getName() == "T8_task" + to_string(i));
    std::cout << "a" << endl;
    REQUIRE(c->getFrequency() == 1400);
    std::cout << "b" << endl;
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    std::cout << "c" << endl;
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 168));

    i = 7;
    t = task[i];
    c = kern->getProcessorReady(t);
    REQUIRE(t->getName() == "T8_task" + to_string(i));
    REQUIRE(c->getFrequency() == 1400);
    REQUIRE(c->getIslandType() == IslandType::LITTLE);
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 468));

    i = 8;
    t = task[i];
    c = cpu_task[i];
    REQUIRE(t->getName() == "T8_task" + to_string(i));
    REQUIRE(c->getFrequency() == 1700);
    REQUIRE(c->getIslandType() == IslandType::BIG);
    printf("aaa %s scheduled on %s freq %lu with wcet %f\n",
           t->getName().c_str(), c->toString().c_str(), c->getFrequency(),
           t->getWCET(c->getSpeed()));
    REQUIRE(isInRange(int(t->getWCET(c->getSpeed())), 310));

    SIMUL.endSingleRun();
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        delete task[j];
        delete ets.at(j);
    }
    delete kern;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp9") {
    init_sequence = 9;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req(false, true);
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();

    int wcets[] = {101, 101, 101, 8, 200, 500, 500, 500, 101, 1}; // 9 tasks
    vector<PeriodicTask *> tasks;
    vector<CBServerCallingEMRTKernel *> ets;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name = "T" + to_string(init_sequence) + "_task" + to_string(j);
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(500, 500, 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
        tasks.push_back(t);
    }
    EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
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
    // REQUIRE(k->getProcessor(ets[3]) == cpus_little[3]); has ended already

    REQUIRE(k->getProcessor(ets[4]) == cpus_big[0]);
    REQUIRE(k->getProcessor(ets[5]) == cpus_big[1]);
    REQUIRE(k->getProcessor(ets[6]) == cpus_big[2]);
    REQUIRE(k->getProcessor(ets[7]) == cpus_big[3]);

    // task8 comes in place of task3
    REQUIRE(k->getProcessor(ets[8]) == cpus_little[3]);
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
        if (c == cpus_big[0] && ets[4]->getStatus() == ServerStatus::RELEASING)
            REQUIRE(isInRange(queues->getUtilization_active(c),
                              200.0 / c->getSpeed() / 500.0));
        else
            REQUIRE(queues->getUtilization_active(c) == 0.0);

    REQUIRE(k->getProcessor(ets[0]) == cpus_little[0]);
    REQUIRE(k->getProcessor(ets[1]) == cpus_little[1]);
    REQUIRE(k->getProcessor(ets[2]) == cpus_little[2]);
    // REQUIRE(k->getProcessor(ets[3]) == cpus_little[3]); has ended already
    std::cout << "eccomi1 " << ets[3]->toString() << ", "
              << ets[3]->getEndOfVirtualTimeEvent() << std::endl;

    // REQUIRE(k->getProcessor(ets[4]) == cpus_big[0]); has ended already
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
                              200.0 / c->getSpeed(18) / 500.0));
        else if (dynamic_cast<CPU_BL *>(c)->getIslandType() == IslandType::BIG)
            REQUIRE(isInRange(queues->getUtilization_active(c),
                              500.0 / c->getSpeed(18) / 500.0));
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

    SIMUL.run_to(1000);
    SIMUL.endSingleRun();

    std::cout << "-----------------------" << std::endl;
    std::cout << "Simulation has finished" << std::endl;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        delete tasks[j];
        delete ets[j];
    }
    delete kern;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp10") {
    /**
        At time 0, you have a task on a CPU, which is under a context switch.
       Say it lasts 30 ticks. Then, another task, more important, arrives at
       time 6. Would this last task begin its context switch at time 30, thus
       being scheduled at time 30+30=60? Experiment requires EDF scheduler.
      */
    init_sequence = 10;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();

    int wcets[] = {30, 30};
    int deadl[] = {500, 400};
    int activ[] = {0, 6};
    vector<PeriodicTask *> tasks;
    vector<CBServerCallingEMRTKernel *> ets;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name = "T" + to_string(init_sequence) + "_task" + to_string(j);
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(deadl[j], deadl[j], 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
        tasks.push_back(t);
    }
    EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
    MultiCoresScheds *queues = k->getEnergyMultiCoresScheds();

    k->setContextSwitchDelay(Tick(8));
    k->addForcedDispatch(ets[0], cpus_big[0], 18);
    k->addForcedDispatch(ets[1], cpus_big[0], 18);

    SIMUL.initSingleRun();
    dynamic_cast<Task *>(tasks[1])->activate(Tick(activ[1]));
    // other checks are: at t=1 task0 belongs big0, at t=8 task1 belongs big0
    // at t=8+8=16 task1 begins running, at t=16+30=46 task1 ends.
    // at t=46+8=54 task0 runs on big0, at t=54+30=84 task0 ends and big0's free
    SIMUL.run_to(17);
    REQUIRE(k->getProcessor(ets[1]) == cpus_big[0]);
    REQUIRE(k->getProcessorReady(tasks[0]) == cpus_big[0]);

    SIMUL.run_to(55);
    REQUIRE(k->getProcessorRunning(tasks[0]) == cpus_big[0]);

    SIMUL.run_to(85);
    REQUIRE(!cpus_big[0]->isBusy());

    SIMUL.run_to(400);
    std::cout << queues->getUtilization_active(cpus_big[0]) << endl;

    SIMUL.run_to(500);
    REQUIRE(queues->getUtilization_active(cpus_big[0]) == 0.0);

    SIMUL.run_to(1000);
    SIMUL.endSingleRun();
    std::cout << "-----------" << std::endl;
    std::cout << "Simulation finished" << std::endl;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++)
        delete tasks[j];
    delete kern;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp13") {
    /**
        Demostrating what happens when a task is killed.
        It will come into play again in its next period and hopefully it can
        be scheduled.
      */
    init_sequence = 13;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;
    performedTests[init_sequence] = req;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<RTKernel *> kernels = {kern};
    vector<AbsRTTask *> tasks;
    vector<CBServerCallingEMRTKernel *> ets;
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();

    int wcets[] = {450, 450, 450, 450, 1};
    int deads[] = {500, 500, 500, 500, 500};
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name = "T13_task_BIG_" + to_string(j);
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(deads[j], deads[j], 0, task_name);
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
    REQUIRE(
        dynamic_cast<CPU_BL *>(k->getProcessor(tasks[0]))->getIslandType() ==
        IslandType::BIG);
    REQUIRE(
        dynamic_cast<CPU_BL *>(k->getProcessor(tasks[1]))->getIslandType() ==
        IslandType::BIG);
    REQUIRE(
        dynamic_cast<CPU_BL *>(k->getProcessor(tasks[2]))->getIslandType() ==
        IslandType::BIG);
    REQUIRE(
        dynamic_cast<CPU_BL *>(k->getProcessor(tasks[3]))->getIslandType() ==
        IslandType::BIG);
    REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessorReady(tasks[4]))
                ->getIslandType() == IslandType::BIG);

    SIMUL.run_to(1000);
    SIMUL.endSingleRun();

    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++)
        delete tasks[j];
    delete kern;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp14") {
    /**
        What happens if task is not schedulable?

        @Mr. Cucinotta, Mr. Marinoni:
        This example also demonstrates that a task is either on the
       queue/scheduler of arrived tasks or in the one of the core selected for
       its dispatching. Moreover, if a task cannot be scheduled in its current,
       it has another chance on the next one.
      */
    init_sequence = 14;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req;
    if (!checkRequisites(req))
        return;
    performedTests[init_sequence] = req;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<RTKernel *> kernels = {kern};
    vector<AbsRTTask *> tasks;
    vector<CBServerCallingEMRTKernel *> ets;
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();

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
        PeriodicTask *t = new PeriodicTask(deads[j], deads[j], 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        std::cout << instr << std::endl;
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
        tasks.push_back(t);

        t->killOnMiss(false);
    }

    SIMUL.initSingleRun();

    SIMUL.run_to(1);
    EnergyMRTKernel *k = dynamic_cast<EnergyMRTKernel *>(kern);
    REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[0])) == cpus_big[0]);
    REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessorReady(tasks[1])) ==
            cpus_big[0]);
    REQUIRE(k->getScheduler()->isInQueue(tasks[0]) ==
            false); // an executing task
    REQUIRE(k->getScheduler()->isInQueue(tasks[1]) == false); // a ready task
    REQUIRE(k->getScheduler()->isInQueue(tasks[2]) ==
            false); // a discarded task
    REQUIRE(k->getScheduler()->isInQueue(tasks[3]) ==
            false); // a discarded task

    REQUIRE(k->getEnergyMultiCoresScheds()
                ->getScheduler(k->getProcessor(tasks[0]))
                ->isInQueue(ets[0]) == true);
    REQUIRE(k->getEnergyMultiCoresScheds()
                ->getScheduler(k->getProcessor(tasks[0]))
                ->isInQueue(ets[1]) == true);
    REQUIRE(k->getEnergyMultiCoresScheds()->isInAnyQueue(tasks[2]) == NULL);
    REQUIRE(k->getEnergyMultiCoresScheds()->isInAnyQueue(tasks[3]) == NULL);

    SIMUL.run_to(501);
    // same tests as above
    REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessor(tasks[0])) == cpus_big[0]);
    REQUIRE(dynamic_cast<CPU_BL *>(k->getProcessorReady(tasks[1])) ==
            cpus_big[0]);
    REQUIRE(k->getScheduler()->isInQueue(tasks[0]) ==
            false); // an executing task
    REQUIRE(k->getScheduler()->isInQueue(tasks[1]) == false); // a ready task
    REQUIRE(k->getScheduler()->isInQueue(tasks[2]) ==
            false); // a discarded task
    REQUIRE(k->getScheduler()->isInQueue(tasks[3]) ==
            false); // a discarded task

    REQUIRE(k->getEnergyMultiCoresScheds()
                ->getScheduler(k->getProcessor(tasks[0]))
                ->isInQueue(ets[0]) == true);
    REQUIRE(k->getEnergyMultiCoresScheds()
                ->getScheduler(k->getProcessor(tasks[0]))
                ->isInQueue(ets[1]) == true);
    REQUIRE(k->getEnergyMultiCoresScheds()->isInAnyQueue(tasks[2]) == NULL);
    REQUIRE(k->getEnergyMultiCoresScheds()->isInAnyQueue(tasks[3]) == NULL);

    SIMUL.run_to(1000);
    SIMUL.endSingleRun();

    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++)
        delete tasks[j];
    delete kern;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

/// Experiments suggested by Mr.Marinoni: ready after task ends, migrate after
/// vtime, dispatch at period begin. These are actually useless tests because if
/// EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END_ADV_CHK is disabled, the
/// algorithm is not correct

TEST_CASE("Experiment 20") {
    /**
        2nd example. End of virtual time of task t. Kernel pulls (migrates) a
       task into the ending core. It has WCET > DL_t (t is now idle). When t
       arrives again, it can be scheduled on its previous core.
      */
    init_sequence = 20;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req(false, true, false, true, true, false, true);
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();
    vector<AbsRTTask *> tasks;
    // MissCount miss_count("miss");

    string names[] = {"B0_killed", "B1", "B2", "B3_running", "B3_ready"};
    int wcets[] = {160, 450, 450, 400, 60};
    int deads[] = {200, 500, 500, 500, 500};
    vector<CBServerCallingEMRTKernel *> ets;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name = "T" + to_string(init_sequence) + "_task_BIG_" + names[j];
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(deads[j], deads[j], 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        std::cout << instr << std::endl;
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
        tasks.push_back(t);
        // miss_count.attachToTask(t);
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

    SIMUL.run_to(150); // kill task on big0, task ready on big0 gets running
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
    std::cout << "u active big 0 " << k->getUtilization_active(cpus_big[0])
              << std::endl;
    std::cout << "idle evt " << ets[0]->getIdleEvent() << std::endl;
    std::cout << "server status " << ets[0]->getStatusString() << std::endl;
    REQUIRE(k->getUtilization_active(cpus_big[0]) > 0.75); // shall be 0.8
    REQUIRE((double) ets[0]->getIdleEvent() >= 185);
    REQUIRE(ets[0]->getStatus() == ServerStatus::RELEASING);

    SIMUL.run_to(151);
    k->printState(true);
    // no migration, only schedule ready tasks
    REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
    REQUIRE(k->getReadyTasks(cpus_big[0]).size() == 0);

    SIMUL.run_to(189); // end vtime => migration
    k->printState(true);
    REQUIRE(k->getUtilization_active(cpus_big[0]) == 0.0);
    REQUIRE(k->getRunningTask(cpus_big[0]) == ets[4]);

    std::cout << std::endl << "Scheduler state t=189:" << endl;
    std::cout << k->getScheduler()->toString() << std::endl << std::endl;

    SIMUL.run_to(201);
    k->printState(true);
    REQUIRE(k->getRunningTask(cpus_big[0]) == ets[0]);
    REQUIRE(k->getReadyTasks(cpus_big[0]).at(0) == ets[4]);

    SIMUL.run_to(501); // all tasks are over, usual dispatch
    k->printState(true);
    for (CBServerCallingEMRTKernel *e : ets)
        REQUIRE(k->getProcessor(e) != NULL);

    // assert (miss_count.getLastValue() == 0);

    SIMUL.endSingleRun();

    std::cout << "--------------" << std::endl;
    std::cout << "Simulation finished" << std::endl;
    for (AbsRTTask *t : tasks)
        delete t;
    for (CBServerCallingEMRTKernel *cbs : ets)
        delete cbs;
    delete k;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

// test not more working because tasks are not allowed anymore to steal
// utilization from task whose vtime ends TEST_CASE ("Experiment 21") {
//     /**
//         3rd example. End of virtual time of task t. Kernel pulls (migrates) a
//         task into the ending core. It has WCET > DL_t (t is now idle). When t
//         arrives again, it cannot be scheduled on its previous core, because U
//         + U_t > 1 (EDF).
//       */
//     init_sequence = 21;
//     std::cout << "Begin of experiment " << init_sequence << std::endl;
//     Requisite req(false, true, false, true, true, false, true, true);
//     if (!checkRequisites( req ))  return;

//     EnergyMRTKernel *kern;
//     init_suite(&kern);
//     REQUIRE(kern != NULL);
//     vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
//     vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();
//     vector<AbsRTTask*> tasks;

//     string  names[] = { "B0_killed", "B1", "B2", "B3_running", "B3_ready" };
//     int     wcets[] = { 450, 450, 450, 400, 343 }; // I know, these task
//     wouldn't normally fit with EDF int     deads[] = { 500, 500, 500, 500,
//     500 }; vector<CBServerCallingEMRTKernel*> ets; for (int j = 0; j <
//     sizeof(wcets) / sizeof(wcets[0]); j++) {
//         task_name = "T" + to_string(init_sequence) + "_task_BIG_" + names[j];
//         std::cout << "Creating task: " << task_name;
//         PeriodicTask* t = new PeriodicTask(deads[j], deads[j], 0, task_name);
//         char instr[60] = "";
//         sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
//         std::cout << instr << std::endl;
//         t->insertCode(instr);
//         ets.push_back(kern->addTaskAndEnvelope(t, ""));
//         tasks.push_back(t);
//     }
//     EnergyMRTKernel* k = dynamic_cast<EnergyMRTKernel*>(kern);
//     k->addForcedDispatch(ets[0], cpus_big[0], 18);
//     k->addForcedDispatch(ets[1], cpus_big[1], 18);
//     k->addForcedDispatch(ets[2], cpus_big[2], 18);
//     k->addForcedDispatch(ets[3], cpus_big[3], 18);
//     k->addForcedDispatch(ets[4], cpus_big[3], 18); // ready

//     for (CPU_BL* c : cpus_little)
//         c->toggleDisabled();

//     SIMUL.initSingleRun();

//     SIMUL.run_to(150); // kill task on big0, task ready on big0 gets running
//     ets[0]->killInstance();
//     SIMUL.sim_step(); // t=150, but all events have been processed
//     std::cout << "u active big 0 " << k->getUtilization_active(cpus_big[0])
//     << std::endl; std::cout << "idle evt " << ets[0]->getIdleEvent() <<
//     std::endl; std::cout << "server status " << ets[0]->getStatusString() <<
//     std::endl; REQUIRE (k->getUtilization_active(cpus_big[0]) > 0.85); //
//     shall be 0.9 REQUIRE ( (double) ets[0]->getIdleEvent() > 165); REQUIRE
//     (ets[0]->getStatus() == ServerStatus::RELEASING);

//     SIMUL.run_to(151);
//     k->printState(true);
//     // no migration, only schedule ready tasks
//     REQUIRE (k->getRunningTask(cpus_big[0]) == NULL);
//     REQUIRE (k->getReadyTasks(cpus_big[0]).size() == 0);

//     SIMUL.run_to(169); // end vtime => migration
//     k->printState(true);
//     REQUIRE (k->getRunningTask(cpus_big[0]) == ets[4]);
//     REQUIRE (k->getUtilization_active(cpus_big[0]) == 0.0);

//     std::cout << std::endl << "Scheduler state t=189:"<<endl;
//     std::cout << k->getScheduler()->toString() << std::endl << std::endl;

//     SIMUL.run_to(201);
//     k->printState(true);

//     SIMUL.run_to(451);
//     k->printState(true);
//     REQUIRE (k->getRunningTask(cpus_big[0]) == ets[4]);
//     REQUIRE (k->getRunningTask(cpus_big[1]) == NULL);
//     REQUIRE (k->getRunningTask(cpus_big[2]) == NULL);
//     REQUIRE (k->getRunningTask(cpus_big[3]) == NULL);

//     SIMUL.run_to(501); // all tasks are over, usual dispatch
//     k->printState(true);
//     for (CPU* c : k->getProcessors(IslandType::BIG))
//         REQUIRE (k->getRunningTask(c) != NULL);
//     REQUIRE (k->getProcessor(ets[0]) != NULL);
//     ets[0]->endRun(); // since it goes scheduled somewhere
//     REQUIRE (k->isDispatchable(ets[0], cpus_big[0])); // because task WCET
//     343 has stolen its utilization

//     SIMUL.endSingleRun();

//     std::cout << "--------------" << std::endl;
//     std::cout << "Simulation finished" << std::endl;
//     for (AbsRTTask *t : tasks)
//         delete t;
//     for (CBServerCallingEMRTKernel* cbs : ets)
//         delete cbs;
//     delete k;
//     std::cout << "End of Experiment #" << init_sequence << std::endl <<
//     std::endl; performedTests[init_sequence] = req;
// }

TEST_CASE("Experiment 22") {
    /**
        4th example. Is it true that, when tasks finish or are killed, next
       ready tasks are scheduled or, if no ready task is available on the core,
       there is a migration?
      */
    init_sequence = 22;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req(false, true, false, true, true, false, true);
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();
    vector<AbsRTTask *> tasks;

    int wcets[] = {10, 450, 450, 450, 20, 10};
    int deads[] = {500, 500, 500, 500, 500, 500};
    vector<CBServerCallingEMRTKernel *> ets;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name =
            "T" + to_string(init_sequence) + "_task_BIG_" + to_string(j);
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(deads[j], deads[j], 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        std::cout << instr << std::endl;
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
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
    std::cout << "server status " << ets[0]->getStatusString() << std::endl;
    // recharging VS releasing: if task reaches its WCET (=> budget ends), then
    // recharging, else releasing?
    REQUIRE(ets[0]->getStatus() == ServerStatus::RECHARGING);
    REQUIRE(ets[4] == k->getRunningTask(cpus_big[0]));
    REQUIRE(k->getProcessorReady(ets[5]) == cpus_big[3]);

    SIMUL.run_to(31);
    std::cout << "==================" << std::endl;
    std::cout << "t=" << time() << std::endl;
    std::cout << "state of kernel:" << std::endl;
    k->printState(true);
    std::cout << "server status " << ets[0]->getStatusString() << std::endl;
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
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("Experiment 23") {
    /// Does killInstance() on CBS server enveloping periodic tasks work?
    init_sequence = 23;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req(false, true, false, true, true, false, true);
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();
    vector<AbsRTTask *> tasks;

    int wcets[] = {10};
    int deads[] = {200};
    vector<CBServerCallingEMRTKernel *> ets;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name =
            "T" + to_string(init_sequence) + "_task_BIG_" + to_string(j);
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(deads[j], deads[j], 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        std::cout << instr << std::endl;
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
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
    REQUIRE(k->getProcessorRunning(ets[0])->getIslandType() == IslandType::BIG);

    SIMUL.endSingleRun();

    std::cout << "--------------" << std::endl;
    std::cout << "Simulation finished" << std::endl;
    for (AbsRTTask *t : tasks)
        delete t;
    for (CBServerCallingEMRTKernel *cbs : ets)
        delete cbs;
    delete k;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("test 24, advanced check on vtime idle") {
    /**
        2nd example repeated, but this time it works as it should. End of
       virtual time of task t. Kernel tries to pull (migrate) a task into the
       ending core, but advanced check fails. It has WCET > DL_t (t is now
       idle).

        Note the advanced check is always needed, in the test above migration
       didn't fail but task missed its deadline.
      */
    init_sequence = 24;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req(false, true, false, true, true, false, true);
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();
    vector<AbsRTTask *> tasks;

    string names[] = {"B0_running", "B0_migr", "B1_killed"};
    int wcets[] = {25, 49, 38};
    int deads[] = {100, 100, 60};
    // MissCount ms("miss");
    vector<CBServerCallingEMRTKernel *> ets;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name = "T" + to_string(init_sequence) + names[j];
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(deads[j], deads[j], 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        std::cout << instr << std::endl;
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
        tasks.push_back(t);
        // ms.attachToTask(t);
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
    std::cout << "u active big 1 " << k->getUtilization_active(cpus_big[1])
              << std::endl;
    std::cout << "idle evt " << ets[2]->getIdleEvent() << std::endl;
    std::cout << "server status " << ets[2]->getStatusString() << std::endl;
    REQUIRE(k->getUtilization_active(cpus_big[1]) > 0.6); // shall be 0.63
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

    // REQUIRE (ms.getLastValue() == 0);

    SIMUL.endSingleRun();

    std::cout << "--------------" << std::endl;
    std::cout << "Simulation finished" << std::endl;
    for (AbsRTTask *t : tasks)
        delete t;
    for (CBServerCallingEMRTKernel *cbs : ets)
        delete cbs;
    delete k;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp 26, Temporarily migrate on task end") {
    /**
        24th example repeated, but this time when task is killed, processor goes
        idles since there is no ready task and no task can be migrated
       definitevely via the migration test. However, it is possible to
       temporarily migrate task 49 into BIG1. When t=60 (period task 38)
       arrives, the task 49 goes back to BIG0 and continues there for 4 ticks;
       in the meanwhile, task 38 runs on BIG1.

        Only temporary migrations should be used here.
      */
    init_sequence = 26;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req(false, true, false, true, true, true, true, true);
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();
    vector<AbsRTTask *> tasks;

    string names[] = {"B0_running", "B0_temp_migr", "B1_killed"};
    int wcets[] = {25, 75, 38};
    int deads[] = {100, 100, 60};
    // MissCount ms("miss");
    vector<CBServerCallingEMRTKernel *> ets;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name = "T" + to_string(TEST_NO) + names[j];
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(deads[j], deads[j], 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        std::cout << instr << std::endl;
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
        tasks.push_back(t);
        // ttrace.attachToTask(*t);
        // ms.attachToTask(t);
        // pstrace.attachToTask(*t);
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
    std::cout << "u active big 1 " << k->getUtilization_active(cpus_big[1])
              << std::endl;
    std::cout << "idle evt " << ets[2]->getIdleEvent() << std::endl;
    std::cout << "server status " << ets[2]->getStatusString() << std::endl;
    REQUIRE(k->getUtilization_active(cpus_big[1]) > 0.6); // shall be 0.63
    REQUIRE((double) ets[2]->getIdleEvent() >= 20);
    REQUIRE(ets[2]->getStatus() == ServerStatus::RELEASING);

    SIMUL.run_to(
        16); // because of temporary migrations, task 49 is moved to core BIG1
    k->printState(true);
    REQUIRE(k->getRunningTask(cpus_big[1]) == ets[1]);
    REQUIRE(k->getRunningTask(cpus_big[0]) == ets[0]);
    REQUIRE(k->isTaskTemporarilyMigrated(ets[1], cpus_big[1]));

    SIMUL.run_to(26); // end vtime, nothing happens on BIG1 because task 49
                      // running; BIG0 gets idle
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

    // REQUIRE (ms.getLastValue() == 0);

    SIMUL.endSingleRun();

    std::cout << "--------------" << std::endl;
    std::cout << "Simulation finished" << std::endl;
    for (AbsRTTask *t : tasks)
        delete t;
    for (CBServerCallingEMRTKernel *cbs : ets)
        delete cbs;
    delete k;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("exp 27, migrations have precedence on temporary ones") {
    /**
        26th example repeated, but this time I want to be sure
        that, when virtual time ends, if a migration is possible,
        there is temporary migration.
      */
    init_sequence = 27;
    std::cout << "Begin of experiment " << init_sequence << std::endl;
    Requisite req(false, true, false, true, true, false, true, false);
    if (!checkRequisites(req))
        return;

    EnergyMRTKernel *kern;
    init_suite(&kern);
    REQUIRE(kern != NULL);
    vector<CPU_BL *> cpus_little = kern->getIslandLittle()->getProcessors();
    vector<CPU_BL *> cpus_big = kern->getIslandBig()->getProcessors();
    vector<AbsRTTask *> tasks;

    string names[] = {"B0_running", "B0_migr", "B1_killed"};
    int wcets[] = {35, 36, 25};
    int deads[] = {100, 100, 60};
    // MissCount ms("miss");
    vector<CBServerCallingEMRTKernel *> ets;
    for (int j = 0; j < sizeof(wcets) / sizeof(wcets[0]); j++) {
        task_name = "T" + to_string(TEST_NO) + names[j];
        std::cout << "Creating task: " << task_name;
        PeriodicTask *t = new PeriodicTask(deads[j], deads[j], 0, task_name);
        char instr[60] = "";
        sprintf(instr, "fixed(%d, %s);", wcets[j], workload.c_str());
        std::cout << instr << std::endl;
        t->insertCode(instr);
        ets.push_back(kern->addTaskAndEnvelope(t, ""));
        // ttrace.attachToTask(*t);
        tasks.push_back(t);
        // ms.attachToTask(t);
        // pstrace.attachToTask(*t);
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
    std::cout << "u active big 1 " << k->getUtilization_active(cpus_big[1])
              << std::endl;
    std::cout << "idle evt " << ets[2]->getIdleEvent() << std::endl;
    std::cout << "server status " << ets[2]->getStatusString() << std::endl;
    REQUIRE(k->getUtilization_active(cpus_big[1]) > 0.4); // shall be 0.41
    REQUIRE((double) ets[2]->getIdleEvent() >= 20);
    REQUIRE(ets[2]->getStatus() == ServerStatus::RELEASING);

    SIMUL.run_to(26); // end vtime, migration of t36 to BIG1
    k->printState(true);
    REQUIRE(k->getUtilization_active(cpus_big[1]) == 0.0);
    REQUIRE(k->getRunningTask(cpus_big[0]) == ets[0]);
    REQUIRE(k->getReadyTasks(cpus_big[0]).empty());
    REQUIRE(k->getRunningTask(cpus_big[1]) == ets[1]);
    REQUIRE(false == k->isTaskTemporarilyMigrated(ets[1], cpus_big[1]));

    SIMUL.run_to(36); // end of t35; t36 remains on BIG1 (thus migration is not
                      // temporary)
    k->printState(true);
    REQUIRE(k->getRunningTask(cpus_big[0]) == NULL);
    REQUIRE(k->getReadyTasks(cpus_big[0]).empty());
    REQUIRE(k->getRunningTask(cpus_big[1]) == ets[1]);

    // REQUIRE (ms.getLastValue() == 0);

    SIMUL.endSingleRun();

    std::cout << "--------------" << std::endl;
    std::cout << "Simulation finished" << std::endl;
    for (AbsRTTask *t : tasks)
        delete t;
    for (CBServerCallingEMRTKernel *cbs : ets)
        delete cbs;
    delete k;
    std::cout << "End of Experiment #" << init_sequence << std::endl
              << std::endl;
    performedTests[init_sequence] = req;
}

TEST_CASE("None") {
    for (const auto &elem : performedTests) {
        std::cout << "Performed experiment #" << elem.first << " with "
                  << elem.second.toString() << std::endl;
    }
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

    /* ------------------------- Creating CPU_BLs -------------------------*/
    for (unsigned int i = 0; i < 4; ++i) {
        /* Create 4 LITTLE CPU_BLs */
        string cpu_name = "LITTLE_" + to_string(i);

        std::cout << "Creating CPU_BL: " << cpu_name << std::endl;

        std::cout << "f is " << F_little[F_little.size() - 1] << " max_freq "
                  << max_frequency << std::endl;

        CPUModelBP *pm =
            new CPUModelBP(V_little[V_little.size() - 1],
                           F_little[F_little.size() - 1], max_frequency);
        {
            CPUModelBP::PowerModelBPParams idle_pp = {0.00134845, 1.76307e-5,
                                                      124.535, 1.00399e-10};
            CPUModelBPParams::SpeedModelParams idle_cp = {1, 0, 0, 0};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams("idle", idle_pp,
                                                              idle_cp);

            CPUModelBP::PowerModelBPParams bzip2_pp = {0.00775587, 33.376,
                                                       1.54585, 9.53439e-10};
            CPUModelBPParams::SpeedModelParams bzip2_cp = {
                0.0256054, 2.9809e+6, 0.602631, 8.13712e+9};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams("bzip2", bzip2_pp,
                                                              bzip2_cp);

            CPUModelBP::PowerModelBPParams hash_pp = {0.00624673, 176.315,
                                                      1.72836, 1.77362e-10};
            CPUModelBPParams::SpeedModelParams hash_cp = {
                0.00645628, 3.37134e+6, 7.83177, 93459};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams("hash", hash_pp,
                                                              hash_cp);

            CPUModelBP::PowerModelBPParams encrypt_pp = {0.00676544, 26.2243,
                                                         5.6071, 5.34216e-10};
            CPUModelBPParams::SpeedModelParams encrypt_cp = {
                6.11496e-78, 3.32246e+6, 6.5652, 115759};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams(
                "encrypt", encrypt_pp, encrypt_cp);

            CPUModelBP::PowerModelBPParams decrypt_pp = {0.00629664, 87.1519,
                                                         2.93286, 2.80871e-10};
            CPUModelBPParams::SpeedModelParams decrypt_cp = {
                5.0154e-68, 3.31791e+6, 7.154, 112163};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams(
                "decrypt", decrypt_pp, decrypt_cp);

            CPUModelBP::PowerModelBPParams cachekiller_pp = {
                0.0126737, 67.9915, 1.63949, 3.66185e-10};
            CPUModelBPParams::SpeedModelParams cachekiller_cp = {
                1.20262, 352597, 2.03511, 169523};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams(
                "cachekiller", cachekiller_pp, cachekiller_cp);
        }

        std::cout << "creating cpu" << std::endl;
        CPU_BL *c = new CPU_BL(cpu_name, "idle", pm);
        c->setIndex(i);
        pm->setCPU(c);
        pm->setFrequencyMax(max_frequency);
        // TracePowerConsumption *power_trace = new TracePowerConsumption(c, 1,
        // "power_" + cpu_name + ".txt"); ptrace.push_back(power_trace);

        cpus_little.push_back(c);
    }

    for (unsigned int i = 0; i < 4; ++i) {
        /* Create 4 big CPU_BLs */

        string cpu_name = "BIG_" + to_string(i);

        std::cout << "Creating CPU_BL: " << cpu_name << std::endl;

        CPUModelBP *pm = new CPUModelBP(V_big[V_big.size() - 1],
                                        F_big[F_big.size() - 1], max_frequency);
        {
            CPUModelBP::PowerModelBPParams idle_pp = {0.0162881, 0.00100737,
                                                      55.8491, 1.00494e-9};
            CPUModelBPParams::SpeedModelParams idle_cp = {1, 0, 0, 0};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams("idle", idle_pp,
                                                              idle_cp);

            CPUModelBP::PowerModelBPParams bzip2_pp = {0.0407739, 12.022,
                                                       3.33367, 7.4577e-9};
            CPUModelBPParams::SpeedModelParams bzip2_cp = {0.17833, 1.63265e+6,
                                                           1.62033, 118803};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams("bzip2", bzip2_pp,
                                                              bzip2_cp);

            CPUModelBP::PowerModelBPParams hash_pp = {0.0388215, 16.3205,
                                                      4.3418, 5.07039e-9};
            CPUModelBPParams::SpeedModelParams hash_cp = {0.017478, 1.93925e+6,
                                                          4.22469, 83048.3};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams("hash", hash_pp,
                                                              hash_cp);

            CPUModelBP::PowerModelBPParams encrypt_pp = {0.0348728, 8.14399,
                                                         5.64344, 7.69915e-9};
            CPUModelBPParams::SpeedModelParams encrypt_cp = {
                8.39417e-34, 1.99222e+6, 3.33002, 96949.4};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams(
                "encrypt", encrypt_pp, encrypt_cp);

            CPUModelBP::PowerModelBPParams decrypt_pp = {0.0320508, 25.8727,
                                                         3.27135, 4.11773e-9};
            CPUModelBPParams::SpeedModelParams decrypt_cp = {
                9.49471e-35, 1.98761e+6, 2.65652, 109497};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams(
                "decrypt", decrypt_pp, decrypt_cp);

            CPUModelBP::PowerModelBPParams cachekiller_pp = {
                0.086908, 9.17989, 2.5828, 7.64943e-9};
            CPUModelBPParams::SpeedModelParams cachekiller_cp = {
                0.825212, 235044, 786.368, 25622.1};
            dynamic_cast<CPUModelBP *>(pm)->setWorkloadParams(
                "cachekiller", cachekiller_pp, cachekiller_cp);
        }

        CPU_BL *c = new CPU_BL(cpu_name, "idle", pm);
        c->setIndex(i);
        pm->setCPU(c);
        pm->setFrequencyMax(max_frequency);
        // TracePowerConsumption *power_trace = new TracePowerConsumption(c, 1,
        // "power_" + cpu_name + ".txt"); ptrace.push_back(power_trace);

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

    std::cout << "end init_suite of Experiment #" << init_suite << std::endl;
    return 0;
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

bool checkRequisites(Requisite reqs) {
    std::cout << "Experiments with " << reqs.toString() << std::endl;
    reqs.satisfied = false;

    if (FORCE_REQUISITES) {
        fixDependencies(reqs);

        std::cout << "Changing EMRTK policies settings as needed:" << std::endl;

        // edit here when you add a new policy in EnergyMRTKernel
        std::cout << "\tSetting Leave Little 3 free policy to "
                  << reqs.EMRTK_leave_little3 << std::endl;
        EnergyMRTKernel::EMRTK_LEAVE_LITTLE3_ENABLED = reqs.EMRTK_leave_little3;

        std::cout << "\tSetting migration policies to " << reqs.EMRTK_migrate
                  << std::endl;
        EnergyMRTKernel::EMRTK_MIGRATE_ENABLED = reqs.EMRTK_migrate;

        std::cout << "\tSetting CBS Server yielding policy to "
                  << reqs.EMRTK_cbs_yield << std::endl;
        EnergyMRTKernel::EMRTK_CBS_YIELD_ENABLED = reqs.EMRTK_cbs_yield;

        std::cout << "\tSetting Temporarily Migrate on vtime end policy to "
                  << reqs.EMRTK_temporarily_migrate_vtime << std::endl;
        EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_VTIME =
            reqs.EMRTK_temporarily_migrate_vtime;

        std::cout << "\tSetting Temporarily Migrate on end policy to "
                  << reqs.EMRTK_temporarily_migrate_end << std::endl;
        EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_END =
            reqs.EMRTK_temporarily_migrate_end;

        std::cout << "\tSetting CBS enveloping periodic tasks to "
                  << reqs.EMRTK_cbs_enveloping_per_task_enabled << std::endl;
        EnergyMRTKernel::EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED =
            reqs.EMRTK_cbs_enveloping_per_task_enabled;

        std::cout << "\tSetting CBS migration after tasks end to "
                  << reqs.EMRTK_cbs_migrate_after_end << std::endl;
        EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END =
            reqs.EMRTK_cbs_migrate_after_end;

        std::cout << "\tSetting CBS migration after virtualtime end to "
                  << reqs.EMRTK_cbs_enveloping_migrate_after_vtime_end
                  << std::endl;
        EnergyMRTKernel::EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END =
            reqs.EMRTK_cbs_enveloping_migrate_after_vtime_end;

        reqs.satisfied = true;
        return true;
    }

    // edit here when you add a new policy in EnergyMRTKernel
    if (reqs.EMRTK_leave_little3 !=
        EnergyMRTKernel::EMRTK_LEAVE_LITTLE3_ENABLED) {
        std::cout << "Test requires EMRTK_LEAVE_LITTLE3_ENABLED = 1, but it's "
                     "disabled: "
                  << EnergyMRTKernel::EMRTK_LEAVE_LITTLE3_ENABLED << ". Skip"
                  << std::endl;
        return false;
    }
    if (reqs.EMRTK_migrate != EnergyMRTKernel::EMRTK_MIGRATE_ENABLED) {
        std::cout
            << "Test requires EMRTK_MIGRATE_ENABLED = 1, but it's disabled: "
            << EnergyMRTKernel::EMRTK_MIGRATE_ENABLED << ". Skip" << std::endl;
        return false;
    }
    if (reqs.EMRTK_cbs_yield != EnergyMRTKernel::EMRTK_CBS_YIELD_ENABLED) {
        std::cout
            << "Test requires EMRTK_CBS_YIELD_ENABLED = 1, but it's disabled: "
            << EnergyMRTKernel::EMRTK_CBS_YIELD_ENABLED << ". Skip"
            << std::endl;
        return false;
    }

    if (reqs.EMRTK_temporarily_migrate_vtime !=
        EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_VTIME) {
        std::cout << "Test requires EMRTK_TEMPORARILY_MIGRATE_VTIME = 1, but "
                     "it's disabled: "
                  << EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_VTIME
                  << ". Skip" << std::endl;
        return false;
    }

    if (reqs.EMRTK_temporarily_migrate_end !=
        EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_END) {
        std::cout << "Test requires EMRTK_TEMPORARILY_MIGRATE_END = 1, but "
                     "it's disabled: "
                  << EnergyMRTKernel::EMRTK_TEMPORARILY_MIGRATE_END << ". Skip"
                  << std::endl;
        return false;
    }

    if (reqs.EMRTK_cbs_enveloping_per_task_enabled !=
        EnergyMRTKernel::EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED) {
        std::cout << "Test requires EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED = 1, "
                     "but it's disabled: "
                  << EnergyMRTKernel::EMRTK_CBS_ENVELOPING_PER_TASK_ENABLED
                  << ". Skip" << std::endl;
        return false;
    }
    if (reqs.EMRTK_cbs_enveloping_migrate_after_vtime_end !=
        EnergyMRTKernel::EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END) {
        std::cout
            << "Test requires EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END = "
               "1, but it's disabled: "
            << EnergyMRTKernel::EMRTK_CBS_ENVELOPING_MIGRATE_AFTER_VTIME_END
            << ". Skip" << std::endl;
        return false;
    }
    if (reqs.EMRTK_cbs_migrate_after_end !=
        EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END) {
        std::cout << "Test requires EMRTK_CBS_MIGRATE_AFTER_END = 1, but it's "
                     "disabled: "
                  << EnergyMRTKernel::EMRTK_CBS_MIGRATE_AFTER_END << ". Skip"
                  << std::endl;
        return false;
    }
    reqs.satisfied = true;
    return true;
}

// edit here when you add a new policy in EnergyMRTKernel
bool fixDependencies(Requisite reqs, bool abortOnFix) {
    std::cout << __func__ << "()" << std::endl;

    // CBS enveloping periodic tasks
    if (reqs.EMRTK_cbs_enveloping_migrate_after_vtime_end &&
        !reqs.EMRTK_cbs_enveloping_per_task_enabled)
        if (abortOnFix) {
            std::cout << "\tabort on line " << __LINE__ << std::endl;
            exit(0);
        } else
            reqs.EMRTK_cbs_enveloping_per_task_enabled = 1;

    if (reqs.EMRTK_cbs_migrate_after_end &&
        !reqs.EMRTK_cbs_enveloping_per_task_enabled)
        if (abortOnFix) {
            std::cout << "\tabort on line " << __LINE__ << std::endl;
            exit(0);
        } else
            reqs.EMRTK_cbs_enveloping_per_task_enabled = 1;

    // migrations
    if (reqs.EMRTK_cbs_enveloping_migrate_after_vtime_end &&
        !reqs.EMRTK_migrate)
        if (abortOnFix) {
            std::cout << "\tabort on line " << __LINE__ << std::endl;
            exit(0);
        } else
            reqs.EMRTK_migrate = 1;

    if (reqs.EMRTK_cbs_migrate_after_end && !reqs.EMRTK_migrate)
        if (abortOnFix) {
            std::cout << "\tabort on line " << __LINE__ << std::endl;
            exit(0);
        } else
            reqs.EMRTK_migrate = 1;

    if (reqs.EMRTK_temporarily_migrate_vtime && !reqs.EMRTK_migrate)
        if (abortOnFix) {
            std::cout << "\tabort on line " << __LINE__ << std::endl;
            exit(0);
        } else
            reqs.EMRTK_migrate = 1;
}
