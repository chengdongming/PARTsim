#ifndef __KERNEVT_HPP__
#define __KERNEVT_HPP__

#include <metasim/event.hpp>

#include <rtsim/kernel.hpp>

namespace RTSim {

    class RTKernel;

    class KernelEvt : public MetaSim::Event {
    protected:
        RTKernel *_kernel;

    public:
        KernelEvt(const std::string &name, RTKernel *k, int p = MetaSim::Event::_DEFAULT_PRIORITY + 10) :
            MetaSim::Event(name, p) {
            _kernel = k;
        }
        void setKernel(RTKernel *k) {
            _kernel = k;
        };
        RTKernel *getKernel() {
            return _kernel;
        };

        std::string toString() const override;
    };

    class DispatchEvt : public KernelEvt {
    public:
        DispatchEvt(RTKernel *k) : KernelEvt("KernelDispatching", k) {}
        void doit() override;
    };

    class BeginDispatchEvt : public KernelEvt {
    public:
        BeginDispatchEvt(RTKernel *k) : KernelEvt("KernelBeginDispatching", k) {}
        void doit() override;
    };

    class EndDispatchEvt : public KernelEvt {
    public:
        EndDispatchEvt(RTKernel *k) : KernelEvt("KernelEndDispatching", k) {}
        void doit() override;
    };

} // namespace RTSim

#endif // __KERNEVT_HPP__
