#include <rtsim/json_trace.hpp>

namespace RTSim {

    using namespace MetaSim;

    JSONTrace::JSONTrace(const string &name) {
        fd.open(name.c_str());
        fd << "{" << std::endl;
        fd << "    \"events\" : \[" << std::endl;
        first_event = true;
        max_time = MetaSim::Tick(-1); // 默认不限制
    }

    JSONTrace::JSONTrace(const string &name, MetaSim::Tick max) {
        fd.open(name.c_str());
        fd << "{" << std::endl;
        fd << "    \"events\" : \[" << std::endl;
        first_event = true;
        max_time = max;
    }

    JSONTrace::~JSONTrace() {
        fd << "] }" << std::endl;
        fd.close();
    }

    void JSONTrace::writeTaskEvent(const Task &tt,
                                   const std::string &evt_name) {
        // 检查当前时间是否超过最大时间
        if (max_time >= 0 && SIMUL.getTime() >= max_time) {
            return; // 超过最大时间，不记录此事件
        }

        if (!first_event)
            fd << "," << std::endl;
        else
            first_event = false;
        fd << "{ ";
        fd << "\"time\" : \"" << SIMUL.getTime() << "\", ";
        fd << "\"event_type\" : \"" << evt_name << "\", ";
        fd << "\"task_name\" : \"" << tt.getName() << "\",";
        fd << "\"arrival_time\" : \"" << tt.getArrival() << "\"}";
    }

    void JSONTrace::probe(ArrEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "arrival");
    }

    void JSONTrace::probe(EndEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "end_instance");
    }

    void JSONTrace::probe(SchedEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "scheduled");
    }

    void JSONTrace::probe(DeschedEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "descheduled");
    }

    void JSONTrace::probe(DeadEvt &e) {
        Task &tt = *(e.getTask());

        // 修复假阳性deadline miss：只有当前时间 >= 绝对截止时间才记录为deadline miss
        // 这是RTSim框架的一个已知问题，在Buffered模式下会调用deadEvt.process()
        // 但这并不一定意味着真的有deadline miss
        MetaSim::Tick current_time = SIMUL.getTime();
        MetaSim::Tick arrival_time = tt.getArrival();
        MetaSim::Tick relative_deadline = tt.getDeadline();
        MetaSim::Tick absolute_deadline = arrival_time + relative_deadline;

        // 只有当前时间真的超过截止时间时才记录
        if (current_time >= absolute_deadline) {
            writeTaskEvent(tt, "dline_miss");
        }
        // 否则忽略这个假阳性事件
    }

    void JSONTrace::probe(KillEvt &e) {
        Task &tt = *(e.getTask());
        writeTaskEvent(tt, "kill");
    }

    void JSONTrace::attachToTask(AbsRTTask &t) {
        // new Particle<ArrEvt, JSONTrace>(&t->arrEvt, this);
        // new Particle<EndEvt, JSONTrace>(&t->endEvt, this);
        // new Particle<SchedEvt, JSONTrace>(&t->schedEvt, this);
        // new Particle<DeschedEvt, JSONTrace>(&t->deschedEvt, this);
        // new Particle<DeadEvt, JSONTrace>(&t->deadEvt, this);

        Task &tt = dynamic_cast<Task &>(t);
        attach_stat(*this, tt.arrEvt);
        attach_stat(*this, tt.endEvt);
        attach_stat(*this, tt.schedEvt);
        attach_stat(*this, tt.deschedEvt);
        attach_stat(*this, tt.deadEvt);
        attach_stat(*this, tt.killEvt);
    }
} // namespace RTSim
