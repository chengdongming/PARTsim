#include "matching_it.hpp"

#include <functional>
#include <iostream>
#include <vector>

template <class I>
void print_and_count(I range) {
    for (auto v : range) {
        std::cout << v << '\t';
    }
    std::cout << "tot: " << range.size() << std::endl;
}

int main(int argc, char *argv[]) {
    std::vector<int> vec = {
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9,
    };

    auto even = [](int v) -> bool {
        return (v & 1) == 0;
    };

    auto odd = [&even](int v) -> bool {
        return !even(v);
    };

    using vecit_type = std::vector<int>::const_iterator;
    using cond_type = std::function<bool(int)>;

    auto all_even = RTSim::MatchingIt(std::begin(vec), std::end(vec), even);
    auto all_odd = RTSim::MatchingIt(std::begin(vec), std::end(vec), odd);

    print_and_count(all_even);
    print_and_count(all_odd);

    return 0;
}
