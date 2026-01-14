#include <gmock/gmock.h>

#include <rtsim/abskernel.hpp>

// Forward declarations
namespace RTSim {
    class AbsRTTask;
    class CPU;
    class Scheduler;
} // namespace RTSim

namespace RTSim::Mocks {
    class KernelMock : public AbsKernel {
    public:
        void addTask(AbsRTTask &t, const string &params) {
            t.setKernel(this);
        }

    public:
        MOCK_METHOD(void, activate, (AbsRTTask *), (override));

        MOCK_METHOD(void, suspend, (AbsRTTask *), (override));

        MOCK_METHOD(void, dispatch, (), (override));

        MOCK_METHOD(void, onArrival, (AbsRTTask *), (override));

        MOCK_METHOD(void, onEnd, (AbsRTTask *), (override));

        MOCK_METHOD(CPU *, getProcessor, (const AbsRTTask *),
                    (const, override));

        MOCK_METHOD(CPU *, getOldProcessor, (const AbsRTTask *),
                    (const, override));

        MOCK_METHOD(double, getSpeed, (), (const, override));

        MOCK_METHOD(bool, isContextSwitching, (), (const, override));

        MOCK_METHOD(RTSim::Scheduler *, getScheduler, (), (const, override));
    };
} // namespace RTSim::Mocks
