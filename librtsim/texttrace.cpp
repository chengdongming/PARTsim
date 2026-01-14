#include <rtsim/texttrace.hpp>

namespace RTSim {
    using namespace MetaSim;

    using CPU_BL = CPU;

    TextTrace::TextTrace(const string &name) {
        fd.open(name.c_str());
    }

    TextTrace::~TextTrace() {
        fd.close();
    }

    void TextTrace::probe(ArrEvt &e) {
        Task *tt = e.getTask();
        fd << "[Time:" << SIMUL.getTime() << "]\t";
        fd << tt->getName() << " arrived at " << tt->getArrival() << std::endl;
    }

    void TextTrace::probe(EndEvt &e) {
        Task *tt = e.getTask();
        fd << "[Time:" << SIMUL.getTime() << "]\t";
        fd << tt->getName() << " ended, its arrival was " << tt->getArrival()
           << ", its period was " << tt->getPeriod() << ", RespTime/Period is "
           << double(SIMUL.getTime() - tt->getArrival()) /
                  (double) tt->getPeriod()
           << std::endl;
    }

    void TextTrace::probe(SchedEvt &e) {
        Task *tt = e.getTask();
        fd << "[Time:" << SIMUL.getTime() << "]\t";
        /* todo sostituire con questa versione
        fd << tt->getName()<<" scheduled its arrival was "
            << tt->getArrival() << std::endl;
            */
        CPU *c = tt->getKernel()->getProcessor(tt);
        if (c != NULL) {
            string frequency_str = "";

            if (dynamic_cast<CPU_BL *>(c))
                frequency_str =
                    "freq " +
                    std::to_string(dynamic_cast<CPU_BL *>(c)->getFrequency());

            fd << tt->getName() << " scheduled on CPU " << c->getName() << " "
               << "workload " << c->getWorkload() << " "
               << "speed " << c->getSpeed() << " " << frequency_str
               << " abs WCET " << tt->getWCET() << " its arrival was "
               << tt->getArrival() << std::endl;
        }
    }

    void TextTrace::probe(DeschedEvt &e) {
        Task *tt = e.getTask();
        fd << "[Time:" << SIMUL.getTime() << "]\t";
        fd << tt->getName() << " descheduled its arrival was "
           << tt->getArrival() << std::endl;
    }

    void TextTrace::probe(DeadEvt &e) {
        Task *tt = e.getTask();

        fd << "[Time:" << SIMUL.getTime() << "]\t";
        fd << tt->getName() << " missed its arrival was " << tt->getArrival()
           << std::endl;
    }

    void TextTrace::probe(KillEvt &e) {
        Task *tt = e.getTask();

        fd << "[Time:" << SIMUL.getTime() << "]\t";
        fd << tt->getName() << " killed its arrival was " << tt->getArrival()
           << std::endl;
    }

    void TextTrace::probe(EndInstrEvt &e) {
        Instr *ii = e.getInstruction();
        Task *tt = ii->getTask();
        fd << "[Time:" << SIMUL.getTime() << "]\t";
        fd << ii->toString() << " ended for Task " << tt->getName()
           << std::endl;
    }

    void TextTrace::attachToTask(AbsRTTask &t) {
        /*new Particle<ArrEvt, TextTrace>(&t->arrEvt, this);
        new Particle<EndEvt, TextTrace>(&t->endEvt, this);
        new Particle<SchedEvt, TextTrace>(&t->schedEvt, this);
        new Particle<DeschedEvt, TextTrace>(&t->deschedEvt, this);
        new Particle<DeadEvt, TextTrace>(&t->deadEvt, this);*/
        Task &tt = dynamic_cast<Task &>(t);
        attach_stat(*this, tt.arrEvt);
        attach_stat(*this, tt.endEvt);
        attach_stat(*this, tt.schedEvt);
        attach_stat(*this, tt.deschedEvt);
        attach_stat(*this, tt.deadEvt);
        attach_stat(*this, tt.killEvt);
    }

    VirtualTrace::VirtualTrace(map<string, int> *r) {
        results = r;
    }

    VirtualTrace::~VirtualTrace() {}

    void VirtualTrace::probe(EndEvt &e) {
        Task *tt = e.getTask();
        auto tmp_wcrt = SIMUL.getTime() - tt->getArrival();

        if ((*results)[tt->getName()] < tmp_wcrt) {
            (*results)[tt->getName()] = tmp_wcrt;
        }
    }

    void VirtualTrace::attachToTask(Task &t) {
        // new Particle<EndEvt, VirtualTrace>(&t->endEvt, this);
        attach_stat(*this, t.endEvt);
    }

}; // namespace RTSim
