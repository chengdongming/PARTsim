#include <set>

#include <gtest/gtest.h>

#include <rtsim/scheduler/scheduler.hpp>
#include <rtsim/task.hpp>

using MetaSim::Tick;
using RTSim::Task;
using RTSim::TaskModel;

namespace {
    class ComparatorTaskModel : public TaskModel {
        Tick _priority;

    public:
        ComparatorTaskModel(Task *task, Tick priority) :
            TaskModel(task), _priority(priority) {}

        Tick getPriority() const override {
            return _priority;
        }

        void changePriority(Tick priority) override {
            _priority = priority;
        }
    };

    using ModelSet =
        std::set<TaskModel *, TaskModel::TaskModelCmp>;
}

TEST(TaskModelComparator, DifferentPriorityUsesLowerNumericValue) {
    Task low_number(nullptr, 100);
    Task high_number(nullptr, 100);
    ComparatorTaskModel low_model(&low_number, -5);
    ComparatorTaskModel high_model(&high_number, 10);

    ModelSet queue;
    queue.insert(&high_model);
    queue.insert(&low_model);

    EXPECT_EQ(*queue.begin(), &low_model);
}

TEST(TaskModelComparator, SamePriorityUsesEarlierInsertionTime) {
    Task earlier(nullptr, 100);
    Task later(nullptr, 100);
    ComparatorTaskModel earlier_model(&earlier, 7);
    ComparatorTaskModel later_model(&later, 7);
    earlier_model.setInsertTime(3);
    later_model.setInsertTime(4);

    ModelSet queue{&earlier_model, &later_model};

    EXPECT_EQ(*queue.begin(), &earlier_model);
}

TEST(TaskModelComparator, SamePriorityAndTimeUsesLowerTaskNumber) {
    Task lower_number(nullptr, 100);
    Task higher_number(nullptr, 100);
    ComparatorTaskModel lower_model(&lower_number, 7);
    ComparatorTaskModel higher_model(&higher_number, 7);
    lower_model.setInsertTime(3);
    higher_model.setInsertTime(3);

    ModelSet queue;
    queue.insert(&higher_model);
    queue.insert(&lower_model);

    EXPECT_EQ(*queue.begin(), &lower_model);
}

TEST(TaskModelComparator, DistinctTasksAreNotSetEquivalent) {
    Task first(nullptr, 100);
    Task second(nullptr, 100);
    ComparatorTaskModel first_model(&first, 7);
    ComparatorTaskModel second_model(&second, 7);
    first_model.setInsertTime(3);
    second_model.setInsertTime(3);

    ModelSet queue;
    const auto first_insert = queue.insert(&first_model);
    const auto second_insert = queue.insert(&second_model);

    EXPECT_TRUE(first_insert.second);
    EXPECT_TRUE(second_insert.second);
    EXPECT_EQ(queue.size(), 2U);
}

TEST(TaskModelComparator, ExtractThenReinsertUsesNewInsertionTime) {
    Task first(nullptr, 100);
    Task second(nullptr, 100);
    ComparatorTaskModel first_model(&first, 7);
    ComparatorTaskModel second_model(&second, 7);
    first_model.setInsertTime(1);
    second_model.setInsertTime(2);

    ModelSet queue{&first_model, &second_model};
    ASSERT_EQ(*queue.begin(), &first_model);

    queue.erase(&first_model);
    first_model.setInsertTime(3);
    queue.insert(&first_model);

    EXPECT_EQ(*queue.begin(), &second_model);
}

TEST(TaskModelComparator, MixedSignEncodingUsesExplicitConfiguredOrder) {
    Task negative(nullptr, 100);
    Task positive(nullptr, 100);
    ComparatorTaskModel negative_model(&negative, -100);
    ComparatorTaskModel positive_model(&positive, 100);

    ModelSet lower_first{
        TaskModel::TaskModelCmp(
            TaskModel::TaskModelCmp::QueueOrder::DocumentedAscending)};
    lower_first.insert(&positive_model);
    lower_first.insert(&negative_model);
    EXPECT_EQ(*lower_first.begin(), &negative_model);

    ModelSet higher_first{
        TaskModel::TaskModelCmp(
            TaskModel::TaskModelCmp::QueueOrder::LegacyDescending)};
    higher_first.insert(&negative_model);
    higher_first.insert(&positive_model);
    EXPECT_EQ(*higher_first.begin(), &positive_model);
}
