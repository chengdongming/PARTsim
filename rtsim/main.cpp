#include "args.hpp"

/*
 * ╔═══════════════════════════════════════════════════════╗
 * ║                         Main                          ║
 * ╚═══════════════════════════════════════════════════════╝
 */

// LibMetasim
#include <metasim/simul.hpp>

// LibRTSim
#include <rtsim/cbserver.hpp>
#include <rtsim/json_trace.hpp>
#include <rtsim/resource/fcfsresmanager.hpp>
#include <rtsim/system.hpp>
#include <rtsim/texttrace.hpp>
#include <rtsim/waitinstr.hpp>
#include <rtsim/exeinstr.hpp>
#include <rtsim/task.hpp>

using Task_ptr = std::shared_ptr<RTSim::Task>;
using Server_ptr = std::shared_ptr<RTSim::Server>;

class ServerTask {
public:
    ServerTask(Task_ptr task, Server_ptr server) : task(task), server(server) {
        if (server)
            server->addTask(*task);
    }

    Server_ptr getServer() {
        return server;
    }

    RTSim::AbsRTTask &getTask() {
        return *task;
    }

private:
    Task_ptr task;
    Server_ptr server;
};

struct placement_t {
    ServerTask task;
    int initial_cpu;
    std::string params;  // ⭐ 修复：添加params字段

    placement_t(Task_ptr task, Server_ptr server, int initial_cpu, const std::string &p = "") :
        task(task, server),
        initial_cpu(initial_cpu),
        params(p) {}

    placement_t(ServerTask task, int initial_cpu, const std::string &p = "") :
        task(task),
        initial_cpu(initial_cpu),
        params(p) {}
};

using TaskSet = std::vector<placement_t>;

TaskSet read_taskset(const std::string &tset_file) {
    yaml::Object_ptr tset_spec = yaml::parse(tset_file);

    TaskSet taskset;

    int i = 0;

    // TODO: assuming periodic task, ask for task type in YML
    for (const auto &task_spec : *(tset_spec->get("taskset"))) {
        auto str_name = task_spec->get("name")->get();
        auto str_iat = task_spec->get("iat")->get();
        auto str_startcpu = task_spec->get("startcpu")->get();
        auto str_deadline = task_spec->get("deadline")->get();
        auto str_cbs_runtime = task_spec->has("cbs_runtime") ? task_spec->get("cbs_runtime")->get() : "0";
        auto str_cbs_period = task_spec->has("cbs_period") ? task_spec->get("cbs_period")->get() : "0";
        auto str_cbs_deadline = task_spec->has("cbs_deadline") ? task_spec->get("cbs_deadline")->get() : "0";
        auto str_ph = task_spec->get("ph")->get();
        auto str_qs = task_spec->get("qs")->get();
        auto code = task_spec->get("code");
        // ⭐ 修复：读取params字段
        auto str_params = task_spec->has("params") ? task_spec->get("params")->get() : "";

        using Tick = MetaSim::Tick;

        int startcpu = str_startcpu.length() ? std::stoi(str_startcpu) : 0;

        auto iat = str_iat.length() ? Tick(std::stol(str_iat)) : Tick(0);
        auto deadline = str_deadline.length() ? Tick(std::stol(str_deadline)) : iat;
        auto cbs_runtime =
            str_cbs_runtime.length() ? Tick(std::stol(str_cbs_runtime)) : Tick(0);
        auto cbs_period = str_cbs_period.length() ? Tick(std::stol(str_cbs_period)) : iat;
        auto cbs_deadline = str_cbs_deadline.length() ? Tick(std::stol(str_cbs_deadline)) : cbs_period;

        // ⭐ 关键修复：从params字符串中解析arrival_offset作为phase
        // params格式: "period=500,wcet=250,arrival_offset=100,workload=bzip2"
        auto ph = str_ph.length() ? Tick(std::stol(str_ph)) : Tick(0);

        // 如果params中包含arrival_offset，则覆盖ph值
        if (!str_params.empty()) {
            size_t offset_pos = str_params.find("arrival_offset=");
            if (offset_pos != std::string::npos) {
                size_t comma_pos = str_params.find(",", offset_pos);
                std::string offset_str = str_params.substr(offset_pos + 15,
                    comma_pos != std::string::npos ? comma_pos - offset_pos - 15 : std::string::npos);
                ph = Tick(std::stol(offset_str));
                std::cout << "⭐ [Main] 从params解析arrival_offset: " << ph << " ms" << std::endl;
            }
        }

        auto qs = str_qs.length() ? std::stol(str_qs) : 100L;

        auto task_ptr = std::make_shared<RTSim::PeriodicTask>(iat, deadline, ph,
                                                              str_name, qs);

        for (const auto &instr : (*code)) {
            auto str_instr = instr->get();
            if (str_instr.length() < 1) {
                continue;
            }

            if (str_instr[str_instr.length() - 1] != ';') {
                str_instr += ";";
            }

            task_ptr->insertCode(str_instr);
        }

        if (cbs_period > 0) {
            // Use Hard CBS
            auto server_ptr = std::make_shared<RTSim::CBServer>(
                cbs_runtime, cbs_period, cbs_deadline, true, "cbserver_" + str_name);

            taskset.emplace_back(task_ptr, server_ptr, startcpu, str_params);
        } else {
          taskset.emplace_back(task_ptr, Server_ptr(), startcpu, str_params);
        }
    }

    return taskset;
}

std::unique_ptr<RTSim::ResManager>
    read_resources(const std::string &tset_file) {
    yaml::Object_ptr tset_spec = yaml::parse(tset_file);

    auto resources = std::make_unique<RTSim::FCFSResManager>();

    for (const auto &res_spec : *(tset_spec->get("resources"))) {
        auto str_name = res_spec->get("name")->get();
        auto str_initial_state = res_spec->get("initial_state")->get();

        // TODO: more general specification in YML for any kind of resource
        int n_initial = str_initial_state == "locked" ? 0 : 1;

        if (!resources->hasResource(str_name)) {
            resources->addResource(str_name, 1, n_initial);
        } else {
            std::cerr << "Cannot specify resource twice: " << str_name
                      << std::endl;
            throw std::exception{};
        }
    }

    return resources;
}

struct Tracer {
    std::unique_ptr<RTSim::TextTrace> ttrace;
    std::unique_ptr<RTSim::JSONTrace> jtrace;

    class UnrecognizedTracerException : public std::exception {
        const std::string _what;

    public:
        UnrecognizedTracerException(const std::string &fname) :
            _what("Unrecognized tracer type: " + fname + "!") {}

        const char *what() const noexcept override {
            return _what.c_str();
        }
    };

    Tracer(const std::string &fname, MetaSim::Tick duration = MetaSim::Tick(-1)) {
        if (string_endswith(fname, ".txt")) {
            ttrace = std::make_unique<RTSim::TextTrace>(fname);
        } else if (string_endswith(fname, ".json")) {
            jtrace = std::make_unique<RTSim::JSONTrace>(fname, duration);
        } else {
            throw UnrecognizedTracerException(fname);
        }
    }

    void attachToTask(RTSim::AbsRTTask &task) {
        if (ttrace)
            ttrace->attachToTask(task);
        if (jtrace)
            jtrace->attachToTask(task);
        RTSim::Task *t = dynamic_cast<RTSim::Task *>(&task);
        const std::vector<std::unique_ptr<RTSim::Instr>> &instrs =
            t->getInstrQueue();
        for (auto i = instrs.begin(); i != instrs.end(); ++i) {
            RTSim::ExecInstr *ei = dynamic_cast<RTSim::ExecInstr *>(i->get());
            if (ei != 0) {
                if (ttrace)
                    ei->setTrace(*ttrace.get());
            }
        }
    }
};

int main(int argc, char *argv[]) {
    auto opts = parse_arguments(argc, argv);

    MetaSim::Simulation &simulation = MetaSim::Simulation::getInstance();

    if (opts["debug"] == "true") {
        simulation.dbg.enable("All");
        simulation.dbg.setStream(opts["debug-out"]);
    }

    // 获取duration参数
    MetaSim::Tick duration = MetaSim::Tick(std::stoi(opts["duration"]));

    std::vector<Tracer> tracers;
    for (auto fname : list_split(opts["trace"])) {
        tracers.emplace_back(fname, duration);
    }

    RTSim::System sys{opts["system"]};

    auto resmanager = read_resources(opts["taskset"]);
    for (auto &kernel : sys.kernels) {
        kernel->setResManager(resmanager.get());
    }

    TaskSet taskset = read_taskset(opts["taskset"]);
    for (auto &[tasksrv, cpu, params] : taskset) {
        if (tasksrv.getServer()) {
            sys.cpus[cpu]->getKernel()->addTask(*tasksrv.getServer(), params);
            for (auto &tracer : tracers) {
                tracer.attachToTask(tasksrv.getTask());
                if (tracer.ttrace)
                    tasksrv.getServer()->setTrace(*tracer.ttrace.get());
                if (tracer.jtrace)
                    tasksrv.getServer()->setTrace(*tracer.jtrace.get());
            }
        } else {
            sys.cpus[cpu]->getKernel()->addTask(tasksrv.getTask(), params);
            for (auto &tracer : tracers)
                tracer.attachToTask(tasksrv.getTask());
        }
    }

    try {
        simulation.run(std::stoi(opts["duration"]));
    } catch (std::exception &e) {
        std::cerr << "EXCEPTION: " << e.what() << std::endl;
        std::cerr << "TERMINATING!" << std::endl;
        return EXIT_FAILURE;
    }

    resmanager->getID();

    return EXIT_SUCCESS;
}
