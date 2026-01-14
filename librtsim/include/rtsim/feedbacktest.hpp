#ifndef __FEEDBACKTEST_H__
#define __FEEDBACKTEST_H__

#include <rtsim/feedback.hpp>
#include <vector>

/// to be generalized
#include <rtsim/sparepot.hpp>

namespace RTSim {

    using namespace MetaSim;

    /**
       This is an abstract class that represents all feedback
       scheduling modules.
     */
    class FeedbackTestModule : public AbstractFeedbackModule {
        std::vector<int> deltas;
        int index;
        Supervisor *sp;
        SporadicServer *ss;

    public:
        FeedbackTestModule(const std::string &name);
        ~FeedbackTestModule();

        void setSupervisor(Supervisor *s, SporadicServer *s2);

        void addSample(int d);

        void notify(const Tick &exec_time) override;
        void newRun() override;
        void endRun() override;
    };
} // namespace RTSim

#endif
