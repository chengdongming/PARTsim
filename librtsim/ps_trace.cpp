#include <rtsim/ps_trace.hpp>

namespace RTSim {

    using namespace MetaSim;

    PSTrace::PSTrace(const string &name) {
        fd.open(name.c_str());
        first_event = true;
    }

    PSTrace::~PSTrace() {
        fd.close();
    }

    void PSTrace::writeTaskEvent(const Task &tt, const std::string &evt_name,
                                 TaskEvt *evt) {
        fd << SIMUL.getTime() << "\t";
        fd << tt.getName() << "\t";
        fd << (dynamic_cast<ArrEvt *>(evt) != NULL ? 0 : evt->getCPU()) << "\t";
        fd << evt_name << "\t";
        fd << std::endl;
    }

    void PSTrace::probe(ArrEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "CREATION\tI", &e);
    }

    void PSTrace::probe(EndEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "RUNNING\tE", &e);
        // writeTaskEvent(tt, "DEAD\tI");
    }

    void PSTrace::probe(SchedEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "RUNNING\tS", &e);
    }

    void PSTrace::probe(DeschedEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "RUNNING\tE", &e);
    }

    void PSTrace::probe(DeadEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "MISS\t\tI", &e);
    }

    void PSTrace::attachToTask(Task &t) {
        // assert(t != NULL);

        /*new Particle<ArrEvt, PSTrace>(t->arrEvt, *this);
        new Particle<EndEvt, PSTrace>(t->endEvt, *this);
        new Particle<SchedEvt, PSTrace>(t->schedEvt, *this);
        new Particle<DeschedEvt, PSTrace>(t->deschedEvt, *this);
        new Particle<DeadEvt, PSTrace>(t->deadEvt, *this);*/

        attach_stat(*this, t.arrEvt);
        attach_stat(*this, t.endEvt);
        attach_stat(*this, t.schedEvt);
        attach_stat(*this, t.deschedEvt);
        attach_stat(*this, t.deadEvt);
    }
} // namespace RTSim
