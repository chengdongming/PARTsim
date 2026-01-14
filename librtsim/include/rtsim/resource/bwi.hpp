#ifndef __BWI_HPP__
#define __BWI_HPP__

#include <map>
#include <string>

#include <rtsim/resource/resmanager.hpp>
#include <rtsim/scheduler/scheduler.hpp>
#include <rtsim/server.hpp>

namespace RTSim {

    class BWI : public ResManager {
        friend class Server;

    public:
        BWI(const string &n);
        ~BWI();

    protected:
        std::map<Server *, Scheduler *> schedulers;

        void addServer(Server *serv, Scheduler *sched);

        bool request(AbsRTTask *t, Resource *r, int n = 1) override;
        void release(AbsRTTask *t, Resource *r, int n = 1) override;
    };

} // namespace RTSim

#endif
