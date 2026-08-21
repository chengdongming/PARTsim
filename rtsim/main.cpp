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
#include <rtsim/scheduler/config_manager.hpp>
#include <rtsim/system.hpp>
#include <rtsim/texttrace.hpp>
#include <rtsim/waitinstr.hpp>
#include <rtsim/exeinstr.hpp>
#include <rtsim/task.hpp>
#include <rtsim/task_model_validation.hpp>

#include <limits>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <iomanip>
#include <set>
#include <sstream>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

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

namespace {

long readTaskInteger(const yaml::Object_ptr &task_spec,
                     const std::string &task_id,
                     const std::string &field,
                     long default_value,
                     bool required = false) {
    if (!task_spec->has(field)) {
        if (required) {
            throw RTSim::InvalidTaskModel(
                "invalid_task_model: missing_task_field task=" + task_id +
                " field=" + field);
        }
        return default_value;
    }

    const auto node = task_spec->get(field);
    if (!node || node->getType() != yaml::ObjType::Scalar) {
        throw RTSim::InvalidTaskModel(
            "invalid_task_model: invalid_numeric_task_field task=" +
            task_id + " field=" + field + " raw=<non-scalar>");
    }
    try {
        return RTSim::parseStrictTaskInteger(task_id, field, node->get());
    } catch (const RTSim::InvalidTaskModel &error) {
        throw RTSim::InvalidTaskModel(
            std::string(error.what()) +
            " C=<unresolved> D=<unresolved> T=<unresolved>");
    }
}

std::string readRequiredTaskString(const yaml::Object_ptr &task_spec,
                                   const std::string &field) {
    if (!task_spec->has(field)) {
        throw RTSim::InvalidTaskModel(
            "invalid_task_model: missing_task_field task=<unknown> field=" +
            field);
    }
    const auto node = task_spec->get(field);
    if (!node || node->getType() != yaml::ObjType::Scalar ||
        node->get().empty()) {
        throw RTSim::InvalidTaskModel(
            "invalid_task_model: invalid_task_field task=<unknown> field=" +
            field);
    }
    return node->get();
}

} // namespace

TaskSet read_taskset(const std::string &tset_file) {
    yaml::Object_ptr tset_spec = yaml::parse(tset_file);

    TaskSet taskset;
    MetaSim::Tick release_horizon(-1);
    if (tset_spec->has("release_horizon")) {
        const auto node = tset_spec->get("release_horizon");
        if (!node || node->getType() != yaml::ObjType::Scalar) {
            throw RTSim::InvalidTaskModel(
                "invalid_task_model: invalid_numeric_task_field "
                "task=<taskset> field=release_horizon raw=<non-scalar>");
        }
        const long parsed = RTSim::parseStrictTaskInteger(
            "<taskset>", "release_horizon", node->get());
        if (parsed <= 0) {
            throw RTSim::InvalidTaskModel(
                "invalid_task_model: invalid_numeric_task_field "
                "task=<taskset> field=release_horizon raw=" + node->get());
        }
        release_horizon = MetaSim::Tick(parsed);
    }

    int i = 0;

    // TODO: assuming periodic task, ask for task type in YML
    for (const auto &task_spec : *(tset_spec->get("taskset"))) {
        const auto str_name = readRequiredTaskString(task_spec, "name");
        const auto iat_value = readTaskInteger(
            task_spec, str_name, "iat", 0, true);
        const auto deadline_value = readTaskInteger(
            task_spec, str_name, "deadline", iat_value, false);
        const auto startcpu_value = readTaskInteger(
            task_spec, str_name, "startcpu", 0, false);
        const auto cbs_runtime_value = readTaskInteger(
            task_spec, str_name, "cbs_runtime", 0, false);
        const auto cbs_period_value = readTaskInteger(
            task_spec, str_name, "cbs_period", 0, false);
        const auto cbs_deadline_value = readTaskInteger(
            task_spec, str_name, "cbs_deadline", 0, false);
        const auto phase_value = readTaskInteger(
            task_spec, str_name, "ph", 0, false);
        const auto qs_value = readTaskInteger(
            task_spec, str_name, "qs", 100, false);
        if (startcpu_value < 0 ||
            startcpu_value > std::numeric_limits<int>::max() ||
            phase_value < 0 || qs_value <= 0 || cbs_runtime_value < 0 ||
            cbs_period_value < 0 || cbs_deadline_value < 0) {
            std::ostringstream message;
            message << "invalid_task_model: invalid_numeric_task_field"
                    << " task=" << str_name
                    << " field=startcpu/ph/qs/cbs"
                    << " C=<unresolved>"
                    << " D=" << deadline_value
                    << " T=" << iat_value;
            throw RTSim::InvalidTaskModel(message.str());
        }
        if (!task_spec->has("code")) {
            throw RTSim::InvalidTaskModel(
                "invalid_task_model: missing_task_field task=" + str_name +
                " field=code");
        }
        auto code = task_spec->get("code");
        // ⭐ 修复：读取params字段
        auto str_params = task_spec->has("params") ? task_spec->get("params")->get() : "";

        using Tick = MetaSim::Tick;

        const int startcpu = static_cast<int>(startcpu_value);
        const auto iat = Tick(iat_value);
        const auto deadline = Tick(deadline_value);
        const auto cbs_runtime = Tick(cbs_runtime_value);
        const auto cbs_period = Tick(cbs_period_value);
        const auto cbs_deadline = Tick(cbs_deadline_value);

        // ⭐ 关键修复：从params字符串中解析arrival_offset作为phase
        // params格式: "period=500,wcet=250,arrival_offset=100,workload=bzip2"
        auto ph = Tick(phase_value);

        // 如果params中包含arrival_offset，则覆盖ph值
        if (!str_params.empty()) {
            size_t offset_pos = str_params.find("arrival_offset=");
            if (offset_pos != std::string::npos) {
                size_t comma_pos = str_params.find(",", offset_pos);
                std::string offset_str = str_params.substr(offset_pos + 15,
                    comma_pos != std::string::npos ? comma_pos - offset_pos - 15 : std::string::npos);
                ph = Tick(RTSim::parseStrictTaskInteger(
                    str_name, "params.arrival_offset", offset_str));
                if (ph < Tick(0)) {
                    throw RTSim::InvalidTaskModel(
                        "invalid_task_model: negative_task_parameter "
                        "task=" + str_name +
                        " field=params.arrival_offset raw=" + offset_str);
                }
                std::cout << "⭐ [Main] 从params解析arrival_offset: " << ph << " ms" << std::endl;
            }
        }

        const auto qs = qs_value;

        auto task_ptr = std::make_shared<RTSim::PeriodicTask>(iat, deadline, ph,
                                                              str_name, qs);
        if (release_horizon >= Tick(0)) {
            if (ph >= release_horizon) {
                throw RTSim::InvalidTaskModel(
                    "invalid_task_model: phase_not_before_release_horizon "
                    "task=" + str_name);
            }
            task_ptr->setReleaseCutoff(release_horizon);
        }

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

        if (task_spec->has("runtime")) {
            const auto declared_runtime = readTaskInteger(
                task_spec, str_name, "runtime", 0, true);
            if (Tick(declared_runtime) != task_ptr->getWCET()) {
                std::ostringstream message;
                message << "invalid_task_model: runtime_wcet_mismatch"
                        << " task=" << str_name
                        << " raw_runtime=" << declared_runtime
                        << " C=" << task_ptr->getWCET()
                        << " D=" << deadline
                        << " T=" << iat;
                throw RTSim::InvalidTaskModel(message.str());
            }
        }

        // PARTSim's audited scheduling/acceptance model is constrained
        // deadline only. Validate the actual instruction WCET rather than a
        // generator hint so direct YAML input cannot bypass the contract.
        RTSim::validateConstrainedDeadlineTask(
            str_name, task_ptr->getWCET(), deadline, iat);

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

    Tracer(const std::string &fname, MetaSim::Tick duration = MetaSim::Tick(-1),
           const std::string &type_hint = "") {
        const std::string &kind = type_hint.empty() ? fname : type_hint;
        if (string_endswith(kind, ".txt")) {
            ttrace = std::make_unique<RTSim::TextTrace>(fname);
        } else if (string_endswith(kind, ".json")) {
            jtrace = std::make_unique<RTSim::JSONTrace>(fname, duration);
        } else {
            throw UnrecognizedTracerException(kind);
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

struct TraceTarget {
    std::filesystem::path final_path;
    std::filesystem::path partial_path;
    std::filesystem::path lock_path;
    bool lock_owned{false};
    bool lock_metadata_committed{false};
    dev_t lock_device{};
    ino_t lock_inode{};
    std::string lock_metadata;
};

struct TracePublicationContract {
    int expected_schema{RTSim::JSONTrace::TRACE_SCHEMA_VERSION};
    int expected_observability_contract_version{
        RTSim::B4_OBSERVABILITY_SUMMARY_CONTRACT_VERSION};
    double initial_energy_j{0.0};
    double capacity_j{0.0};
    std::size_t processor_count{0};
};

static std::string safePathComponent(const std::string &value) {
    std::string result;
    result.reserve(value.size());
    for (const unsigned char character : value) {
        result.push_back(std::isalnum(character) || character == '-' ||
                                 character == '_'
                             ? static_cast<char>(character)
                             : '_');
    }
    return result.empty() ? "no-run-id" : result;
}

static std::string traceNonce() {
    static std::atomic<unsigned long long> sequence{0};
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return std::to_string(
               std::chrono::duration_cast<std::chrono::nanoseconds>(now)
                   .count()) +
           "-" + std::to_string(sequence.fetch_add(1));
}

static bool isSha256(const std::string &value) {
    return value.size() == 64 && std::all_of(
        value.begin(), value.end(), [](unsigned char character) {
            return (character >= '0' && character <= '9') ||
                   (character >= 'a' && character <= 'f');
        });
}

static std::uint64_t parsePositiveInteger(
    const std::string &value,
    const std::string &option_name) {
    if (value.empty() ||
        !std::all_of(
            value.begin(), value.end(),
            [](unsigned char character) {
                return character >= '0' && character <= '9';
            })) {
        throw std::invalid_argument(
            option_name + " must be a positive integer");
    }
    std::size_t consumed = 0;
    const unsigned long long parsed = std::stoull(value, &consumed, 10);
    if (consumed != value.size() || parsed == 0) {
        throw std::invalid_argument(
            option_name + " must be a positive integer");
    }
    return static_cast<std::uint64_t>(parsed);
}

static void discardPartialTraces(const std::vector<TraceTarget> &targets) {
    for (const auto &target : targets) {
        std::error_code error;
        std::filesystem::remove(target.partial_path, error);
    }
}

class PartialTraceGuard {
    const std::vector<TraceTarget> &_targets;
    bool _active{true};

public:
    explicit PartialTraceGuard(const std::vector<TraceTarget> &targets)
        : _targets(targets) {}
    ~PartialTraceGuard() {
        if (_active)
            discardPartialTraces(_targets);
    }
    void release() { _active = false; }
};

class TraceLockGuard {
    std::vector<TraceTarget> &_targets;

public:
    explicit TraceLockGuard(std::vector<TraceTarget> &targets)
        : _targets(targets) {}

    void acquire(const std::string &run_id, const std::string &command) {
        for (auto &target : _targets) {
            target.lock_path = target.final_path;
            target.lock_path += ".lock";
            const int descriptor = ::open(
                target.lock_path.c_str(), O_CREAT | O_EXCL | O_WRONLY, 0644);
            if (descriptor < 0) {
                const std::string reason = errno == EEXIST
                    ? "trace_target_locked"
                    : "trace_target_lock_error";
                throw std::runtime_error(
                    reason + ": " + target.final_path.string() + ": " +
                    std::strerror(errno));
            }
            struct stat identity {};
            if (::fstat(descriptor, &identity) != 0) {
                const int saved_errno = errno;
                ::close(descriptor);
                ::unlink(target.lock_path.c_str());
                throw std::runtime_error(
                    "trace_target_lock_error: cannot identify lock: " +
                    target.lock_path.string() + ": " +
                    std::strerror(saved_errno));
            }
            target.lock_device = identity.st_dev;
            target.lock_inode = identity.st_ino;
            target.lock_owned = true;
            const std::string nonce = traceNonce();
            std::ostringstream metadata;
            metadata << "pid=" << ::getpid() << "\n"
                     << "run_id=" << run_id << "\n"
                     << "nonce=" << nonce << "\n"
                     << "created_unix_ns="
                     << std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::system_clock::now().time_since_epoch())
                            .count()
                     << "\ncommand_hash=" << std::hash<std::string>{}(command)
                     << "\ntarget=" << target.final_path.string() << "\n";
            const std::string contents = metadata.str();
            target.lock_metadata = contents;
            const ssize_t written = ::write(
                descriptor, contents.data(), contents.size());
            const int sync_result = ::fsync(descriptor);
            ::close(descriptor);
            if (written != static_cast<ssize_t>(contents.size()) ||
                sync_result != 0) {
                throw std::runtime_error(
                    "trace_target_lock_error: cannot persist lock metadata: " +
                    target.lock_path.string());
            }
            target.lock_metadata_committed = true;
        }
    }

    ~TraceLockGuard() {
        for (auto &target : _targets) {
            if (!target.lock_owned)
                continue;
            struct stat current {};
            const bool same_file =
                ::lstat(target.lock_path.c_str(), &current) == 0 &&
                current.st_dev == target.lock_device &&
                current.st_ino == target.lock_inode;
            bool same_owner = same_file;
            if (same_owner && target.lock_metadata_committed) {
                std::ifstream input(target.lock_path, std::ios::binary);
                const std::string observed(
                    (std::istreambuf_iterator<char>(input)),
                    std::istreambuf_iterator<char>());
                same_owner = !input.bad() && observed == target.lock_metadata;
            }
            if (same_owner) {
                std::error_code error;
                std::filesystem::remove(target.lock_path, error);
                if (error) {
                    std::cerr << "TRACE LOCK CLEANUP ERROR: "
                              << target.lock_path << ": " << error.message()
                              << std::endl;
                }
            } else if (std::filesystem::exists(target.lock_path)) {
                std::cerr << "TRACE LOCK OWNER MISMATCH: preserving "
                          << target.lock_path << std::endl;
            }
            target.lock_owned = false;
        }
    }
};

static void fsyncFile(const std::filesystem::path &path) {
    const int descriptor = ::open(path.c_str(), O_RDONLY);
    if (descriptor < 0)
        throw std::runtime_error("cannot open trace for fsync: " + path.string());
    const int result = ::fsync(descriptor);
    ::close(descriptor);
    if (result != 0)
        throw std::runtime_error("cannot fsync trace: " + path.string());
}

static void validateTraceForPublication(const TraceTarget &target,
                                        const std::string &run_id,
                                        const std::string &scheduler,
                                        const std::string &display_name,
                                        const std::string &implementation,
                                        const std::string &expected_horizon,
                                        const std::string &taskset_hash,
                                        const TracePublicationContract &contract) {
    if (!string_endswith(target.final_path.string(), ".json"))
        return;
    // The repository has no C++ JSON dependency.  Use the project's Python
    // runtime to perform real JSON parsing (including duplicate-key and
    // trailing-data rejection) instead of attempting a fragile substring
    // parser in C++.  This mirrors the formal Python trace classifier's v2
    // structural contract before the partial can become public.
    static const char validator[] = R"PY(
import json, math, re, sys
sys.excepthook = lambda _kind, value, _traceback: print(
    'strict_trace_validation_error: ' + str(value), file=sys.stderr)

def fail(message):
    raise ValueError(message)

def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            fail('duplicate JSON key: ' + str(key))
        result[key] = value
    return result

def finite(value, name):
    if isinstance(value, bool):
        fail(name + ' is boolean')
    try:
        number = float(value)
    except (TypeError, ValueError):
        fail(name + ' is not numeric')
    if not math.isfinite(number) or number < 0:
        fail(name + ' is not finite/nonnegative')
    return number

def exact_keys(value, expected, name):
    if not isinstance(value, dict):
        fail(name + ' is not an object')
    if set(value) != set(expected):
        fail(name + ' has missing or unknown fields')

def counter(value, name):
    if type(value) is not int or value < 0:
        fail(name + ' is not a nonnegative integer')
    return value

def scalar(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(name + ' is not a JSON number')
    number = float(value)
    if not math.isfinite(number) or number < 0:
        fail(name + ' is not finite/nonnegative')
    return number

def approximately_equal(lhs, rhs):
    return abs(lhs - rhs) <= 1e-9 * max(1.0, abs(lhs), abs(rhs))

with open(sys.argv[1], encoding='utf-8') as handle:
    data = json.load(handle, object_pairs_hook=pairs)
if not isinstance(data, dict):
    fail('top level is not an object')
if (type(data.get('trace_schema_version')) is not int
        or data['trace_schema_version'] != int(sys.argv[3])):
    fail('invalid trace_schema_version')
if not isinstance(data.get('run_id'), str) or data['run_id'] != sys.argv[2]:
    fail('run_id mismatch')
semantic_hash = data.get('taskset_semantic_hash')
if (not isinstance(semantic_hash, str)
        or re.fullmatch(r'[0-9a-f]{64}', semantic_hash) is None
        or semantic_hash != sys.argv[8]):
    fail('taskset semantic hash mismatch')
if type(data.get('run_count')) is not int or data['run_count'] != 1:
    fail('run_count must equal one')
generation = data.get('target_run_generation')
if type(generation) is not int or generation <= 0:
    fail('invalid target_run_generation')
if type(data.get('run_generation')) is not int or data['run_generation'] != generation:
    fail('top-level generation mismatch')
events = data.get('events')
if not isinstance(events, list):
    fail('events is not an array')
last_time = 0.0
has_arrival = False
misses = set()
for event in events:
    if not isinstance(event, dict):
        fail('event is not an object')
    if type(event.get('run_generation')) is not int or event['run_generation'] != generation:
        fail('event generation mismatch')
    event_time = finite(event.get('time'), 'event time')
    last_time = max(last_time, event_time)
    kind = event.get('event_type')
    if not isinstance(kind, str) or not kind:
        fail('invalid event_type')
    has_arrival = has_arrival or kind == 'arrival'
    if kind == 'dline_miss':
        job = event.get('job_id')
        task = event.get('task_name')
        release = finite(event.get('arrival_time'), 'miss arrival')
        deadline = finite(event.get('deadline'), 'miss deadline')
        remaining = finite(event.get('remaining_execution_ms'), 'miss remaining')
        if not isinstance(job, str) or not job or job in misses:
            fail('invalid/duplicate deadline miss job')
        if not isinstance(task, str) or not task or release > deadline or event_time < deadline or remaining <= 0:
            fail('malformed deadline miss')
        misses.add(job)
for name in ('configured_scheduler', 'scheduler_display_name',
             'scheduler_implementation'):
    if not isinstance(data.get(name), str) or not data[name]:
        fail('invalid scheduler identity: ' + name)
for name, expected_identity in (
        ('configured_scheduler', sys.argv[4]),
        ('scheduler_display_name', sys.argv[5]),
        ('scheduler_implementation', sys.argv[6])):
    if data[name] != expected_identity:
        fail('scheduler identity mismatch: ' + name)
expected = finite(data.get('expected_simulation_horizon_ms'), 'expected horizon')
caller_expected = finite(sys.argv[7], 'caller expected horizon')
observed = finite(data.get('observed_simulation_end_ms'), 'observed horizon')
if type(data.get('simulation_completed')) is not bool or not data['simulation_completed']:
    fail('simulation is not complete')
if data.get('simulation_completion_reason') != 'reached_horizon':
    fail('invalid completion reason')
if (not math.isclose(expected, caller_expected, rel_tol=0.0, abs_tol=1e-9)
        or not math.isclose(expected, observed, rel_tol=0.0, abs_tol=1e-9)
        or observed + 1e-9 < last_time):
    fail('completion horizon mismatch')
if not has_arrival:
    fail('trace has no arrivals')
if int(sys.argv[3]) == 3:
    if type(data.get('observability_summary_contract_version')) is not int:
        fail('invalid observability summary contract version type')
    contract_version = int(sys.argv[12])
    if data['observability_summary_contract_version'] != contract_version:
        fail('invalid observability summary contract version')
    summary_horizon = data.get('observability_summary_horizon_ms')
    if type(summary_horizon) is not int or summary_horizon <= 0:
        fail('invalid observability summary horizon')
    if not approximately_equal(float(summary_horizon), caller_expected):
        fail('observability summary horizon mismatch')

    mechanism_fields = (
        'bypass_opportunity_ticks', 'actual_bypass_ticks',
        'low_priority_bypass_core_ticks', 'hp_dispatch_demand_ticks',
        'hp_energy_blocked_ticks', 'hp_energy_blocked_job_ticks',
        'observed_decision_ticks')
    if contract_version == 2:
        mechanism_fields += (
            'sync_batch_evaluation_ticks', 'sync_batch_reject_ticks',
            'alap_deferral_opportunity_ticks',
            'positive_slack_deferral_ticks',
            'st_charging_opportunity_ticks',
            'st_slack_charging_wait_ticks')
    mechanism = data.get('mechanism_summary')
    exact_keys(mechanism, mechanism_fields, 'mechanism_summary')
    mechanism = {
        name: counter(mechanism[name], 'mechanism_summary.' + name)
        for name in mechanism_fields
    }
    processors = int(sys.argv[11])
    if processors <= 0:
        fail('invalid expected processor count')
    if (mechanism['observed_decision_ticks'] != summary_horizon
            or mechanism['bypass_opportunity_ticks'] > summary_horizon
            or mechanism['actual_bypass_ticks']
                > mechanism['bypass_opportunity_ticks']
            or mechanism['low_priority_bypass_core_ticks']
                > mechanism['actual_bypass_ticks'] * processors
            or mechanism['hp_dispatch_demand_ticks'] > summary_horizon
            or mechanism['hp_energy_blocked_ticks']
                > mechanism['hp_dispatch_demand_ticks']
            or mechanism['hp_energy_blocked_job_ticks']
                > mechanism['hp_energy_blocked_ticks'] * min(processors, 4)):
        fail('mechanism summary bounds are inconsistent')
    if contract_version == 2 and (
            mechanism['sync_batch_evaluation_ticks'] > summary_horizon
            or mechanism['sync_batch_reject_ticks']
                > mechanism['sync_batch_evaluation_ticks']
            or mechanism['alap_deferral_opportunity_ticks'] > summary_horizon
            or mechanism['positive_slack_deferral_ticks']
                > mechanism['alap_deferral_opportunity_ticks']
            or mechanism['st_charging_opportunity_ticks'] > summary_horizon
            or mechanism['st_slack_charging_wait_ticks']
                > mechanism['st_charging_opportunity_ticks']):
        fail('version 2 mechanism bounds are inconsistent')

    energy_scalar_fields = (
        'offered_energy_j', 'credited_energy_j', 'clipped_energy_j',
        'consumed_energy_j', 'battery_min_j', 'battery_max_j',
        'battery_final_j')
    energy_counter_fields = (
        'battery_empty_ticks', 'battery_full_ticks',
        'observed_energy_intervals')
    energy = data.get('energy_summary')
    exact_keys(
        energy, energy_scalar_fields + energy_counter_fields,
        'energy_summary')
    numeric_energy = {
        name: scalar(energy[name], 'energy_summary.' + name)
        for name in energy_scalar_fields
    }
    energy_counts = {
        name: counter(energy[name], 'energy_summary.' + name)
        for name in energy_counter_fields
    }
    initial_energy = scalar(float(sys.argv[9]), 'expected initial energy')
    capacity = scalar(float(sys.argv[10]), 'expected battery capacity')
    if initial_energy > capacity:
        fail('expected initial energy exceeds capacity')
    if not approximately_equal(
            numeric_energy['offered_energy_j'],
            numeric_energy['credited_energy_j']
                + numeric_energy['clipped_energy_j']):
        fail('energy offered reconciliation failed')
    if not approximately_equal(
            initial_energy + numeric_energy['credited_energy_j']
                - numeric_energy['consumed_energy_j'],
            numeric_energy['battery_final_j']):
        fail('energy conservation failed')
    tolerance = 1e-9 * max(
        1.0, initial_energy, capacity,
        *(numeric_energy.values()))
    if (numeric_energy['battery_min_j'] < -tolerance
            or numeric_energy['battery_min_j']
                > numeric_energy['battery_final_j'] + tolerance
            or numeric_energy['battery_final_j']
                > numeric_energy['battery_max_j'] + tolerance
            or numeric_energy['battery_max_j'] > capacity + tolerance
            or energy_counts['observed_energy_intervals'] != summary_horizon
            or energy_counts['battery_empty_ticks']
                > energy_counts['observed_energy_intervals']
            or energy_counts['battery_full_ticks']
                > energy_counts['observed_energy_intervals']):
        fail('energy summary bounds are inconsistent')

    task_fields = (
        'task_name', 'priority_rank', 'is_top4', 'is_bottom6',
        'released_jobs', 'completed_jobs', 'terminated_jobs',
        'deadline_miss_jobs', 'unfinished_at_horizon_jobs',
        'executed_core_ticks', 'completed_response_time_count',
        'completed_response_time_sum_ms',
        'completed_response_time_max_ms')
    if contract_version == 2:
        task_fields = task_fields[:5] + ('adjudicable_jobs',) + task_fields[5:]
    per_task = data.get('per_task_summary')
    if not isinstance(per_task, list) or len(per_task) != 10:
        fail('per_task_summary must contain exactly ten tasks')
    names = set()
    total_executed = 0
    for expected_rank, task in enumerate(per_task):
        exact_keys(task, task_fields, 'per_task_summary item')
        name = task['task_name']
        if not isinstance(name, str) or not name or name in names:
            fail('invalid or duplicate task name')
        names.add(name)
        if type(task['priority_rank']) is not int:
            fail('priority rank is not an integer')
        if task['priority_rank'] != expected_rank:
            fail('priority ranks are not ordered and contiguous')
        if type(task['is_top4']) is not bool or type(task['is_bottom6']) is not bool:
            fail('priority group flags are not booleans')
        if (task['is_top4'] != (expected_rank < 4)
                or task['is_bottom6'] != (expected_rank >= 4)):
            fail('priority group flags disagree with rank')
        counts = {
            field: counter(task[field], name + '.' + field)
            for field in task_fields[4:]
        }
        released = counts['released_jobs']
        adjudicable = counts.get('adjudicable_jobs', released)
        completed = counts['completed_jobs']
        terminated = counts['terminated_jobs']
        unfinished = counts['unfinished_at_horizon_jobs']
        response_count = counts['completed_response_time_count']
        response_sum = counts['completed_response_time_sum_ms']
        response_max = counts['completed_response_time_max_ms']
        if (completed + terminated > released
                or unfinished != released - completed - terminated
                or counts['deadline_miss_jobs'] > adjudicable
                or adjudicable > released
                or (contract_version == 2 and adjudicable < 100)
                or response_count > completed
                or (contract_version == 1 and response_count != completed)
                or (response_count == 0
                    and (response_sum != 0 or response_max != 0))
                or (response_count > 0 and response_max > response_sum)):
            fail('per-task lifecycle closure is inconsistent')
        total_executed += counts['executed_core_ticks']
    if total_executed > processors * summary_horizon:
        fail('total executed core ticks exceed processor capacity')
)PY";

    const pid_t child = ::fork();
    if (child < 0)
        throw std::runtime_error("cannot start strict trace validator");
    if (child == 0) {
        const std::string expected_schema =
            std::to_string(contract.expected_schema);
        std::ostringstream initial_energy;
        initial_energy << std::setprecision(
            std::numeric_limits<double>::max_digits10)
                       << contract.initial_energy_j;
        std::ostringstream capacity;
        capacity << std::setprecision(
            std::numeric_limits<double>::max_digits10)
                 << contract.capacity_j;
        const std::string processors =
            std::to_string(contract.processor_count);
        const std::string observability_contract_version =
            std::to_string(
                contract.expected_observability_contract_version);
        // Prefer the inherited PATH for virtualenv/Conda Python; retain fixed
        // system paths as controlled fallbacks.
        ::execlp("python3", "python3", "-c", validator,
                 target.partial_path.c_str(), run_id.c_str(),
                 expected_schema.c_str(),
                 scheduler.c_str(), display_name.c_str(), implementation.c_str(),
                 expected_horizon.c_str(), taskset_hash.c_str(),
                 initial_energy.str().c_str(), capacity.str().c_str(),
                 processors.c_str(), observability_contract_version.c_str(),
                 static_cast<char *>(nullptr));
        ::execl("/usr/bin/python3", "python3", "-c", validator,
                target.partial_path.c_str(), run_id.c_str(),
                expected_schema.c_str(),
                scheduler.c_str(), display_name.c_str(), implementation.c_str(),
                expected_horizon.c_str(), taskset_hash.c_str(),
                initial_energy.str().c_str(), capacity.str().c_str(),
                processors.c_str(), observability_contract_version.c_str(),
                static_cast<char *>(nullptr));
        ::execl("/usr/local/bin/python3", "python3", "-c", validator,
                target.partial_path.c_str(), run_id.c_str(),
                expected_schema.c_str(),
                scheduler.c_str(), display_name.c_str(), implementation.c_str(),
                expected_horizon.c_str(), taskset_hash.c_str(),
                initial_energy.str().c_str(), capacity.str().c_str(),
                processors.c_str(), observability_contract_version.c_str(),
                static_cast<char *>(nullptr));
        _exit(127);
    }
    int status = 0;
    pid_t waited = -1;
    do {
        waited = ::waitpid(child, &status, 0);
    } while (waited < 0 && errno == EINTR);
    if (waited < 0 || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        throw std::runtime_error(
            "trace failed publication validation: " +
            target.partial_path.string());
    }
}

static std::string readFileBytes(const std::filesystem::path &path) {
    std::ifstream input(path, std::ios::binary);
    if (!input)
        throw std::runtime_error("cannot read trace: " + path.string());
    return std::string((std::istreambuf_iterator<char>(input)),
                       std::istreambuf_iterator<char>());
}

static void publishTraces(const std::vector<TraceTarget> &targets,
                          const std::string &run_id,
                          const std::string &scheduler,
                          const std::string &display_name,
                          const std::string &implementation,
                          const std::string &expected_horizon,
                          const std::string &taskset_hash,
                          const TracePublicationContract &contract) {
    std::vector<bool> idempotent(targets.size(), false);
    std::size_t index = 0;
    for (const auto &target : targets) {
        if (!std::filesystem::is_regular_file(target.partial_path))
            throw std::runtime_error(
                "trace partial was not produced: " + target.partial_path.string());
        validateTraceForPublication(
            target, run_id, scheduler, display_name, implementation,
            expected_horizon, taskset_hash, contract);
        fsyncFile(target.partial_path);
        if (std::filesystem::exists(target.final_path)) {
            TraceTarget existing = target;
            existing.partial_path = target.final_path;
            try {
                validateTraceForPublication(
                    existing, run_id, scheduler, display_name, implementation,
                    expected_horizon, taskset_hash, contract);
            } catch (...) {
                throw std::runtime_error(
                    "trace_target_exists_for_different_run: " +
                    target.final_path.string());
            }
            if (readFileBytes(target.partial_path) !=
                readFileBytes(target.final_path)) {
                throw std::runtime_error(
                    "trace_target_exists_with_different_content: " +
                    target.final_path.string());
            }
            idempotent[index] = true;
        }
        ++index;
    }

    index = 0;
    for (const auto &target : targets) {
        if (idempotent[index++]) {
            std::error_code error;
            std::filesystem::remove(target.partial_path, error);
            continue;
        }
        // link(2) is an atomic no-replace publication within the target
        // directory.  It is the POSIX fallback for renameat2(RENAME_NOREPLACE)
        // and cannot silently overwrite an uncooperative writer's file.
        if (::link(target.partial_path.c_str(), target.final_path.c_str()) != 0) {
            const std::string reason = errno == EEXIST
                ? "trace_target_exists_during_publication"
                : "trace_publication_link_error";
            throw std::runtime_error(
                reason + ": " + target.final_path.string() + ": " +
                std::strerror(errno));
        }
        if (::unlink(target.partial_path.c_str()) != 0)
            throw std::runtime_error(
                "trace publication could not remove partial: " +
                target.partial_path.string());
        const auto parent = target.final_path.parent_path().empty()
                                ? std::filesystem::path(".")
                                : target.final_path.parent_path();
        const int directory = ::open(parent.c_str(), O_RDONLY | O_DIRECTORY);
        if (directory >= 0) {
            ::fsync(directory);
            ::close(directory);
        }
    }
}

int main(int argc, char *argv[]) {
    const char *quiet_stdout = std::getenv("PARTSIM_QUIET_STDOUT");
    if (quiet_stdout != nullptr &&
        std::strcmp(quiet_stdout, "1") == 0 &&
        std::freopen("/dev/null", "w", stdout) == nullptr) {
        const int redirect_error = errno;
        std::fprintf(
            stderr,
            "PRE-FLIGHT ERROR: cannot redirect stdout to /dev/null: %s\n",
            std::strerror(redirect_error));
        return EXIT_FAILURE;
    }

    auto opts = parse_arguments(argc, argv);

    MetaSim::Simulation &simulation = MetaSim::Simulation::getInstance();

    if (opts["debug"] == "true") {
        simulation.dbg.enable("All");
        simulation.dbg.setStream(opts["debug-out"]);
    }

    // 获取duration参数
    MetaSim::Tick duration;
    try {
        duration = MetaSim::Tick(std::stoi(opts["duration"]));
    } catch (const std::exception &error) {
        std::cerr << "PRE-FLIGHT ERROR: invalid duration: "
                  << error.what() << std::endl;
        return EXIT_FAILURE;
    }
    const bool semantic_traces = opts["semantic-traces"] == "true";
    const bool b4_observability_summary =
        opts["b4-observability-summary"] == "true";
    const bool b4_horizon_supplied =
        !opts["b4-summary-horizon"].empty();
    const bool b4_contract_version_supplied =
        !opts["b4-observability-contract-version"].empty();
    std::uint64_t b4_summary_horizon = 0;
    if (b4_observability_summary != b4_horizon_supplied) {
        std::cerr
            << "PRE-FLIGHT ERROR: --b4-observability-summary and "
               "--b4-summary-horizon must be supplied together"
            << std::endl;
        return EXIT_FAILURE;
    }
    if (b4_contract_version_supplied && !b4_observability_summary) {
        std::cerr
            << "PRE-FLIGHT ERROR: --b4-observability-contract-version "
               "requires --b4-observability-summary"
            << std::endl;
        return EXIT_FAILURE;
    }
    int b4_observability_contract_version =
        RTSim::B4_OBSERVABILITY_SUMMARY_CONTRACT_VERSION;
    if (b4_observability_summary) {
        try {
            b4_summary_horizon = parsePositiveInteger(
                opts["b4-summary-horizon"],
                "--b4-summary-horizon");
        } catch (const std::exception &error) {
            std::cerr << "PRE-FLIGHT ERROR: " << error.what()
                      << std::endl;
            return EXIT_FAILURE;
        }
        if (b4_contract_version_supplied) {
            const std::string &value =
                opts["b4-observability-contract-version"];
            if (value == "1") {
                b4_observability_contract_version =
                    RTSim::B4_OBSERVABILITY_SUMMARY_CONTRACT_VERSION;
            } else if (value == "2") {
                b4_observability_contract_version =
                    RTSim::B4_OBSERVABILITY_SUMMARY_CONTRACT_VERSION_V2;
            } else {
                std::cerr
                    << "PRE-FLIGHT ERROR: "
                       "--b4-observability-contract-version must be 1 or 2"
                    << std::endl;
                return EXIT_FAILURE;
            }
        }
        if (duration <= MetaSim::Tick(0) ||
            b4_summary_horizon !=
                static_cast<std::uint64_t>(
                    static_cast<std::int64_t>(duration))) {
            std::cerr
                << "PRE-FLIGHT ERROR: B4 summary horizon must equal duration"
                << std::endl;
            return EXIT_FAILURE;
        }
    }

    TaskSet taskset;
    try {
        taskset = read_taskset(opts["taskset"]);
    } catch (const RTSim::InvalidTaskModel &error) {
        std::cerr << error.what() << std::endl;
        return EXIT_FAILURE;
    }
    MetaSim::Tick release_horizon(-1);
    for (auto &[tasksrv, cpu, params] : taskset) {
        (void) cpu;
        (void) params;
        auto *task = dynamic_cast<RTSim::Task *>(&tasksrv.getTask());
        if (!task) {
            std::cerr << "PRE-FLIGHT ERROR: non-Task task model cannot "
                         "enforce release horizon"
                      << std::endl;
            return EXIT_FAILURE;
        }
        if (!task->hasReleaseCutoff())
            continue;
        if (release_horizon < MetaSim::Tick(0))
            release_horizon = task->getReleaseCutoff();
        else if (release_horizon != task->getReleaseCutoff()) {
            std::cerr << "PRE-FLIGHT ERROR: inconsistent task release "
                         "horizons"
                      << std::endl;
            return EXIT_FAILURE;
        }
    }
    if (release_horizon >= MetaSim::Tick(0) &&
        duration <= release_horizon) {
        std::cerr << "PRE-FLIGHT ERROR: observation horizon must exceed "
                     "release horizon"
                  << std::endl;
        return EXIT_FAILURE;
    }
    RTSim::ConfigManager::getInstance().setExpectedTaskCount(static_cast<int>(taskset.size()));

    // Complete all input/system/factory preflight before opening any trace.
    std::unique_ptr<RTSim::System> sys;
    std::unique_ptr<RTSim::ResManager> resmanager;
    try {
        sys = std::make_unique<RTSim::System>(opts["system"]);
        resmanager = read_resources(opts["taskset"]);
        for (const auto &[tasksrv, cpu, params] : taskset) {
            (void) tasksrv;
            (void) params;
            if (cpu < 0 || static_cast<std::size_t>(cpu) >= sys->cpus.size())
                throw std::out_of_range("invalid initial CPU index");
        }
        for (auto &kernel : sys->kernels)
            kernel->setResManager(resmanager.get());
    } catch (const std::exception &error) {
        std::cerr << "PRE-FLIGHT ERROR: " << error.what() << std::endl;
        return EXIT_FAILURE;
    }
    std::vector<RTSim::ObservabilityTaskMetadata>
        b4_observability_metadata;
    RTSim::EnergyInfoProvider *b4_energy_provider = nullptr;
    if (b4_observability_summary) {
        try {
            std::vector<RTSim::AbsRTTask *> priority_universe;
            priority_universe.reserve(taskset.size());
            for (auto &[tasksrv, cpu, params] : taskset) {
                (void)cpu;
                (void)params;
                priority_universe.push_back(&tasksrv.getTask());
            }
            b4_observability_metadata =
                RTSim::makeB4ObservabilityTaskMetadata(
                    priority_universe);
            if (b4_observability_metadata.size() !=
                RTSim::B4_EXPECTED_TASK_COUNT) {
                throw std::runtime_error(
                    "schema3 requires exactly ten B4 tasks");
            }

            std::set<RTSim::EnergyInfoProvider *> providers;
            for (const auto &kernel : sys->kernels) {
                RTSim::Scheduler *scheduler =
                    kernel->getScheduler();
                auto *provider =
                    dynamic_cast<RTSim::EnergyInfoProvider *>(
                        scheduler);
                if (!provider) {
                    throw std::runtime_error(
                        "schema3 kernel scheduler has no energy provider");
                }
                providers.insert(provider);
            }
            if (providers.size() != 1) {
                throw std::runtime_error(
                    "schema3 requires exactly one energy provider");
            }
            b4_energy_provider = *providers.begin();
        } catch (const std::exception &error) {
            std::cerr << "PRE-FLIGHT ERROR: " << error.what()
                      << std::endl;
            return EXIT_FAILURE;
        }
    }
    std::vector<TraceTarget> trace_targets;
    std::vector<Tracer> tracers;
    for (const auto &fname : list_split(opts["trace"])) {
        TraceTarget target;
        target.final_path = std::filesystem::path(fname);
        trace_targets.push_back(target);
    }
    const std::size_t json_trace_count = static_cast<std::size_t>(
        std::count_if(
            trace_targets.begin(), trace_targets.end(),
            [](const TraceTarget &target) {
                return string_endswith(
                    target.final_path.string(), ".json");
            }));
    const bool json_trace_requested = json_trace_count != 0;
    if (b4_observability_summary && json_trace_count != 1) {
        std::cerr
            << "TRACE INITIALIZATION ERROR: schema3 requires exactly one JSON trace"
            << std::endl;
        return EXIT_FAILURE;
    }
    if (json_trace_requested &&
            !isSha256(opts["taskset-semantic-hash"])) {
        std::cerr << "TRACE INITIALIZATION ERROR: formal JSON trace requires "
                     "--taskset-semantic-hash with 64 lowercase hex digits"
                  << std::endl;
        return EXIT_FAILURE;
    }
    TraceLockGuard trace_lock_guard(trace_targets);
    try {
        std::ostringstream command;
        for (int argument = 0; argument < argc; ++argument)
            command << (argument ? " " : "") << argv[argument];
        trace_lock_guard.acquire(opts["run-id"], command.str());
        for (auto &target : trace_targets) {
            target.partial_path = target.final_path;
            target.partial_path += ".partial." + std::to_string(::getpid()) +
                                   "." + safePathComponent(opts["run-id"]) +
                                   "." + traceNonce();
            tracers.emplace_back(
                target.partial_path.string(), duration,
                target.final_path.string());
        }
    } catch (const std::exception &error) {
        tracers.clear();
        discardPartialTraces(trace_targets);
        std::cerr << "TRACE INITIALIZATION ERROR: " << error.what()
                  << std::endl;
        return EXIT_FAILURE;
    }
    PartialTraceGuard partial_trace_guard(trace_targets);

    RTSim::JSONTrace *b4_json_trace = nullptr;
    // ⭐ 设置JSONTrace的能量提供者
    for (auto &tracer : tracers) {
        if (tracer.jtrace) {
            tracer.jtrace->setSemanticTraceEnabled(semantic_traces);
            if (release_horizon >= MetaSim::Tick(0)) {
                tracer.jtrace->setReleaseObservationWindow(
                    release_horizon, duration);
            }
            tracer.jtrace->setRunId(opts["run-id"]);
            tracer.jtrace->setTasksetSemanticHash(
                opts["taskset-semantic-hash"]);
            if (!sys->scheduler_identities.empty()) {
                const auto &identity = sys->scheduler_identities.front();
                tracer.jtrace->setSchedulerIdentity(
                    identity.configured_scheduler,
                    identity.display_name,
                    identity.implementation_id,
                    identity.rtti_name);
            }
            if (b4_observability_summary) {
                tracer.jtrace->configureB4ObservabilitySchema3(
                    MetaSim::Tick(
                        static_cast<std::int64_t>(
                            b4_summary_horizon)),
                    b4_observability_metadata,
                    b4_observability_contract_version);
                tracer.jtrace->setEnergyProvider(
                    b4_energy_provider);
                b4_energy_provider->setTraceLogger(
                    tracer.jtrace.get());
                b4_energy_provider->setSemanticTraceEnabled(
                    semantic_traces);
                b4_json_trace = tracer.jtrace.get();
            } else if (!sys->kernels.empty()) {
                // Default schema2 compatibility path.
                RTSim::Scheduler *sched = sys->kernels[0]->getScheduler();
                RTSim::EnergyInfoProvider *energy_provider =
                    dynamic_cast<RTSim::EnergyInfoProvider*>(sched);
                if (energy_provider) {
                    tracer.jtrace->setEnergyProvider(energy_provider);
                    energy_provider->setTraceLogger(tracer.jtrace.get());
                    energy_provider->setSemanticTraceEnabled(semantic_traces);
                }
            }
        }
    }

    for (auto &[tasksrv, cpu, params] : taskset) {
        if (tasksrv.getServer()) {
            sys->cpus[cpu]->getKernel()->addTask(*tasksrv.getServer(), params);
            for (auto &tracer : tracers) {
                tracer.attachToTask(tasksrv.getTask());
                if (tracer.ttrace)
                    tasksrv.getServer()->setTrace(*tracer.ttrace.get());
                if (tracer.jtrace)
                    tasksrv.getServer()->setTrace(*tracer.jtrace.get());
            }
        } else {
            sys->cpus[cpu]->getKernel()->addTask(tasksrv.getTask(), params);
            for (auto &tracer : tracers)
                tracer.attachToTask(tasksrv.getTask());
        }
    }

    try {
        simulation.run(std::stoi(opts["duration"]));
    } catch (std::exception &e) {
        const auto &outcome = simulation.getLastRunOutcome();
        for (auto &tracer : tracers) {
            if (tracer.jtrace) {
                tracer.jtrace->setSimulationOutcome(
                    outcome.actual_end_time, false, "runtime_error");
            }
        }
        std::cerr << "EXCEPTION: " << e.what() << std::endl;
        std::cerr << "TERMINATING!" << std::endl;
        // Closing the tracer finalizes only the private partial.  A failed
        // run never creates or replaces the public trace path.
        tracers.clear();
        discardPartialTraces(trace_targets);
        return EXIT_FAILURE;
    }

    const auto &outcome = simulation.getLastRunOutcome();
    for (auto &tracer : tracers) {
        if (tracer.jtrace) {
            tracer.jtrace->setSimulationOutcome(
                outcome.actual_end_time,
                outcome.reached_requested_horizon,
                MetaSim::simulationCompletionReasonName(outcome.reason));
        }
    }

    resmanager->getID();

    TracePublicationContract publication_contract;
    publication_contract.expected_schema =
        b4_observability_summary
            ? RTSim::B4_OBSERVABILITY_TRACE_SCHEMA_VERSION
            : RTSim::JSONTrace::TRACE_SCHEMA_VERSION;
    publication_contract.expected_observability_contract_version =
        b4_observability_contract_version;
    try {
        if (b4_observability_summary) {
            if (!outcome.reached_requested_horizon ||
                !b4_json_trace || !b4_energy_provider) {
                throw std::runtime_error(
                    "schema3 run did not reach a complete summary horizon");
            }
            const MetaSim::Tick summary_horizon(
                static_cast<std::int64_t>(
                    b4_summary_horizon));
            b4_json_trace->finalizeObservabilitySummaries(
                summary_horizon);
            const RTSim::B4ObservabilityEnergySnapshot snapshot =
                b4_energy_provider->getB4ObservabilityEnergySnapshot(
                    b4_summary_horizon);
            if (snapshot.horizon_ms != b4_summary_horizon) {
                throw std::runtime_error(
                    "energy provider returned a mismatched summary horizon");
            }
            b4_json_trace->setObservabilityEnergySummary(
                snapshot.summary);
            b4_json_trace->sealObservabilityPayloadForSerialization();
            publication_contract.initial_energy_j =
                snapshot.initial_energy_j;
            publication_contract.capacity_j =
                snapshot.capacity_j;
            publication_contract.processor_count =
                sys->cpus.size();
        }
        // JSONTrace writes its closing metadata in the destructor.
        tracers.clear();
        if (sys->scheduler_identities.empty())
            throw std::runtime_error(
                "trace publication missing scheduler identity");
        const auto &identity = sys->scheduler_identities.front();
        publishTraces(
            trace_targets, opts["run-id"], identity.configured_scheduler,
            identity.display_name, identity.implementation_id,
            opts["duration"], opts["taskset-semantic-hash"],
            publication_contract);
        partial_trace_guard.release();
    } catch (const std::exception &error) {
        // Keep the closed private partial available to the experiment runner
        // for failure diagnostics.  Successful publication still removes it
        // atomically, and the runner copies then cleans it after recording the
        // failure.
        partial_trace_guard.release();
        std::cerr << "TRACE PUBLICATION ERROR: " << error.what() << std::endl;
        return EXIT_FAILURE;
    }

    return EXIT_SUCCESS;
}
