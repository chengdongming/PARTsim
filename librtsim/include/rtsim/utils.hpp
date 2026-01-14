#ifndef _RTSIM_UTILS_HPP
#define _RTSIM_UTILS_HPP

#include <rtsim/abstask.hpp>
#include <rtsim/cbserver.hpp>
#include <rtsim/exeinstr.hpp>
#include <rtsim/task.hpp>

namespace RTSim {

    /**
        Created in order not to repeat functions in files that don't share a
       common parent. It contains some static utility functions (i.e.,
       Utils::...() ).
      */
    class Utils {
    public:
        /// Returns true if the element is in the vector
        template <typename T>
        static bool exists(T toBeFound, vector<T> objs) {
            bool found = false;
            for (T o : objs) // usare il for e l'if. T deve fare overriding di
                             // operator==() per il confronto tra oggetti
                if (o == toBeFound) {
                    found = true;
                    break;
                }
            return found;
        }

        /// Wrapper for exists()
        template <typename T>
        static bool exists(T toBeFound, vector<T> *objs) {
            if (objs == NULL)
                return false;
            return exists(toBeFound, *objs);
        }

        /// Returns the workload type of the first fixed() instrution of the
        /// task t
        static std::string getTaskWorkload(AbsRTTask *t) {
            auto task = dynamic_cast<Task *>(t);
            if (task == nullptr)
                goto server_maybe;

            for (auto &instr : task->getInstrQueue()) {
                auto exec_instr = dynamic_cast<ExecInstr *>(instr.get());
                if (exec_instr) {
                    return exec_instr->getWorkload();
                }
            }

        server_maybe:
            // For now, only the first task is supported and only for a CBS,
            // future extensions to the Server implementation may change this
            auto server = dynamic_cast<CBServer *>(t);
            if (server == nullptr)
                goto busy;

            return getTaskWorkload(server->getFirstTask());

        busy:
            // FIXME: special kind of workload
            return "busy";
        }

        /// Vector to string
        static string toString(const vector<double> vec) {
            string s = "[";
            for (double d : vec)
                s += std::to_string(d) + ", ";
            return s + "]";
        }
    };

} // namespace RTSim

#endif
