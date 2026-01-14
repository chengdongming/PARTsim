#ifndef RTSIM_MATCHING_IT
#define RTSIM_MATCHING_IT

#include <type_traits>
#include <cstddef>

namespace RTSim {
    template <class ItBegin, class ItEnd, class Cond>
    // TODO: implement either iterator concepts or tagging!
    class MatchingIt {
    private:
        using self_type = MatchingIt<ItBegin, ItEnd, Cond>;
        using iterator_type = ItBegin;
        using end_type = ItEnd;
        // using reference_type = std::add_const_t<typename ItBegin::reference>;
        // using condition_type = std::function<bool(reference_type)>;
        using condition_type = Cond;

        bool valid_iterator() {
            return _it == _end || _cond(*_it);
        }

    public:
        MatchingIt(const iterator_type &it, const end_type &end,
                   const condition_type &cond) :
            _it(it),
            _end(end),
            _cond(cond) {
            if (!valid_iterator()) {
                // Bring the iterator in a known matching state or in a state
                // that matches _end
                ++(*this);
            }
        }

        auto operator*() {
            return *_it;
        }

        self_type &operator++() {
            if (_it == _end)
                return *this;

            do {
                ++_it;
            } while (!valid_iterator());

            return *this;
        }

        self_type operator++(int) {
            self_type prev(this);
            // Invoke pre-increment operator defined above
            ++(*this);
            return prev;
        }

        bool operator!=(const self_type &oth) const {
            return !(*this == oth);
        }

        bool operator==(const self_type &oth) const {
            return _it == oth._it;
        }

        self_type begin() {
            return *this;
        }

        self_type end() {
            return self_type(_end, _end, _cond);
        }

        size_t size() {
            self_type the_end = end();
            int count = 0;
            for (self_type iter = *this; iter != the_end; ++iter) {
                ++count;
            }
            return count;
        }

    private:
        iterator_type _it;
        const iterator_type _end;
        const condition_type _cond;
    };
} // namespace RTSim

#endif // RTSIM_MATCHING_IT
